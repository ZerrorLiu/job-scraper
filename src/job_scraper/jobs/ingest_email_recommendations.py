from __future__ import annotations

import argparse
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path

from job_scraper.adapters.sinks.notion_workflow import (
    import_processed_statuses,
    publish_daily,
)
from job_scraper.adapters.storage.sqlite_v2 import WorkspaceDatabase
from job_scraper.application.aggregation import AcceptedJob, merge_accepted_job
from job_scraper.application.process_candidate import (
    CandidateProcessingContext,
    HistoryMode,
    ProcessJobCandidate,
)
from job_scraper.cli.console import (
    display_track_label,
    format_reasons,
    log_error,
    log_job_status,
    log_line,
    reason_label,
)
from job_scraper.config import AppConfig, load_config
from job_scraper.configuration import find_profile_definition
from job_scraper.domain.policies import FilterPolicy
from job_scraper.integrations.email_recommendations import (
    EmailIngestConfig,
    EmailIngestState,
    EmailJobCandidate,
    ImapEmailClient,
    MailMessage,
    canonical_link_key,
    enrich_email_candidate_to_raw_job,
    extract_job_candidates,
    load_email_ingest_config,
)
from job_scraper.integrations.notion import NotionClient
from job_scraper.models import JobRecord, RawJobRecord, RunStats
from job_scraper.pipeline.normalize import (
    build_dedupe_key,
    normalize_candidate,
)
from job_scraper.pipeline.policy_adapter import policy_from_legacy
from job_scraper.pipeline.role_filter import text_matches_target
from job_scraper.pipeline.steps import default_pipeline
from job_scraper.registry.builtins import build_pipeline, create_builtin_registry
from job_scraper.storage.db import Database

PROJECT_ROOT = Path(__file__).resolve().parents[3]
EMAIL_SOURCE = "email"


