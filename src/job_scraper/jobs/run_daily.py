from __future__ import annotations

import argparse
import sys
from collections import Counter
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv

from job_scraper.adapters.sinks.csv import CsvSink
from job_scraper.adapters.sinks.notion_payload import na_value
from job_scraper.adapters.sinks.notion_workflow import (
    import_processed_statuses,
)
from job_scraper.adapters.storage.notion_bindings import NotionDatabaseBindingStore
from job_scraper.adapters.storage.sqlite_v2 import WorkspaceDatabase
from job_scraper.application.acquisition import RequestCoalescer, RequestGate
from job_scraper.application.aggregation import AcceptedJob
from job_scraper.application.process_candidate import ProcessJobCandidate
from job_scraper.application.run_plan import (
    DEFAULT_SINK_IDS,
    ProfileRunRequest,
    RunPlan,
    acquisition_produced_nothing,
    effective_post_age_hours,
    resolve_export_destination,
    resolve_profile_id,
    run_exit_code,
    select_sink_ids,
)
from job_scraper.application.run_profile import CandidateEvent, RunProfileSource
from job_scraper.cli.console import (
    LiveRunTable,
    display_track_label,
    format_reasons,
    log_error,
    log_job_status,
    log_line,
    reason_label,
    source_label,
)
from job_scraper.collectors.base import SearchWindow
from job_scraper.collectors.linkedin import LinkedInProgressEvent
from job_scraper.config import AppConfig, FiltersConfig, load_config
from job_scraper.configuration import find_profile_definition
from job_scraper.configuration.brightdata import brightdata_direct_collection_enabled
from job_scraper.configuration.composition import apply_profile_to_runtime
from job_scraper.configuration.models import ProfileDefinition
from job_scraper.configuration.policy import policy_from_legacy
from job_scraper.domain.models import RawJobRecord
from job_scraper.integrations.notion import NotionClient
from job_scraper.pipeline.normalize import (
    normalize_candidate,
)
from job_scraper.pipeline.steps import default_pipeline
from job_scraper.ports.sinks import PublishContext
from job_scraper.ports.sources import JobSource, SourceCapabilities
from job_scraper.registry.builtins import (
    SinkBuildRequest,
    SourceBuildRequest,
    build_pipeline,
    build_sink,
    build_source,
    create_builtin_registry,
)
from job_scraper.storage.db import Database


@dataclass(frozen=True, slots=True)
class RuntimeServices:
    request_coalescer: RequestCoalescer | None = None
    linkedin_request_gate: RequestGate | None = None
    dashboard: LiveRunTable | None = None


@dataclass(slots=True)
class _SourceExecution:
    collector: JobSource
    max_post_age_hours: int
    keyword_total: int = 0
    records: list[RawJobRecord] | None = None
    acquisition_error: str = ""


class _BufferedSource:
    def __init__(self, execution: _SourceExecution) -> None:
        self.source_name = execution.collector.source_name
        self.capabilities = getattr(
            execution.collector,
            "capabilities",
            SourceCapabilities(acquisition_mode="buffered", platform=self.source_name),
        )
        self._records = execution.records or []
        self._error = execution.acquisition_error

    def collect(self, window: SearchWindow) -> Iterable[RawJobRecord]:
        del window
        if self._error:
            raise RuntimeError(self._error)
        yield from self._records

    def validate_runtime(self) -> None:
        return None


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run one configured job-discovery profile.")
    parser.add_argument(
        "--config",
        required=True,
        help="Path to the private runtime configuration TOML.",
    )
    parser.add_argument(
        "--export-csv",
        help="Optional CSV export path. Defaults to exports/jobs_<today>.csv using the configured timezone.",
    )
    parser.add_argument(
        "--skip-export", action="store_true", help="Do not write the daily CSV export."
    )
    parser.add_argument("--skip-notion", action="store_true", help="Do not sync to Notion.")
    parser.add_argument(
        "--ignore-post-age",
        action="store_true",
        help="Disable posted-at age filtering for this run. Useful for one-time inventory backfills.",
    )
    parser.add_argument(
        "--post-age-days",
        type=positive_int,
        help="Override the online post-age window in days. For example, 2 pulls postings up to 48 hours old.",
    )
    parser.add_argument(
        "--query",
        action="append",
        dest="search_queries",
        help="Temporarily replace the profile search matrix. Repeat to use multiple queries.",
    )
    parser.add_argument(
        "--source",
        action="append",
        dest="only_sources",
        help=(
            "Run only these of the profile's sources. Repeat to select several. "
            "Use when one source belongs on a different schedule from the rest."
        ),
    )
    return parser.parse_args(argv)


