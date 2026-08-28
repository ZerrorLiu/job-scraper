from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path

from job_scraper.adapters.sinks.notion_workflow import (
    import_processed_statuses,
    publish_daily,
)
from job_scraper.adapters.storage.notion_bindings import NotionDatabaseBindingStore
from job_scraper.adapters.storage.sqlite_v2 import StoredJobDetail, WorkspaceDatabase
from job_scraper.application.aggregation import AcceptedJob, merge_accepted_job
from job_scraper.application.process_candidate import (
    CandidateProcessingContext,
    ProcessJobCandidate,
)
from job_scraper.cli.console import (
    LiveRunTable,
    display_track_label,
    format_reasons,
    log_error,
    log_job_status,
    log_line,
    reason_label,
)
from job_scraper.collectors.data_integration_adapter import (
    BrightDataBatchResult,
    BrightDataDetailResolutionResult,
    execute_resilient_brightdata_detail_batches,
    resolve_indeed_market,
)
from job_scraper.config import AppConfig, load_config
from job_scraper.configuration import find_profile_definition
from job_scraper.configuration.composition import apply_profile_to_runtime
from job_scraper.configuration.policy import policy_from_legacy
from job_scraper.domain.models import JobRecord, RawJobRecord, RunStats
from job_scraper.domain.policies import FilterPolicy, title_scoped
from job_scraper.integrations.browser_details import (
    BrowserDetailContractError,
    BrowserDetailResult,
    BrowserDetailTask,
    BrowserSearchResult,
    BrowserSearchTask,
    queue_status,
    search_queue_status,
    search_task_from_mapping,
    task_from_mapping,
)
from job_scraper.integrations.email_recommendations import (
    EmailIngestConfig,
    EmailIngestState,
    EmailJobCandidate,
    ImapEmailClient,
    MailMessage,
    can_use_email_fallback,
    canonical_link_key,
    email_candidate_to_raw_job,
    enrich_email_candidate_to_raw_job,
    extract_job_candidates,
    indeed_detail_url,
    is_efinancialcareers_url,
    load_email_ingest_config,
    platform_job_reference,
)
from job_scraper.integrations.notion import NotionClient
from job_scraper.pipeline.normalize import (
    build_dedupe_key,
    country_to_code,
    normalize_candidate,
)
from job_scraper.pipeline.steps import default_pipeline
from job_scraper.registry.builtins import build_pipeline, create_builtin_registry
from job_scraper.storage.db import Database

PROJECT_ROOT = Path(__file__).resolve().parents[3]
EMAIL_SOURCE = "email"
BRIGHTDATA_EMAIL_DETAIL_SNAPSHOT_TIMEOUT_SECONDS = 120.0
BRIGHTDATA_EMAIL_DETAIL_TOTAL_TIMEOUT_SECONDS = 300.0


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
    notion_binding_store: NotionDatabaseBindingStore | None = None
    workspace_database: WorkspaceDatabase | None = None
    platform_country_scope: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    accepted_jobs: dict[str, AcceptedJob] = field(default_factory=dict)
    reject_counts: Counter[str] = field(default_factory=Counter)

    @property
    def track_label(self) -> str:
        return display_track_label(self.config.project.track_label)


@dataclass(slots=True)
class EmailPreparation:
    args: argparse.Namespace
    email_config: EmailIngestConfig
    started_at: datetime
    state: EmailIngestState
    runtimes: list[TrackRuntime]
    messages_to_mark: list[MailMessage]
    accepted_by_message: dict[str, int]
    candidate_tasks: list[tuple[MailMessage, EmailJobCandidate]]
    prepared_details: dict[str, RawJobRecord]
    fetched_messages: int
    skipped_messages: int


@dataclass(frozen=True, slots=True)
class EmailPrepareRequest:
    config_path: Path
    track_config_paths: tuple[Path, ...]
    lookback_days: int | None
    max_messages: int | None
    detail_workers: int
    folder: str | None = None
    skip_notion: bool = False
    skip_status_import: bool = True


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
        help="Skip the initial Notion-to-local manual job-decision import, but still write accepted jobs to Notion.",
    )
    browser_mode = parser.add_mutually_exclusive_group()
    browser_mode.add_argument(
        "--browser-queue",
        type=Path,
        help="Write or refresh a local, single-lane Indeed browser-detail queue without publishing jobs.",
    )
    browser_mode.add_argument(
        "--browser-claim",
        type=Path,
        help="Lease one pending item from a local browser-detail queue and print it as JSON.",
    )
    browser_mode.add_argument(
        "--browser-results",
        type=Path,
        help="Import complete rows from a local browser-detail queue through the email pipeline.",
    )
    browser_mode.add_argument(
        "--browser-search-queue",
        type=Path,
        help="Write or refresh local, single-lane Indeed browser-search tasks from track configuration.",
    )
    browser_mode.add_argument(
        "--browser-search-claim",
        type=Path,
        help="Lease one pending local browser-search task and print it as JSON.",
    )
    browser_mode.add_argument(
        "--browser-search-results",
        type=Path,
        help="Expand complete local browser-search results into a browser-detail queue.",
    )
    parser.add_argument(
        "--browser-detail-queue",
        type=Path,
        help="Destination browser-detail queue; required with --browser-search-results.",
    )
    parser.add_argument(
        "--browser-sender",
        action="append",
        dest="browser_senders",
        help="Restrict browser queue email selection to this sender substring and ignore subject keywords. Repeatable.",
    )
    return parser.parse_args(argv)