@dataclass(slots=True)
class TrackRuntime:
    config_path: Path
    config: AppConfig
    database: Database
    notion: NotionClient
    run: RunStats
    processor: ProcessJobCandidate
    profile_id: str
    enabled_sinks: tuple[str, ...]
    accepted_jobs: dict[str, AcceptedJob] = field(default_factory=dict)
    reject_counts: Counter[str] = field(default_factory=Counter)

    @property
    def track_label(self) -> str:
        return display_track_label(self.config.project.track_label)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Ingest job recommendation emails and append matching jobs to Notion."
    )
    parser.add_argument(
        "--config",
        default=str(default_email_config_path()),
        help="Path to email ingest TOML. Defaults to config/email.toml.",
    )
    parser.add_argument(
        "--skip-notion", action="store_true", help="Do not sync accepted email jobs to Notion."
    )
    parser.add_argument(
        "--reprocess", action="store_true", help="Ignore the local processed-message state."
    )
    parser.add_argument(
        "--max-messages",
        type=int,
        help="Override the configured maximum number of emails to fetch.",
    )
    parser.add_argument(
        "--lookback-days", type=int, help="Override the configured IMAP SINCE lookback window."
    )
    parser.add_argument("--folder", help="Override the configured IMAP folder/label for this run.")
    parser.add_argument(
        "--track-config",
        action="append",
        dest="track_configs",
        help="Restrict routing to an explicit track config. Repeat for multiple tracks.",
    )
    parser.add_argument(
        "--detail-workers",
        type=int,
        default=8,
        help="Number of parallel workers for fetching job detail pages.",
    )
    parser.add_argument(
        "--skip-status-import",
        action="store_true",
        help="Skip the initial Notion-to-local application status import, but still write accepted jobs to Notion.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    email_config = load_email_ingest_config(args.config)
    email_config = apply_overrides(email_config, args.max_messages, args.lookback_days, args.folder)
    if args.track_configs:
        email_config.track_config_paths = [
            Path(value).resolve() for value in dict.fromkeys(args.track_configs)
        ]
    if not email_config.host:
        log_error("Email | Missing IMAP host in email ingest config")
        return 2
    if not email_config.username or not email_config.password:
        log_error(
            "Email | Missing mailbox credentials. Set "
            f"{email_config.username_env or 'username'} and {email_config.password_env or 'password'}."
        )
        return 2

    started_at = datetime.now(UTC)
    state = EmailIngestState.load(email_config.state_path)
    runtimes = initialize_tracks(
        email_config,
        started_at,
        skip_notion=args.skip_notion,
        skip_status_import=args.skip_status_import,
    )
    messages_to_mark: list[MailMessage] = []
    accepted_by_message: dict[str, int] = {}
    status = "completed"

    try:
        messages = ImapEmailClient(email_config).fetch_recent_messages()
        log_line(
            f"Email | Fetched {len(messages)} candidate recommendation emails | "
            f"Lookback {email_config.lookback_days}d | Max {email_config.max_messages}"
        )
        for message in messages:
            if state.is_processed(message.message_id) and not args.reprocess:
                log_line(f"Email | Skip processed | {message.subject}")
                continue
            messages_to_mark.append(message)
            accepted_by_message[message.message_id] = 0

        candidate_tasks = collect_candidate_tasks(messages_to_mark, runtimes)
        process_candidate_tasks(
            candidate_tasks,
            runtimes,
            started_at,
            accepted_by_message,
            detail_workers=args.detail_workers,
        )

        for runtime in runtimes:
            runtime.run.finished_at = datetime.now(UTC)
            runtime.database.finish_run(runtime.run, "completed")
            log_line(
                f"Email | Track {runtime.track_label} | Done | "
                f"Seen {runtime.run.jobs_seen} | Accepted {runtime.run.jobs_new + runtime.run.jobs_updated} | "
                f"New {runtime.run.jobs_new} | Updated {runtime.run.jobs_updated} | "
                f"Filtered {runtime.run.jobs_filtered} | Main filters: {format_reasons(runtime.reject_counts)}"
            )

        if not args.skip_notion:
            for runtime in runtimes:
                if "notion_daily" not in runtime.enabled_sinks or not runtime.notion.enabled():
                    continue
                publish_daily(
                    runtime.database,
                    list(runtime.accepted_jobs.values()),
                    runtime.notion,
                    runtime.config.project.timezone,
                    started_at,
                    table_prefix=runtime.config.notion.daily_table_prefix,
                    track_label=runtime.track_label,
                    logger=log_line,
                )

        for message in messages_to_mark:
            state.mark_processed(message, accepted_by_message.get(message.message_id, 0))
        state.save()
        log_line(f"Email | State updated | {email_config.state_path}")
    except Exception as exc:  # pragma: no cover
        status = "failed"
        log_error(f"Email | Failed | {exc}")
        for runtime in runtimes:
            runtime.run.finished_at = datetime.now(UTC)
            runtime.run.jobs_failed += 1
            runtime.run.errors.append(str(exc))
            runtime.database.finish_run(runtime.run, "failed")
        return 1

    return 0 if status == "completed" else 1


def default_email_config_path() -> Path:
    return PROJECT_ROOT / "config" / "email.toml"


def apply_overrides(
    config: EmailIngestConfig,
    max_messages: int | None,
    lookback_days: int | None,
    folder: str | None,
) -> EmailIngestConfig:
    if max_messages is not None:
        config.max_messages = max_messages
    if lookback_days is not None:
        config.lookback_days = lookback_days
    if folder:
        config.folder = folder
    return config


def initialize_tracks(
    email_config: EmailIngestConfig,
    started_at: datetime,
    skip_notion: bool,
    skip_status_import: bool = False,
) -> list[TrackRuntime]:
    runtimes: list[TrackRuntime] = []
    for config_path in email_config.track_config_paths:
        config = load_config(config_path)
        profile = find_profile_definition(config_path)
        if profile is not None and "email_imap" not in profile.channels:
            continue
        database = Database(config.project.database_path)
        database.initialize()
        notion = NotionClient(config.notion)
        workspace_database = (
            WorkspaceDatabase(config.project.workspace_database_path)
            if config.project.workspace_database_path is not None
            else None
        )
        if workspace_database is not None:
            workspace_database.initialize()
        pipeline = (
            build_pipeline(create_builtin_registry(), profile.pipeline)
            if profile is not None
            else default_pipeline()
        )
        if not skip_notion and not skip_status_import and notion.enabled():
            synced_statuses = import_processed_statuses(
                database,
                notion,
                table_title=f"{(config.notion.daily_table_prefix or display_track_label(config.project.track_label)).strip()} Jobs",
                error_logger=log_error,
            )
            log_line(
                f"Notion | Track {display_track_label(config.project.track_label)} | Imported processed statuses {synced_statuses}"
            )
        runtimes.append(
            TrackRuntime(
                config_path=config_path,
                config=config,
                database=database,
                notion=notion,
                run=database.create_run(EMAIL_SOURCE, started_at),
                processor=ProcessJobCandidate(
                    database,
                    pipeline,
                    normalize_candidate,
                    decision_recorder=workspace_database,
                ),
                profile_id=(
                    profile.profile_id
                    if profile is not None
                    else display_track_label(config.project.track_label)
                ),
                enabled_sinks=(profile.sinks if profile is not None else ("csv", "notion_daily")),
            )
        )
    return runtimes


def collect_candidate_tasks(
    messages: list[MailMessage], runtimes: list[TrackRuntime]
) -> list[tuple[MailMessage, EmailJobCandidate]]:
    del runtimes
    tasks: list[tuple[MailMessage, EmailJobCandidate]] = []
    seen_detail_links: set[str] = set()
    for message in messages:
        candidates = extract_job_candidates(message)
        log_line(f"Email | {message.subject} | Extracted {len(candidates)} job-like links")
        for candidate in candidates:
            link_key = canonical_link_key(candidate.url)
            if link_key in seen_detail_links:
                continue
            seen_detail_links.add(link_key)
            tasks.append((message, candidate))
    log_line(f"Email | Detail fetch queue | Unique job-like links {len(tasks)}")
    return tasks


def candidate_may_match_any_track(
    candidate: EmailJobCandidate,
    runtimes: list[TrackRuntime],
    allow_broad_platform_scan: bool = False,
) -> bool:
    context = " ".join(
        str(value or "")
        for value in [
            candidate.context,
            candidate.email_subject,
            candidate.anchor_text,
        ]
    )
    if allow_broad_platform_scan and candidate_is_from_job_platform(candidate):
        return True
    for runtime in runtimes:
        filters = runtime.config.filters
        if text_matches_target(
            candidate.title,
            context,
            filters.target_keywords,
            filters.target_match_scope,
            filters.target_rules,
        ):
            return True
    return False


def candidate_is_from_job_platform(candidate: EmailJobCandidate) -> bool:
    searchable = " ".join(
        str(value or "").lower()
        for value in [
            candidate.url,
            candidate.email_from,
            candidate.email_subject,
        ]
    )
    platform_tokens = (
        "linkedin.com/jobs",
        "indeed.com",
        "efinancialcareers",
        "glassdoor",
        "stepstone",
        "instaffo",
        "workday",
        "greenhouse",
        "successfactors",
        "softgarden",
        "join.com",
    )
    return any(token in searchable for token in platform_tokens)


def process_candidate_tasks(
    tasks: list[tuple[MailMessage, EmailJobCandidate]],
    runtimes: list[TrackRuntime],
    started_at: datetime,
    accepted_by_message: dict[str, int],
    detail_workers: int,
) -> None:
    if not tasks:
        return
    worker_count = max(1, detail_workers)
    completed = 0
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = {
            executor.submit(
                enrich_email_candidate_to_raw_job, candidate, runtimes[0].config.http, started_at
            ): message
            for message, candidate in tasks
        }
        for future in as_completed(futures):
            message = futures[future]
            completed += 1
            try:
                raw = future.result()
            except Exception as exc:
                log_error(f"Email | Detail fetch failed unexpectedly | {exc}")
                continue
            if not is_processable_detail_status(raw.raw_payload.get("detail_status")):
                reject_detail_unavailable(raw, runtimes)
            else:
                accepted_count = 0
                for runtime in runtimes:
                    accepted = process_raw_job(raw, runtime, started_at)
                    if accepted:
                        accepted_count += 1
                if accepted_count:
                    accepted_by_message[message.message_id] = (
                        accepted_by_message.get(message.message_id, 0) + accepted_count
                    )
            if completed % 25 == 0 or completed == len(tasks):
                log_line(f"Email | Detail progress {completed}/{len(tasks)}")


def process_message(
    message: MailMessage,
    runtimes: list[TrackRuntime],
    started_at: datetime,
    seen_detail_links: set[str] | None = None,
) -> int:
    seen_detail_links = seen_detail_links if seen_detail_links is not None else set()
    candidates = extract_job_candidates(message)
    log_line(f"Email | {message.subject} | Extracted {len(candidates)} job-like links")
    accepted_for_message = 0
    for candidate in candidates:
        link_key = canonical_link_key(candidate.url)
        if link_key in seen_detail_links:
            continue
        seen_detail_links.add(link_key)
        raw = enrich_email_candidate_to_raw_job(
            candidate, runtimes[0].config.http, scraped_at=started_at
        )
        if not is_processable_detail_status(raw.raw_payload.get("detail_status")):
            reject_detail_unavailable(raw, runtimes)
            continue
        for runtime in runtimes:
            accepted = process_raw_job(raw, runtime, started_at)
            if accepted:
                accepted_for_message += 1
    return accepted_for_message


def process_raw_job(
    raw: RawJobRecord,
    runtime: TrackRuntime,
    started_at: datetime,
) -> bool:
    stats = runtime.run
    stats.jobs_seen += 1
    policy = policy_from_legacy(
        runtime.config.filters,
        max_post_age_hours=0,
    )
    if str(raw.raw_payload.get("detail_status") or "") == "email_fallback":
        policy = email_fallback_policy(policy)
    normalized = normalize_candidate(raw, policy)
    normalized.dedupe_key = stable_email_dedupe_key(normalized)
    result = runtime.processor.process_normalized(
        normalized,
        CandidateProcessingContext(
            profile_id=runtime.profile_id,
            run_id=stats.run_id,
            started_at=started_at,
            policy=policy,
            history_mode=HistoryMode.PREVIOUSLY_PUBLISHED,
        ),
    )
    reason = None if result.decision.reason is None else result.decision.reason.value
    if reason:
        stats.jobs_filtered += 1
        runtime.reject_counts[reason] += 1
        log_job_status(
            EMAIL_SOURCE,
            stats.jobs_seen,
            "FAIL",
            reason_label(reason),
            normalized.title,
            normalized.company_name,
            normalized.city or "N/A",
        )
        return False

    job_id = result.job_id
    is_new = result.is_new
    merged = merge_accepted_job(runtime.accepted_jobs, normalized, job_id)
    if merged:
        stats.jobs_updated += 1
        decision = "PASS merge"
    elif is_new:
        stats.jobs_new += 1
        decision = "PASS new"
    else:
        stats.jobs_updated += 1
        decision = "PASS old"
    log_job_status(
        EMAIL_SOURCE,
        stats.jobs_seen,
        decision,
        "",
        normalized.title,
        normalized.company_name,
        normalized.city or "N/A",
    )
    return True


def email_fallback_policy(policy: FilterPolicy) -> FilterPolicy:
    """Require title-local role evidence when email context replaces job details."""

    return replace(
        policy,
        acceptance_scope="title",
        acceptance_rules=tuple(
            replace(rule, match_scope="title") for rule in policy.acceptance_rules
        ),
    )


def reject_detail_unavailable(
    raw: RawJobRecord,
    runtimes: list[TrackRuntime],
) -> None:
    for runtime in runtimes:
        runtime.run.jobs_seen += 1
        runtime.run.jobs_filtered += 1
        runtime.reject_counts["detail_unavailable"] += 1
        log_job_status(
            EMAIL_SOURCE,
            runtime.run.jobs_seen,
            "FAIL",
            reason_label("detail_unavailable"),
            raw.title,
            raw.company_name,
            raw.location_raw or "N/A",
        )


def is_processable_detail_status(value: object) -> bool:
    return str(value or "") in {"ok", "email_fallback"}


def stable_email_dedupe_key(job: JobRecord) -> str:
    payload = job.raw_payload or {}
    return build_dedupe_key(
        str(payload.get("email_candidate_title") or job.title),
        str(payload.get("email_candidate_company") or job.company_name),
        str(payload.get("email_candidate_location") or job.location_raw),
        job.source_job_id,
        description="",
        source=job.source,
    )


if __name__ == "__main__":
    raise SystemExit(main())