def main(
    argv: list[str] | None = None,
    *,
    runtime: RuntimeServices | None = None,
) -> int:
    load_dotenv()
    args = parse_args(argv if argv is not None else sys.argv[1:])
    request = ProfileRunRequest(
        config_path=Path(args.config),
        export_csv=args.export_csv,
        skip_export=args.skip_export,
        skip_notion=args.skip_notion,
        ignore_post_age=args.ignore_post_age,
        post_age_days=args.post_age_days,
        search_queries=tuple(args.search_queries or ()),
        only_sources=tuple(args.only_sources or ()),
    )
    return execute(request, runtime=runtime)


def execute(request: ProfileRunRequest, *, runtime: RuntimeServices | None = None) -> int:
    """Run one profile from typed input without reparsing command-line arguments."""
    config = load_config(request.config_path)
    profile = find_profile_definition(request.config_path)
    if profile is not None:
        apply_profile_to_runtime(profile, config)
    _disable_brightdata_when_suspended(config)
    if request.only_sources:
        restrict_sources(config, list(request.only_sources), profile_id=str(request.config_path))
    if request.search_queries:
        override_search_queries(config, list(request.search_queries))
    track_label = display_track_label(config.project.track_label)
    runtime = runtime or RuntimeServices(
        request_coalescer=RequestCoalescer(),
        linkedin_request_gate=RequestGate(
            max_concurrency=4,
            min_interval_seconds=0.75,
            rate_limit_cooldown_seconds=30,
        ),
    )
    database = Database(config.project.database_path)
    database.initialize()

    sources = build_collectors(config, runtime=runtime, track_label=track_label)
    try:
        validate_collector_runtimes(sources)
    except RuntimeError as exc:
        log_error(f"Runtime preflight failed | {exc}")
        return 2

    profile_id = resolve_profile_id(
        profile.profile_id if profile is not None else None,
        track_label,
    )
    notion = NotionClient(config.notion)
    notion_binding_store = NotionDatabaseBindingStore(
        config.project.database_path.parent / "notion_database_bindings.json"
    )

    if notion.enabled() and not request.skip_notion:
        synced_statuses = sync_processed_statuses_from_notion(
            database,
            notion,
            table_title=track_table_title(config.notion.daily_table_prefix, track_label),
            binding_store=notion_binding_store,
            profile_id=profile_id,
        )
        log_line(f"Notion | Imported processed statuses {synced_statuses}")

    window = SearchWindow(started_at=datetime.now(UTC), overlap_hours=config.project.overlap_hours)
    accepted_jobs: dict[str, AcceptedJob] = {}
    workspace_database = _initialize_workspace_database(config)
    registry = create_builtin_registry()
    pipeline = (
        build_pipeline(registry, profile.pipeline) if profile is not None else default_pipeline()
    )
    candidate_processor = ProcessJobCandidate(
        database,
        pipeline,
        normalize_candidate,
        decision_recorder=workspace_database,
    )
    source_runner = RunProfileSource(
        database,
        candidate_processor,
        on_candidate=lambda event: _log_candidate_event(
            event,
            track_label=track_label,
            dashboard=runtime.dashboard,
        ),
    )
    collection_failed = False
    employment_scope = "full-time roles" if config.filters.full_time_only else "roles"
    log_line(
        f"Run started | Sources: {', '.join(collector.source_name for collector in sources)} | "
        f"Track: {track_label} | Filter: {track_label} {employment_scope} using the configured post-age window"
    )

    executions: list[_SourceExecution] = []
    for collector in sources:
        configured_post_age_hours = determine_post_age_hours(
            database,
            collector.source_name,
            config.project.recent_post_age_hours,
            config.project.bootstrap_post_age_hours,
        )
        max_post_age_hours = effective_post_age_hours(
            configured_post_age_hours,
            override_days=request.post_age_days,
            ignore_post_age=request.ignore_post_age,
        )
        executions.append(
            _SourceExecution(
                collector=collector,
                max_post_age_hours=max_post_age_hours,
                keyword_total=_collector_keyword_total(collector),
            )
        )
        post_age_label = "disabled" if max_post_age_hours <= 0 else f"{max_post_age_hours}h"
        log_line(
            f"{source_label(collector.source_name)} | Start | Post-age window {post_age_label}"
        )
        if runtime.dashboard is not None:
            runtime.dashboard.update(
                track_label,
                source_label(collector.source_name),
                stage="Fetching",
                keywords_done=0,
                keywords_total=_collector_keyword_total(collector),
                detail=f"Window {post_age_label}",
            )

    for execution in _acquire_sources(
        executions,
        window=window,
        track_label=track_label,
        dashboard=runtime.dashboard,
    ):
        collector = execution.collector
        if runtime.dashboard is not None:
            runtime.dashboard.update(
                track_label,
                source_label(collector.source_name),
                stage="Filtering",
                keywords_done=execution.keyword_total,
                keywords_total=execution.keyword_total,
                progress_text="",
                seen=len(execution.records or []),
                detail="Applying policy",
            )
        result = source_runner.execute(
            _BufferedSource(execution),
            profile_id=profile_id,
            started_at=window.started_at,
            overlap_hours=window.overlap_hours,
            max_post_age_hours=execution.max_post_age_hours,
            policy=policy_from_legacy(
                config.filters,
                max_post_age_hours=execution.max_post_age_hours,
            ),
            accepted_jobs=accepted_jobs,
        )
        if result.failed:
            collection_failed = True
            log_error(f"{source_label(collector.source_name)} | Failed | {result.error}")
            if runtime.dashboard is not None:
                runtime.dashboard.update(
                    track_label,
                    source_label(collector.source_name),
                    stage="Failed",
                    detail=result.error,
                )
            continue
        stats = result.stats
        if runtime.dashboard is not None:
            runtime.dashboard.update(
                track_label,
                source_label(collector.source_name),
                stage="Done",
                keywords_done=execution.keyword_total,
                keywords_total=execution.keyword_total,
                progress_text="",
                seen=stats.jobs_seen,
                accepted=stats.jobs_new + stats.jobs_updated,
                filtered=stats.jobs_filtered,
                detail=format_reasons(Counter(result.reject_counts)),
            )
        log_line(
            f"{source_label(collector.source_name)} | Done | "
            f"Seen {stats.jobs_seen} | Accepted {stats.jobs_new + stats.jobs_updated} | "
            f"New {stats.jobs_new} | Updated {stats.jobs_updated} | "
            f"Filtered {stats.jobs_filtered} | Main filters: {format_reasons(Counter(result.reject_counts))}"
        )

    if acquisition_produced_nothing(
        collection_failed=collection_failed,
        accepted_count=len(accepted_jobs),
    ):
        log_error(f"Run failed | Track: {track_label} | No jobs reached downstream synchronization")
        return 1

    plan = build_run_plan(request, config, profile, started_at=window.started_at)
    export_destination = plan.export_destination
    publish_context = PublishContext(
        run_id=f"profile:{plan.profile_id}",
        profile_id=plan.profile_id,
    )
    publish_had_errors = False
    for sink_id in plan.sink_ids:
        sink = build_sink(
            registry,
            sink_id,
            SinkBuildRequest(
                repository=database,
                policy=plan.sink_policy,
                started_at=window.started_at,
                profile_id=publish_context.profile_id,
                profile_label=track_label,
                timezone_name=config.project.timezone,
                csv_destination=export_destination,
                retained_exports=config.project.retained_exports,
                notion_client=notion,
                notion_table_prefix=config.notion.daily_table_prefix,
                notion_binding_store=notion_binding_store,
                logger=log_line,
            ),
        )
        result = sink.publish(list(accepted_jobs.values()), publish_context)
        if sink_id == "csv":
            if result.published:
                log_line(f"Export | Wrote {result.published} rows to {export_destination}")
            else:
                log_line(f"Export | No rows to write: {export_destination}")
        if result.errors:
            publish_had_errors = True
            log_line(
                f"{sink_id} | {result.published} synced, {len(result.errors)} failed | "
                f"Track: {track_label}"
            )
            for error in result.errors:
                log_line(f"{sink_id} | Failed | {error}")

    log_line(f"Run finished | Track: {track_label} | Accepted total: {len(accepted_jobs)}")
    return run_exit_code(
        collection_failed=collection_failed,
        publish_had_errors=publish_had_errors,
        accepted_count=len(accepted_jobs),
    )