def main(
    argv: list[str] | None = None,
    *,
    dashboard: LiveRunTable | None = None,
) -> int:
    command_args = argv or sys.argv[1:]
    args = parse_args(command_args)
    if args.browser_senders and args.browser_queue is None:
        log_error("Email | --browser-sender requires --browser-queue")
        return 2
    if args.browser_detail_queue is not None and args.browser_search_results is None:
        log_error("Email | --browser-detail-queue requires --browser-search-results")
        return 2
    if args.browser_search_results is not None and args.browser_detail_queue is None:
        log_error("Email | --browser-search-results requires --browser-detail-queue")
        return 2
    if (
        args.browser_search_results is not None
        and args.browser_detail_queue is not None
        and args.browser_search_results.resolve() == args.browser_detail_queue.resolve()
    ):
        log_error("Email | Browser search and detail queues must use different files")
        return 2
    if args.browser_queue is not None:
        return emit_browser_detail_queue(args)
    if args.browser_claim is not None:
        return claim_browser_detail_task(args.browser_claim)
    if args.browser_results is not None:
        return import_browser_detail_results(args)
    if args.browser_search_queue is not None:
        return emit_browser_search_queue(args)
    if args.browser_search_claim is not None:
        return claim_browser_search_task(args.browser_search_claim)
    if args.browser_search_results is not None:
        return expand_browser_search_results(args)
    try:
        preparation = prepare(command_args, dashboard=dashboard)
    except Exception as exc:  # pragma: no cover
        log_error(f"Email | Failed | {exc}")
        return 1
    return finish(preparation, dashboard=dashboard)


def emit_browser_detail_queue(args: argparse.Namespace) -> int:
    """Emit local browser work without starting runs, detail fetches, or publishing."""

    email_config = load_email_ingest_config(args.config)
    email_config = apply_overrides(email_config, args.max_messages, args.lookback_days, args.folder)
    if args.browser_senders:
        email_config.sender_allowlist = list(dict.fromkeys(args.browser_senders))
        email_config.subject_keywords = []
    if not email_config.host:
        raise ValueError("Missing IMAP host in email ingest config")
    if not email_config.username or not email_config.password:
        raise ValueError(
            "Missing mailbox credentials. Set "
            f"{email_config.username_env or 'username'} and {email_config.password_env or 'password'}."
        )
    state = EmailIngestState.load(email_config.state_path)
    messages = ImapEmailClient(email_config).fetch_recent_messages()
    pending_messages = [
        message
        for message in messages
        if args.reprocess or not state.is_processed(message.message_id)
    ]
    tasks = collect_candidate_tasks(pending_messages, [], email_config.skipped_link_hosts)
    current = _read_browser_queue(args.browser_queue)
    queue = merge_browser_detail_queue((candidate for _message, candidate in tasks), current)
    _validate_single_browser_lease(queue)
    _write_browser_queue(args.browser_queue, queue)
    state_counts = Counter(str(row["status"]) for row in queue)
    log_line(
        f"Email | Browser detail queue | {len(queue)} Indeed URLs | "
        f"Pending {state_counts['pending']} | In progress {state_counts['in_progress']} | "
        f"Complete {state_counts['complete']} | Imported {state_counts['imported']}"
    )
    return 0


def browser_email_tasks(email_config: EmailIngestConfig) -> tuple[BrowserDetailTask, ...]:
    """Read authorized recommendation mail and return only Indeed detail work.

    This read-only helper intentionally ignores processed-message state: queue
    task IDs deduplicate postings, while every Indeed card still receives the
    required browser-detail opportunity even if another email path ran first.
    """

    if not email_config.host:
        raise ValueError("Missing IMAP host in email ingest config")
    if not email_config.username or not email_config.password:
        raise ValueError("Missing mailbox credentials for browser email refresh")
    messages = ImapEmailClient(email_config).fetch_recent_messages()
    candidates = collect_candidate_tasks(messages, [], email_config.skipped_link_hosts)
    tasks: dict[str, BrowserDetailTask] = {}
    for _message, candidate in candidates:
        if not indeed_detail_url(candidate.url):
            continue
        task = BrowserDetailTask.from_candidate(candidate)
        tasks.setdefault(task.task_id, task)
    return tuple(tasks.values())