def build_run_plan(
    request: ProfileRunRequest,
    config: AppConfig,
    profile: ProfileDefinition | None,
    *,
    started_at: datetime,
) -> RunPlan:
    """Decide what this run will do, before it does anything."""
    track_label = display_track_label(config.project.track_label)
    export_destination = resolve_export_destination(
        explicit_destination=request.export_csv,
        skip_export=request.skip_export,
        timezone_name=config.project.timezone,
        export_dir=config.project.export_dir,
        started_at=started_at,
        file_prefix=config.project.export_filename_prefix,
    )
    return RunPlan(
        profile_id=resolve_profile_id(
            profile.profile_id if profile is not None else None,
            track_label,
        ),
        track_label=track_label,
        sink_ids=select_sink_ids(
            profile.sinks if profile is not None else DEFAULT_SINK_IDS,
            skip_export=request.skip_export,
            skip_notion=request.skip_notion,
            has_export_destination=export_destination is not None,
        ),
        export_destination=export_destination,
        sink_policy=policy_from_legacy(
            config.filters,
            max_post_age_hours=config.project.recent_post_age_hours,
        ),
    )


def _initialize_workspace_database(
    config: AppConfig,
) -> WorkspaceDatabase | None:
    path = config.project.workspace_database_path
    if path is None:
        return None
    workspace = WorkspaceDatabase(path)
    workspace.initialize()
    return workspace