def merge_browser_detail_queue(
    candidates: Iterable[EmailJobCandidate],
    current: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    """Keep one durable task per canonical Indeed job while preserving its checkpoint."""

    return merge_browser_detail_tasks(
        (BrowserDetailTask.from_candidate(candidate) for candidate in candidates), current
    )


def merge_browser_detail_tasks(
    tasks: Iterable[BrowserDetailTask],
    current: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    """Keep one durable task per canonical Indeed job while preserving its checkpoint."""

    prior_by_id: dict[str, dict[str, object]] = {}
    for row in current:
        task_id = task_from_mapping(row).task_id
        previous = prior_by_id.get(task_id)
        if previous is None or browser_queue_state_rank(row) > browser_queue_state_rank(previous):
            prior_by_id[task_id] = dict(row)
    queue = list(prior_by_id.values())
    seen_task_ids: set[str] = set()
    for task in tasks:
        if task.task_id in prior_by_id:
            continue
        if task.task_id in seen_task_ids:
            continue
        seen_task_ids.add(task.task_id)
        queue.append(task.to_dict())
    return queue


def emit_browser_search_queue(args: argparse.Namespace) -> int:
    """Emit browser-visible search work without reaching Indeed or a browser."""

    email_config = load_email_ingest_config(args.config)
    if args.track_configs:
        email_config.track_config_paths = [
            Path(value).resolve() for value in dict.fromkeys(args.track_configs)
        ]
    created_at = datetime.now(UTC)
    tasks = browser_search_tasks(email_config.track_config_paths, created_at)
    current = _read_browser_search_queue(args.browser_search_queue)
    queue = merge_browser_search_queue(tasks, current)
    _validate_single_browser_search_lease(queue)
    _write_browser_search_queue(args.browser_search_queue, queue)
    state_counts = Counter(str(row["status"]) for row in queue)
    log_line(
        f"Email | Browser search queue | {len(queue)} Indeed searches | "
        f"Pending {state_counts['pending']} | In progress {state_counts['in_progress']} | "
        f"Complete {state_counts['complete']} | Expanded {state_counts['expanded']}"
    )
    return 0


def browser_search_tasks(
    track_config_paths: Iterable[Path], created_at: datetime
) -> list[BrowserSearchTask]:
    tasks: list[BrowserSearchTask] = []
    seen_task_ids: set[str] = set()
    for config_path in track_config_paths:
        config = load_config(config_path)
        profile = find_profile_definition(config_path)
        if profile is not None:
            apply_profile_to_runtime(profile, config)
        indeed_settings = config.sources.get("indeed_brightdata")
        settings = indeed_settings
        if settings is None or not settings.search_queries or not settings.locations:
            settings = next(
                (
                    candidate
                    for candidate in config.sources.values()
                    if candidate.search_queries and candidate.locations
                ),
                None,
            )
        if settings is None:
            continue
        domain = browser_search_domain(
            indeed_settings.options if indeed_settings is not None else settings.options,
            config.filters.country,
        )
        for location in settings.locations:
            for query in settings.search_queries:
                task = BrowserSearchTask.create(
                    domain=domain,
                    query=query,
                    location=location,
                    created_at=created_at,
                )
                if task.task_id not in seen_task_ids:
                    seen_task_ids.add(task.task_id)
                    tasks.append(task)
    return tasks


def browser_search_domain(options: Mapping[str, object], geographic_zone: str) -> str:
    """Resolve the storefront with the same market policy as direct Indeed collection."""

    _country, domain = resolve_indeed_market(
        geographic_zone,
        country=str(options.get("country") or "") or country_to_code(geographic_zone) or None,
        domain=str(options.get("domain") or "") or None,
    )
    return domain


def merge_browser_search_queue(
    tasks: Iterable[BrowserSearchTask], current: Sequence[Mapping[str, object]]
) -> list[dict[str, object]]:
    prior_by_id: dict[str, dict[str, object]] = {}
    for row in current:
        task_id = search_task_from_mapping(row).task_id
        previous = prior_by_id.get(task_id)
        if previous is None or browser_search_state_rank(row) > browser_search_state_rank(previous):
            prior_by_id[task_id] = dict(row)
    return [prior_by_id.get(task.task_id, task.to_dict()) for task in tasks]


def browser_search_state_rank(row: Mapping[str, object]) -> int:
    return {
        "pending": 0,
        "blocked": 1,
        "unavailable": 1,
        "in_progress": 2,
        "complete": 3,
        "expanded": 4,
    }.get(str(row.get("status") or ""), -1)


def claim_browser_search_task(path: Path) -> int:
    queue = _read_browser_search_queue(path)
    _validate_single_browser_search_lease(queue)
    if any(str(row["status"]) == "in_progress" for row in queue):
        log_line("Email | Browser search queue | A task is already in progress")
        return 2
    for row in queue:
        if str(row["status"]) != "pending":
            continue
        row["status"] = "in_progress"
        row["lease_started_at"] = datetime.now(UTC).isoformat()
        _write_browser_search_queue(path, queue)
        print(json.dumps(row, ensure_ascii=True, sort_keys=True))
        return 0
    log_line("Email | Browser search queue | No pending task")
    return 0


def expand_browser_search_results(args: argparse.Namespace) -> int:
    if args.browser_detail_queue is None:
        raise ValueError("--browser-search-results requires --browser-detail-queue")
    if args.browser_search_results.resolve() == args.browser_detail_queue.resolve():
        raise ValueError("Browser search and detail queues must use different files")
    search_queue = _read_browser_search_queue(args.browser_search_results)
    completed: list[tuple[int, BrowserSearchResult]] = []
    for index, row in enumerate(search_queue):
        if search_queue_status(row) == "complete":
            completed.append((index, BrowserSearchResult.from_mapping(row)))
    if not completed:
        log_line("Email | Browser search expansion | No complete search rows")
        return 0
    detail_queue = _read_browser_queue(args.browser_detail_queue)
    detail_tasks = (
        BrowserDetailTask.from_search_card(card)
        for _index, result in completed
        for card in result.cards
    )
    merged_detail_queue = merge_browser_detail_tasks(detail_tasks, detail_queue)
    for index, _result in completed:
        search_queue[index]["status"] = "expanded"
        search_queue[index].pop("lease_started_at", None)
    _write_browser_queue(args.browser_detail_queue, merged_detail_queue)
    _write_browser_search_queue(args.browser_search_results, search_queue)
    log_line(
        f"Email | Browser search expansion | Expanded {len(completed)} searches | "
        f"Detail queue {len(merged_detail_queue)} URLs"
    )
    return 0


def browser_queue_state_rank(row: Mapping[str, object]) -> int:
    return {
        "pending": 0,
        "blocked": 1,
        "unavailable": 1,
        "in_progress": 2,
        "complete": 3,
        "imported": 4,
    }.get(str(row.get("status") or ""), -1)


def claim_browser_detail_task(path: Path) -> int:
    """Lease exactly one queue item; browser navigation remains outside this package."""

    queue = _read_browser_queue(path)
    _validate_single_browser_lease(queue)
    if any(str(row["status"]) == "in_progress" for row in queue):
        log_line("Email | Browser detail queue | A task is already in progress")
        return 2
    for row in queue:
        if str(row["status"]) != "pending":
            continue
        row["status"] = "in_progress"
        row["lease_started_at"] = datetime.now(UTC).isoformat()
        _write_browser_queue(path, queue)
        # Windows console encodings can be narrower than UTF-8 while email
        # context is arbitrary Unicode.  The queue file remains UTF-8; CLI
        # handoff is escaped JSON so a successful lease never looks failed.
        print(json.dumps(row, ensure_ascii=True, sort_keys=True))
        return 0
    log_line("Email | Browser detail queue | No pending task")
    return 0


def import_browser_detail_results(args: argparse.Namespace) -> int:
    """Import complete browser rows without contacting IMAP or a browser provider."""

    queue = _read_browser_queue(args.browser_results)
    completed: list[tuple[int, BrowserDetailResult]] = []
    seen_task_ids: set[str] = set()
    for index, row in enumerate(queue):
        task = task_from_mapping(row)
        if task.task_id in seen_task_ids:
            raise BrowserDetailContractError(
                f"Browser queue contains duplicate task_id {task.task_id}"
            )
        seen_task_ids.add(task.task_id)
        status = queue_status(row)
        if status == "complete":
            completed.append((index, BrowserDetailResult.from_mapping(row)))
    if not completed:
        log_line("Email | Browser detail import | No complete queue rows")
        return 0

    email_config = load_email_ingest_config(args.config)
    if args.track_configs:
        email_config.track_config_paths = [
            Path(value).resolve() for value in dict.fromkeys(args.track_configs)
        ]
    import_browser_detail_batch(
        [result for _index, result in completed],
        email_config,
        skip_notion=args.skip_notion,
        skip_status_import=args.skip_status_import,
    )

    for index, _result in completed:
        queue[index]["status"] = "imported"
        queue[index].pop("lease_started_at", None)
    _write_browser_queue(args.browser_results, queue)
    log_line(f"Email | Browser detail import | Imported {len(completed)} complete queue rows")
    return 0


def import_browser_detail_batch(
    results: Sequence[BrowserDetailResult],
    email_config: EmailIngestConfig,
    *,
    skip_notion: bool,
    skip_status_import: bool = False,
) -> list[TrackRuntime]:
    """Run already-validated browser detail results through the normal pipeline.

    Shared by the local JSONL CLI flow (`import_browser_detail_results`) and
    the networked task-server flow, so both entry paths run exactly the same
    normalization, filtering, persistence, and publication.
    """

    started_at = datetime.now(UTC)
    runtimes = initialize_tracks(
        email_config,
        started_at,
        skip_notion=skip_notion,
        skip_status_import=skip_status_import,
    )
    if not runtimes:
        raise ValueError("No enabled email tracks are available for browser detail import")
    try:
        for result in results:
            raw = raw_from_browser_detail(result, started_at)
            for runtime in runtimes:
                process_raw_job(raw, runtime, started_at)
        for runtime in runtimes:
            runtime.run.finished_at = datetime.now(UTC)
            runtime.database.finish_run(runtime.run, "completed")
        _publish_browser_imports(runtimes, started_at, skip_notion=skip_notion)
    except Exception as exc:
        _mark_email_runs_failed(runtimes, exc, dashboard=None)
        raise
    return runtimes


def raw_from_browser_detail(result: BrowserDetailResult, scraped_at: datetime) -> RawJobRecord:
    if result.task.origin == "browser_search":
        source_id, source_job_id = platform_job_reference(result.task.url)
        if source_id != "indeed" or not source_job_id:
            raise BrowserDetailContractError(
                "Browser search detail is missing an Indeed job reference"
            )
        return RawJobRecord(
            source="indeed",
            source_job_id=source_job_id,
            source_url=result.task.url,
            canonical_url=result.task.url,
            title=result.title,
            company_name=result.company_name,
            location_raw=result.location_raw,
            posted_at_text=result.task.observed_at.isoformat(),
            scraped_at=scraped_at,
            job_description=result.description,
            raw_payload={
                "freshness_basis": "browser_observed",
                "acquisition_mode": "authorised_browser_search",
                "source_platforms": ["indeed"],
                "detail_status": "ok",
                "detail_source": "authorised_browser",
                "description_source": "authorised_browser",
                "title_source": "authorised_browser",
                "browser_detail_task_id": result.task.task_id,
                "browser_search_task_id": result.task.search_task_id,
                "browser_search_query": result.task.search_query,
                "browser_search_location": result.task.search_location,
                "matched_source_id": "indeed",
                "matched_source_job_id": source_job_id,
            },
        )
    raw = email_candidate_to_raw_job(result.task.to_candidate(), scraped_at=scraped_at)
    raw.title = result.title
    raw.company_name = result.company_name
    raw.location_raw = result.location_raw
    raw.job_description = result.description
    raw.canonical_url = result.task.url
    raw.raw_payload.update(
        {
            "detail_status": "ok",
            "detail_source": "authorised_browser",
            "description_source": "authorised_browser",
            "title_source": "authorised_browser",
            "browser_detail_task_id": result.task.task_id,
            "matched_source_id": "indeed",
            "matched_source_job_id": platform_job_reference(result.task.url)[1],
        }
    )
    return raw


def _publish_browser_imports(
    runtimes: Sequence[TrackRuntime],
    started_at: datetime,
    *,
    skip_notion: bool,
) -> None:
    if skip_notion:
        return
    for runtime in runtimes:
        if "notion_daily" not in runtime.enabled_sinks or not runtime.notion.enabled():
            continue
        result = publish_daily(
            runtime.database,
            list(runtime.accepted_jobs.values()),
            runtime.notion,
            runtime.config.project.timezone,
            started_at,
            table_prefix=runtime.config.notion.daily_table_prefix,
            track_label=runtime.track_label,
            profile_id=runtime.profile_id,
            binding_store=runtime.notion_binding_store,
            logger=log_line,
        )
        if result.errors:
            for error in result.errors:
                log_error(f"Email | Track {runtime.track_label} | Notion | Failed | {error}")
            raise RuntimeError(
                f"Notion publication failed for track {runtime.track_label}: "
                + "; ".join(result.errors)
            )


def _read_browser_queue(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    rows: list[dict[str, object]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise BrowserDetailContractError(
                f"Browser queue {path} line {line_number} is not JSON"
            ) from exc
        if not isinstance(value, dict):
            raise BrowserDetailContractError(
                f"Browser queue {path} line {line_number} must be a JSON object"
            )
        task_from_mapping(value)
        queue_status(value)
        rows.append(value)
    return rows


def _read_browser_search_queue(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    rows: list[dict[str, object]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise BrowserDetailContractError(
                f"Browser search queue {path} line {line_number} is not JSON"
            ) from exc
        if not isinstance(value, dict):
            raise BrowserDetailContractError(
                f"Browser search queue {path} line {line_number} must be a JSON object"
            )
        search_task_from_mapping(value)
        search_queue_status(value)
        rows.append(value)
    return rows


def _write_browser_queue(path: Path, rows: Sequence[Mapping[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows)
    path.write_text(content, encoding="utf-8")


def _write_browser_search_queue(path: Path, rows: Sequence[Mapping[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows)
    path.write_text(content, encoding="utf-8")


def _validate_single_browser_lease(rows: Sequence[Mapping[str, object]]) -> None:
    leases = sum(1 for row in rows if str(row.get("status") or "") == "in_progress")
    if leases > 1:
        raise BrowserDetailContractError("Browser detail queue permits only one in_progress task")


def _validate_single_browser_search_lease(rows: Sequence[Mapping[str, object]]) -> None:
    leases = sum(1 for row in rows if str(row.get("status") or "") == "in_progress")
    if leases > 1:
        raise BrowserDetailContractError("Browser search queue permits only one in_progress task")


def prepare(
    argv: list[str],
    *,
    dashboard: LiveRunTable | None = None,
) -> EmailPreparation:
    """Fetch and resolve email cards without publishing or mutating message state."""

    return prepare_request(_prepare_request_from_args(parse_args(argv)), dashboard=dashboard)


def prepare_request(
    request: EmailPrepareRequest,
    *,
    dashboard: LiveRunTable | None = None,
) -> EmailPreparation:
    """Prepare email acquisition from a typed orchestration boundary."""

    args = argparse.Namespace(
        config=str(request.config_path),
        track_configs=[str(path) for path in request.track_config_paths],
        lookback_days=request.lookback_days,
        max_messages=request.max_messages,
        detail_workers=request.detail_workers,
        folder=request.folder,
        skip_notion=request.skip_notion,
        skip_status_import=request.skip_status_import,
        reprocess=False,
    )
    email_config = load_email_ingest_config(args.config)
    email_config = apply_overrides(email_config, args.max_messages, args.lookback_days, args.folder)
    if args.track_configs:
        email_config.track_config_paths = [
            Path(value).resolve() for value in dict.fromkeys(args.track_configs)
        ]
    if not email_config.host:
        raise ValueError("Missing IMAP host in email ingest config")
    if not email_config.username or not email_config.password:
        raise ValueError(
            "Missing mailbox credentials. Set "
            f"{email_config.username_env or 'username'} and {email_config.password_env or 'password'}."
        )

    started_at = datetime.now(UTC)
    state = EmailIngestState.load(email_config.state_path)
    runtimes = initialize_tracks(
        email_config,
        started_at,
        skip_notion=args.skip_notion,
        skip_status_import=args.skip_status_import,
    )
    for runtime in runtimes:
        if dashboard is not None:
            dashboard.update(
                runtime.track_label,
                "Email",
                stage="Fetching",
                progress_text="Mailbox",
                detail="Reading mailbox",
            )
    try:
        return _prepare_with_runtimes(
            args,
            email_config,
            started_at,
            state,
            runtimes,
            dashboard,
        )
    except Exception as exc:
        _mark_email_runs_failed(runtimes, exc, dashboard)
        raise


def _prepare_request_from_args(args: argparse.Namespace) -> EmailPrepareRequest:
    return EmailPrepareRequest(
        config_path=Path(args.config).resolve(),
        track_config_paths=tuple(Path(value).resolve() for value in args.track_configs or ()),
        lookback_days=args.lookback_days,
        max_messages=args.max_messages,
        detail_workers=args.detail_workers,
        folder=args.folder,
        skip_notion=args.skip_notion,
        skip_status_import=args.skip_status_import,
    )


def _prepare_with_runtimes(
    args: argparse.Namespace,
    email_config: EmailIngestConfig,
    started_at: datetime,
    state: EmailIngestState,
    runtimes: list[TrackRuntime],
    dashboard: LiveRunTable | None,
) -> EmailPreparation:
    messages_to_mark: list[MailMessage] = []
    accepted_by_message: dict[str, int] = {}
    messages = ImapEmailClient(
        email_config,
        timeout_seconds=min(runtime.config.http.timeout_seconds for runtime in runtimes),
    ).fetch_recent_messages()
    log_line(
        f"Email | Fetched {len(messages)} candidate recommendation emails | "
        f"Lookback {email_config.lookback_days}d | Max {email_config.max_messages}"
    )
    skipped_messages = 0
    for message in messages:
        if state.is_processed(message.message_id) and not args.reprocess:
            skipped_messages += 1
            log_line(f"Email | Skip processed | {message.subject}")
            continue
        messages_to_mark.append(message)
        accepted_by_message[message.message_id] = 0

    candidate_tasks = collect_candidate_tasks(
        messages_to_mark,
        runtimes,
        email_config.skipped_link_hosts,
    )
    for runtime in runtimes:
        if dashboard is not None:
            dashboard.update(
                runtime.track_label,
                "Email",
                stage="Resolving",
                keywords_done=0,
                keywords_total=len(candidate_tasks),
                progress_text=(f"0/{len(candidate_tasks)}" if candidate_tasks else "0 cards"),
                detail=f"{len(messages)} mail, {skipped_messages} old",
            )
    prepared_details = prepare_email_details(
        candidate_tasks,
        runtimes,
        started_at,
        dashboard=dashboard,
    )
    return EmailPreparation(
        args=args,
        email_config=email_config,
        started_at=started_at,
        state=state,
        runtimes=runtimes,
        messages_to_mark=messages_to_mark,
        accepted_by_message=accepted_by_message,
        candidate_tasks=candidate_tasks,
        prepared_details=prepared_details,
        fetched_messages=len(messages),
        skipped_messages=skipped_messages,
    )


def _mark_email_runs_failed(
    runtimes: list[TrackRuntime],
    exc: Exception,
    dashboard: LiveRunTable | None,
) -> None:
    for runtime in runtimes:
        runtime.run.finished_at = datetime.now(UTC)
        runtime.run.jobs_failed += 1
        runtime.run.errors.append(str(exc))
        runtime.database.finish_run(runtime.run, "failed")
        if dashboard is not None:
            dashboard.update(
                runtime.track_label,
                "Email",
                stage="Failed",
                progress_text="Failed",
                detail=str(exc),
            )


def finish(
    preparation: EmailPreparation,
    *,
    dashboard: LiveRunTable | None = None,
) -> int:
    """Filter, persist and publish a previously prepared email batch."""

    args = preparation.args
    runtimes = preparation.runtimes
    started_at = preparation.started_at
    status = "completed"
    had_track_failure = False
    try:
        for runtime in runtimes:
            if dashboard is not None:
                dashboard.update(
                    runtime.track_label,
                    "Email",
                    stage="Filtering",
                    keywords_done=0,
                    keywords_total=len(preparation.candidate_tasks),
                    progress_text=(
                        f"0/{len(preparation.candidate_tasks)}"
                        if preparation.candidate_tasks
                        else "0 cards"
                    ),
                    detail=(
                        f"{preparation.fetched_messages} mail, {preparation.skipped_messages} old"
                    ),
                )
        process_candidate_tasks(
            preparation.candidate_tasks,
            runtimes,
            started_at,
            preparation.accepted_by_message,
            detail_workers=args.detail_workers,
            dashboard=dashboard,
            prepared=preparation.prepared_details,
        )

        for runtime in runtimes:
            runtime.run.finished_at = datetime.now(UTC)
            runtime.database.finish_run(runtime.run, "completed")
            if dashboard is not None:
                dashboard.update(
                    runtime.track_label,
                    "Email",
                    stage="Done",
                    progress_text=(
                        f"{len(preparation.candidate_tasks)} cards"
                        if preparation.candidate_tasks
                        else "0 cards"
                    ),
                    seen=runtime.run.jobs_seen,
                    accepted=runtime.run.jobs_new + runtime.run.jobs_updated,
                    filtered=runtime.run.jobs_filtered,
                    detail=(
                        format_reasons(runtime.reject_counts)
                        if preparation.candidate_tasks
                        else (
                            f"{preparation.fetched_messages} mail, "
                            f"{preparation.skipped_messages} old"
                        )
                    ),
                )
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
                try:
                    result = publish_daily(
                        runtime.database,
                        list(runtime.accepted_jobs.values()),
                        runtime.notion,
                        runtime.config.project.timezone,
                        started_at,
                        table_prefix=runtime.config.notion.daily_table_prefix,
                        track_label=runtime.track_label,
                        profile_id=runtime.profile_id,
                        binding_store=runtime.notion_binding_store,
                        logger=log_line,
                    )
                except Exception as track_exc:
                    # A track-level publish failure (auth, database access,
                    # ...) marks only this track's run as failed. It must
                    # not block reaching state.mark_processed/save() below
                    # for messages this track and other, unrelated tracks
                    # already finished processing.
                    had_track_failure = True
                    runtime.run.errors.append(str(track_exc))
                    runtime.database.finish_run(runtime.run, "failed")
                    log_error(
                        f"Email | Track {runtime.track_label} | Notion publish failed | {track_exc}"
                    )
                    if dashboard is not None:
                        dashboard.update(
                            runtime.track_label,
                            "Email",
                            stage="Failed",
                            progress_text="Notion failed",
                            detail=str(track_exc),
                        )
                    continue
                if result.errors:
                    log_error(
                        f"Email | Track {runtime.track_label} | Notion | "
                        f"{result.published} synced, {len(result.errors)} failed"
                    )
                    for error in result.errors:
                        log_error(
                            f"Email | Track {runtime.track_label} | Notion | Failed | {error}"
                        )

        for message in preparation.messages_to_mark:
            preparation.state.mark_processed(
                message,
                preparation.accepted_by_message.get(message.message_id, 0),
            )
        preparation.state.save()
        log_line(f"Email | State updated | {preparation.email_config.state_path}")
    except Exception as exc:  # pragma: no cover
        status = "failed"
        log_error(f"Email | Failed | {exc}")
        for runtime in runtimes:
            runtime.run.finished_at = datetime.now(UTC)
            runtime.run.jobs_failed += 1
            runtime.run.errors.append(str(exc))
            runtime.database.finish_run(runtime.run, "failed")
            if dashboard is not None:
                dashboard.update(
                    runtime.track_label,
                    "Email",
                    stage="Failed",
                    progress_text="Failed",
                    seen=runtime.run.jobs_seen,
                    accepted=runtime.run.jobs_new + runtime.run.jobs_updated,
                    filtered=runtime.run.jobs_filtered,
                    detail=str(exc),
                )
        return 1

    return 1 if status != "completed" or had_track_failure else 0


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
        profile_id = (
            profile.profile_id
            if profile is not None
            else display_track_label(config.project.track_label)
        )
        notion = NotionClient(config.notion)
        notion_binding_store = NotionDatabaseBindingStore(
            config.project.database_path.parent / "notion_database_bindings.json"
        )
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
                binding_store=notion_binding_store,
                profile_id=profile_id,
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
                profile_id=profile_id,
                enabled_sinks=(profile.sinks if profile is not None else ("csv", "notion_daily")),
                notion_binding_store=notion_binding_store,
                workspace_database=workspace_database,
                platform_country_scope=email_config.platform_country_scope,
            )
        )
    return runtimes


def collect_candidate_tasks(
    messages: list[MailMessage],
    runtimes: list[TrackRuntime],
    skipped_link_hosts: Sequence[str] = (),
) -> list[tuple[MailMessage, EmailJobCandidate]]:
    del runtimes
    tasks: list[tuple[MailMessage, EmailJobCandidate]] = []
    seen_detail_links: set[str] = set()
    for message in messages:
        candidates = extract_job_candidates(message, skipped_link_hosts)
        log_line(f"Email | {message.subject} | Extracted {len(candidates)} job-like links")
        for candidate in candidates:
            link_key = canonical_link_key(candidate.url)
            if link_key in seen_detail_links:
                continue
            seen_detail_links.add(link_key)
            tasks.append((message, candidate))
    log_line(f"Email | Detail fetch queue | Unique job-like links {len(tasks)}")
    return tasks


def process_candidate_tasks(
    tasks: list[tuple[MailMessage, EmailJobCandidate]],
    runtimes: list[TrackRuntime],
    started_at: datetime,
    accepted_by_message: dict[str, int],
    detail_workers: int,
    dashboard: LiveRunTable | None = None,
    prepared: dict[str, RawJobRecord] | None = None,
) -> None:
    if not tasks:
        return
    prepared_details = (
        prepared if prepared is not None else prepare_email_details(tasks, runtimes, started_at)
    )
    worker_count = max(1, detail_workers)
    completed = 0
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = {
            executor.submit(
                resolve_prepared_or_fetch,
                candidate,
                runtimes,
                started_at,
                prepared_details,
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
            if dashboard is not None:
                for runtime in runtimes:
                    dashboard.update(
                        runtime.track_label,
                        "Email",
                        stage="Filtering",
                        keywords_done=completed,
                        keywords_total=len(tasks),
                        progress_text=f"{completed}/{len(tasks)}",
                        seen=runtime.run.jobs_seen,
                        accepted=runtime.run.jobs_new + runtime.run.jobs_updated,
                        filtered=runtime.run.jobs_filtered,
                        detail=f"Card {completed}/{len(tasks)}",
                    )
            if completed % 25 == 0 or completed == len(tasks):
                log_line(f"Email | Detail progress {completed}/{len(tasks)}")


def prepare_email_details(
    tasks: list[tuple[MailMessage, EmailJobCandidate]],
    runtimes: list[TrackRuntime],
    scraped_at: datetime,
    *,
    dashboard: LiveRunTable | None = None,
) -> dict[str, RawJobRecord]:
    prepared: dict[str, RawJobRecord] = {}
    unresolved_indeed: list[EmailJobCandidate] = []
    for _message, candidate in tasks:
        stored = find_stored_job_detail(candidate, runtimes)
        if stored is not None:
            prepared[canonical_link_key(candidate.url)] = raw_from_stored_detail(
                candidate,
                stored,
                scraped_at,
            )
            continue
        source_id, source_job_id = platform_job_reference(candidate.url)
        if source_id == "indeed" and source_job_id:
            unresolved_indeed.append(candidate)
    if not unresolved_indeed or not brightdata_detail_enrichment_enabled():
        return prepared
    urls = [indeed_detail_url(candidate.url) or candidate.url for candidate in unresolved_indeed]
    resolution = asyncio.run(
        _execute_brightdata_detail_batches_bounded(
            urls,
            snapshot_database=runtimes[0].database,
            request_timeout_seconds=runtimes[0].config.http.timeout_seconds,
            event_logger=lambda message: _report_email_resolution(
                message,
                runtimes,
                dashboard,
            ),
        )
    )
    records_by_id: dict[str, tuple[dict[str, object], BrightDataBatchResult]] = {}
    for batch in resolution.batches:
        for record in batch.records:
            records_by_id[str(record.get("record_id") or "")] = (record, batch)
    for failed_url, error in resolution.errors_by_url.items():
        log_error(f"Email | Bright Data detail enrichment failed | URL {failed_url} | {error}")
    for candidate in unresolved_indeed:
        link_key = canonical_link_key(candidate.url)
        _source_id, source_job_id = platform_job_reference(candidate.url)
        resolved_record = records_by_id.get(source_job_id)
        if resolved_record is not None:
            record, batch = resolved_record
            prepared[link_key] = raw_from_brightdata_detail(
                candidate,
                record,
                batch,
                scraped_at,
            )
            continue
        raw = email_candidate_to_raw_job(candidate, scraped_at=scraped_at)
        raw.raw_payload["detail_status"] = (
            "email_fallback" if can_use_email_fallback(candidate, raw) else "too_sparse"
        )
        detail_url = indeed_detail_url(candidate.url) or candidate.url
        raw.raw_payload["detail_error"] = resolution.errors_by_url.get(
            detail_url,
            "Bright Data returned no detail record",
        )
        prepared[link_key] = raw
    return prepared


def _report_email_resolution(
    message: str,
    runtimes: list[TrackRuntime],
    dashboard: LiveRunTable | None,
) -> None:
    log_line(message)
    if dashboard is None:
        return
    lowered = message.casefold()
    progress = "Running"
    for status in ("ready", "running", "queued", "failed"):
        if f"status {status}" in lowered:
            progress = status.title()
            break
    for runtime in runtimes:
        dashboard.update(
            runtime.track_label,
            "Email",
            stage="Resolving",
            progress_text=progress,
            detail="Bright Data details",
        )


def brightdata_detail_enrichment_enabled() -> bool:
    return bool(
        os.getenv("BRIGHTDATA_EMAIL_DETAIL_ENRICHMENT_ENABLED", "").strip().lower() == "true"
        and os.getenv("BRIGHTDATA_API_KEY", "").strip()
        and os.getenv("BRIGHTDATA_DATASET_ID", "").strip()
    )


async def _execute_brightdata_detail_batches_bounded(
    urls: Sequence[str],
    *,
    snapshot_database: Database | None,
    request_timeout_seconds: float,
    total_timeout_seconds: float = BRIGHTDATA_EMAIL_DETAIL_TOTAL_TIMEOUT_SECONDS,
    event_logger: Callable[[str], None] | None = None,
) -> BrightDataDetailResolutionResult:
    unique_urls = list(dict.fromkeys(url.strip() for url in urls if url.strip()))
    if not unique_urls:
        return BrightDataDetailResolutionResult(batches=[], errors_by_url={})
    try:
        return await asyncio.wait_for(
            execute_resilient_brightdata_detail_batches(
                unique_urls,
                snapshot_database=snapshot_database,
                timeout_seconds=BRIGHTDATA_EMAIL_DETAIL_SNAPSHOT_TIMEOUT_SECONDS,
                request_timeout_seconds=request_timeout_seconds,
                event_logger=event_logger,
            ),
            timeout=total_timeout_seconds,
        )
    except TimeoutError:
        message = (
            "Bright Data detail enrichment exceeded total timeout of "
            f"{total_timeout_seconds:g} seconds"
        )
        return BrightDataDetailResolutionResult(
            batches=[],
            errors_by_url=dict.fromkeys(unique_urls, message),
        )


def resolve_prepared_or_fetch(
    candidate: EmailJobCandidate,
    runtimes: list[TrackRuntime],
    scraped_at: datetime,
    prepared: dict[str, RawJobRecord],
) -> RawJobRecord:
    resolved = prepared.get(canonical_link_key(candidate.url))
    if resolved is not None:
        return resolved
    return enrich_email_candidate_bounded(candidate, runtimes, scraped_at)


def enrich_email_candidate_bounded(
    candidate: EmailJobCandidate,
    runtimes: list[TrackRuntime],
    scraped_at: datetime,
) -> RawJobRecord:
    """Resolve one email card's detail page, degrading instead of raising.

    The fetch bounds itself (socket timeout, response-size cap, and a total
    wall-clock budget shared across retries), so this runs on the caller's
    worker thread. It previously spawned one short-lived interpreter per
    candidate purely to get a hard timeout -- about 160ms of process startup
    per URL, and up to `--detail-workers` full interpreters resident at once.
    """
    http_config = runtimes[0].config.http
    try:
        return enrich_email_candidate_to_raw_job(candidate, http_config, scraped_at)
    except Exception as exc:
        return _email_detail_failure_raw(candidate, scraped_at, f"{type(exc).__name__}: {exc}")


def _email_detail_failure_raw(
    candidate: EmailJobCandidate,
    scraped_at: datetime,
    error: str,
) -> RawJobRecord:
    raw = email_candidate_to_raw_job(candidate, scraped_at=scraped_at)
    raw.raw_payload["detail_status"] = (
        "email_fallback" if can_use_email_fallback(candidate, raw) else "too_sparse"
    )
    raw.raw_payload["detail_error"] = error
    return raw


def raw_from_stored_detail(
    candidate: EmailJobCandidate,
    stored: StoredJobDetail,
    scraped_at: datetime,
) -> RawJobRecord:
    raw = email_candidate_to_raw_job(candidate, scraped_at=scraped_at)
    raw.title = stored.title or raw.title
    raw.company_name = stored.company_name or raw.company_name
    raw.location_raw = stored.location_text or raw.location_raw
    raw.job_description = stored.description
    raw.employment_type = stored.employment_type or raw.employment_type
    raw.raw_payload.update(
        {
            "detail_status": "ok",
            "detail_source": "workspace_source",
            "description_source": "workspace_source",
            "title_source": "workspace_source",
            "matched_source_id": stored.source_id,
            "matched_source_job_id": stored.source_job_id,
        }
    )
    return raw


def raw_from_brightdata_detail(
    candidate: EmailJobCandidate,
    record: dict[str, object],
    batch: BrightDataBatchResult,
    scraped_at: datetime,
) -> RawJobRecord:
    raw = email_candidate_to_raw_job(candidate, scraped_at=scraped_at)
    raw.title = str(record.get("position_title") or raw.title)
    raw.company_name = str(record.get("organization") or raw.company_name)
    raw.location_raw = str(record.get("region") or raw.location_raw)
    raw.job_description = str(record.get("description") or "")
    raw.employment_type = str(record.get("employment_type") or "unknown")
    raw.posted_at_text = str(record.get("timestamp") or raw.posted_at_text)
    raw.canonical_url = str(record.get("reference_url") or raw.canonical_url)
    payload = record.get("raw_payload")
    raw.raw_payload.update(
        {
            "detail_status": "ok",
            "detail_source": "brightdata_detail",
            "description_source": "brightdata_detail",
            "title_source": "brightdata_detail",
            "brightdata_snapshot_id": batch.snapshot_id,
            "matched_source_id": "indeed",
            "matched_source_job_id": str(record.get("record_id") or ""),
            "brightdata_detail_payload": payload if isinstance(payload, dict) else {},
        }
    )
    return raw


def find_stored_job_detail(
    candidate: EmailJobCandidate,
    runtimes: list[TrackRuntime],
) -> StoredJobDetail | None:
    source_id, source_job_id = platform_job_reference(candidate.url)
    if not source_id or not source_job_id:
        return None
    checked_paths: set[Path] = set()
    for runtime in runtimes:
        workspace = runtime.workspace_database
        if workspace is None or workspace.path in checked_paths:
            continue
        checked_paths.add(workspace.path)
        detail = workspace.find_source_job_detail(source_id, source_job_id)
        if detail is not None:
            return detail
    return None


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
    policy = email_policy_for_raw(raw, policy, runtime.platform_country_scope)
    if str(raw.raw_payload.get("detail_status") or "") == "email_fallback":
        policy = title_scoped(policy)
    normalized = normalize_candidate(raw, policy)
    normalized.dedupe_key = stable_email_dedupe_key(normalized)
    result = runtime.processor.process_normalized(
        normalized,
        CandidateProcessingContext(
            profile_id=runtime.profile_id,
            run_id=stats.run_id,
            started_at=started_at,
            policy=policy,
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


def email_policy_for_raw(
    raw: RawJobRecord,
    policy: FilterPolicy,
    platform_country_overrides: Mapping[str, tuple[str, ...]] | None = None,
) -> FilterPolicy:
    """Widen the country scope for platforms configured to allow it.

    Some job boards list a role in one country but recruit across a region, so
    a profile may choose to accept neighbouring countries from that source
    only. Which platforms and which countries is a private routing decision,
    supplied through `[platform_country_scope]` in the mailbox config rather
    than baked in here.
    """
    if not platform_country_overrides:
        return policy
    for platform in email_source_platforms(raw):
        countries = platform_country_overrides.get(platform)
        if countries:
            return replace(policy, countries=tuple(countries))
    return policy


def email_source_platforms(raw: RawJobRecord) -> tuple[str, ...]:
    platforms = raw.raw_payload.get("source_platforms", [])
    values = (
        [str(platform).strip().casefold() for platform in platforms]
        if isinstance(platforms, list | tuple | set)
        else []
    )
    if is_efinancialcareers_url(raw.source_url):
        values.append("efinancialcareers")
    return tuple(dict.fromkeys(value for value in values if value))


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
    return build_dedupe_key(
        job.title,
        job.company_name,
        job.location_raw,
        job.source_job_id,
        description="",
        source=job.source,
    )


if __name__ == "__main__":
    raise SystemExit(main())