def build_collectors(
    config: AppConfig,
    *,
    runtime: RuntimeServices | None = None,
    track_label: str = "",
) -> list[JobSource]:
    """Construct enabled upstream adapters in deterministic orchestration order."""
    registry = create_builtin_registry()
    runtime = runtime or RuntimeServices()
    collectors: list[JobSource] = []
    for source_id, settings in config.sources.items():
        if not settings.enabled:
            continue
        collectors.append(
            build_source(
                registry,
                source_id,
                SourceBuildRequest(
                    http=config.http,
                    settings=settings,
                    company_names=tuple(config.filters.company_names),
                    request_coalescer=runtime.request_coalescer,
                    request_gate=runtime.linkedin_request_gate,
                    snapshot_database=Database(config.project.database_path),
                    event_logger=lambda message: _update_indeed_progress(
                        runtime.dashboard, track_label, message
                    ),
                    progress_callback=lambda event: _update_linkedin_progress(
                        runtime.dashboard, track_label, event
                    ),
                ),
            )
        )
    return collectors


def _disable_brightdata_when_suspended(config: AppConfig) -> None:
    """Keep an accidentally selected paid source from issuing live requests."""
    if brightdata_direct_collection_enabled():
        return
    source = config.sources.get("indeed_brightdata")
    if source is not None:
        source.enabled = False


def restrict_sources(config: AppConfig, only: list[str], *, profile_id: str = "") -> None:
    """Narrow this run to a subset of the profile's already-enabled sources.

    A profile's `sources` list is a composition decision -- which adapters this
    track uses at all. It is not a schedule, and some sources do not want the
    schedule the rest of their profile runs on: a board sweep costing one
    request per configured employer belongs on a weekly timer beside a daily
    one, and there is otherwise no way to express that without duplicating the
    whole track as a second profile.

    Selecting a source the profile does not enable is an error rather than a
    silent no-op, because the failure it hides -- a timer quietly acquiring
    nothing after a profile edit renamed or dropped the source -- looks exactly
    like a source that legitimately found no new postings.
    """

    requested = list(dict.fromkeys(name.strip() for name in only if name.strip()))
    if not requested:
        return
    enabled = {source_id for source_id, settings in config.sources.items() if settings.enabled}
    unknown = sorted(set(requested) - enabled)
    if unknown:
        where = f" for {profile_id}" if profile_id else ""
        raise ValueError(
            f"--source selected {', '.join(unknown)}, which the profile does not enable"
            f"{where}; enabled sources are {', '.join(sorted(enabled)) or '(none)'}"
        )
    for source_id, settings in config.sources.items():
        settings.enabled = source_id in requested


def override_search_queries(config: AppConfig, queries: list[str]) -> None:
    """Apply a run-scoped search matrix without mutating checked-in profile files."""

    normalized = list(dict.fromkeys(query.strip() for query in queries if query.strip()))
    if not normalized:
        raise ValueError("At least one non-empty --query value is required")
    for settings in config.sources.values():
        settings.search_queries = list(normalized)


def validate_collector_runtimes(collectors: list[JobSource]) -> None:
    for collector in collectors:
        validator = getattr(collector, "validate_runtime", None)
        if callable(validator):
            validator()


def positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"{value!r} is not an integer") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be greater than 0")
    return parsed


def export_csv(database: Database, destination: Path, filters: FiltersConfig) -> None:
    sink = CsvSink(database, destination, policy_from_legacy(filters))
    result = sink.publish(
        (),
        PublishContext(run_id="legacy", profile_id="legacy"),
    )
    if result.published == 0:
        log_line(f"Export | No rows to write: {destination}")
        return
    log_line(f"Export | Wrote {result.published} rows to {destination}")


def sync_processed_statuses_from_notion(
    database: Database,
    notion: NotionClient,
    *,
    table_title: str = "",
    binding_store: NotionDatabaseBindingStore | None = None,
    profile_id: str = "",
) -> int:
    return import_processed_statuses(
        database,
        notion,
        table_title=table_title,
        binding_store=binding_store,
        profile_id=profile_id,
        error_logger=log_error,
    )


def track_table_title(table_prefix: str, track_label: str) -> str:
    prefix = " ".join((table_prefix or track_label).split()).strip()
    return f"{prefix} Jobs"


def _log_candidate_event(
    event: CandidateEvent,
    *,
    track_label: str = "",
    dashboard: LiveRunTable | None = None,
) -> None:
    if dashboard is not None:
        stats = event.stats
        dashboard.update(
            track_label,
            source_label(event.source_id),
            stage="Filtering",
            seen=stats.jobs_seen,
            accepted=stats.jobs_new + stats.jobs_updated,
            filtered=stats.jobs_filtered,
            detail=reason_label(event.reason) if event.reason else event.outcome,
        )
        return
    log_job_status(
        event.source_id,
        event.seen_index,
        event.outcome,
        reason_label(event.reason) if event.reason else "",
        event.job.title,
        event.job.company_name,
        na_value(event.job.city),
    )
    if event.seen_index % 10 != 0:
        return
    stats = event.stats
    log_line(
        f"{source_label(event.source_id)} | Progress | "
        f"Seen {stats.jobs_seen} | "
        f"Accepted {stats.jobs_new + stats.jobs_updated} | "
        f"Filtered {stats.jobs_filtered} | "
        f"Main filters: {format_reasons(Counter(event.reject_counts))}"
    )


def _acquire_sources(
    executions: list[_SourceExecution],
    *,
    window: SearchWindow,
    track_label: str,
    dashboard: LiveRunTable | None,
) -> Iterable[_SourceExecution]:
    if len(executions) <= 1:
        for execution in executions:
            _acquire_source(execution, window)
            yield execution
        return

    with ThreadPoolExecutor(
        max_workers=len(executions),
        thread_name_prefix="source",
    ) as executor:
        futures = {
            executor.submit(_acquire_source, execution, window): execution
            for execution in executions
        }
        for future in as_completed(futures):
            execution = futures[future]
            future.result()
            if dashboard is not None:
                dashboard.update(
                    track_label,
                    source_label(execution.collector.source_name),
                    stage="Filtering" if not execution.acquisition_error else "Failed",
                    keywords_done=execution.keyword_total,
                    keywords_total=execution.keyword_total,
                    progress_text="",
                    seen=len(execution.records or []),
                    detail=execution.acquisition_error or "Acquisition complete",
                )
            yield execution


def _acquire_source(execution: _SourceExecution, window: SearchWindow) -> None:
    source_window = SearchWindow(
        started_at=window.started_at,
        overlap_hours=window.overlap_hours,
        post_age_hours=execution.max_post_age_hours,
    )
    try:
        execution.records = list(execution.collector.collect(source_window))
    except Exception as exc:
        execution.acquisition_error = str(exc)
        execution.records = []


def _collector_keyword_total(collector: JobSource) -> int:
    source_config = getattr(collector, "source_config", None)
    queries = getattr(source_config, "search_queries", None)
    if queries is None:
        queries = getattr(collector, "search_queries", ())
    return len(queries or ())


def _update_linkedin_progress(
    dashboard: LiveRunTable | None,
    track_label: str,
    event: LinkedInProgressEvent,
) -> None:
    if dashboard is None:
        return
    dashboard.update(
        track_label,
        "LinkedIn",
        stage="Fetching",
        keywords_done=event.completed_queries,
        keywords_total=event.total_queries,
        detail=f"{event.query}: {event.listings}",
    )


def _update_indeed_progress(
    dashboard: LiveRunTable | None,
    track_label: str,
    message: str,
) -> None:
    if dashboard is None:
        log_line(message)
        return
    dashboard.record_message(f"{track_label} | {message}")
    detail = message.rsplit("|", maxsplit=1)[-1].strip() or "Cloud snapshot running"
    progress_text = _indeed_progress_text(message)
    dashboard.update(
        track_label,
        "Indeed",
        stage="Fetching",
        progress_text=progress_text,
        detail=detail,
    )


def _indeed_progress_text(message: str) -> str:
    lowered = message.casefold()
    for status in ("ready", "running", "queued", "failed"):
        if f"status {status}" in lowered:
            return status.title()
    if "snapshot downloaded" in lowered:
        return "Ready"
    if "snapshot" in lowered:
        return "Running"
    return ""


def determine_post_age_hours(
    database: Database,
    source_name: str,
    recent_post_age_hours: int,
    bootstrap_post_age_hours: int,
) -> int:
    if database.has_source_observations(source_name):
        return recent_post_age_hours
    return bootstrap_post_age_hours


if __name__ == "__main__":
    raise SystemExit(main())
