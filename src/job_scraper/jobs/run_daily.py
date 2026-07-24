from __future__ import annotations

import argparse
import inspect
import json
import sys
import time
from collections import Counter
from collections.abc import Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import cast
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from job_scraper.adapters.sinks.csv import CsvSink
from job_scraper.adapters.sinks.notion_payload import (
    build_children as _build_children,
)
from job_scraper.adapters.sinks.notion_payload import (
    build_daily_properties as _build_daily_properties,
)
from job_scraper.adapters.sinks.notion_payload import (
    build_job_title as _build_job_title,
)
from job_scraper.adapters.sinks.notion_payload import (
    location_options as _location_options,
)
from job_scraper.adapters.sinks.notion_payload import (
    notion_language_name as _notion_language_name,
)
from job_scraper.adapters.sinks.notion_payload import (
    notion_page_company_name as _notion_page_company_name,
)
from job_scraper.adapters.sinks.notion_payload import (
    notion_page_job_title_and_url as _notion_page_job_title_and_url,
)
from job_scraper.adapters.sinks.notion_payload import (
    notion_page_status as _notion_page_status,
)
from job_scraper.adapters.sinks.notion_payload import (
    render_location as _render_location,
)
from job_scraper.adapters.sinks.notion_payload import (
    rich_text_property as _rich_text_property,
)
from job_scraper.adapters.sinks.notion_workflow import (
    configured_data_source_ids,
    import_processed_statuses,
    publish_daily,
)
from job_scraper.adapters.storage.sqlite_v2 import WorkspaceDatabase
from job_scraper.application.acquisition import RequestCoalescer, RequestGate
from job_scraper.application.aggregation import (
    AcceptedJob,
    merge_accepted_job,
)
from job_scraper.application.aggregation import (
    accepted_database_ids as _accepted_database_ids,
)
from job_scraper.application.aggregation import (
    add_source_provenance as _add_source_provenance,
)
from job_scraper.application.aggregation import (
    normalize_list as _normalize_list,
)
from job_scraper.application.aggregation import (
    unique_list as _unique_list,
)
from job_scraper.application.process_candidate import ProcessJobCandidate
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
from job_scraper.configuration.composition import apply_profile_to_runtime
from job_scraper.domain.identity import canonical_identity
from job_scraper.integrations.notion import NotionClient, normalize_status_name
from job_scraper.models import JobRecord, RawJobRecord
from job_scraper.pipeline.context import EvaluationContext
from job_scraper.pipeline.export_filter import (
    ExportRow,
    export_row_matches_policy,
)
from job_scraper.pipeline.export_filter import (
    export_row_is_germany as _export_row_is_germany,
)
from job_scraper.pipeline.normalize import (
    looks_like_target_countries,
    normalize_candidate,
    parse_country_codes,
)
from job_scraper.pipeline.policy_adapter import policy_from_legacy
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

PROCESSED_APPLICATION_STATUSES = {"applied", "not_interested"}


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
    parser.add_argument("--init-db", action="store_true", help="Initialize the database and exit.")
    parser.add_argument(
        "--export-csv",
        help="Optional CSV export path. Defaults to exports/jobs_<today>.csv using the configured timezone.",
    )
    parser.add_argument(
        "--skip-export", action="store_true", help="Do not write the daily CSV export."
    )
    parser.add_argument("--skip-notion", action="store_true", help="Do not sync to Notion.")
    parser.add_argument(
        "--enable-indeed",
        action="store_true",
        help="Enable Indeed through Bright Data, inheriting the track's LinkedIn search matrix.",
    )
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
    return parser.parse_args(argv)


def main(
    argv: list[str] | None = None,
    *,
    runtime: RuntimeServices | None = None,
) -> int:
    load_dotenv()
    args = parse_args(argv if argv is not None else sys.argv[1:])
    config = load_config(args.config)
    profile = find_profile_definition(args.config)
    if profile is not None:
        apply_profile_to_runtime(profile, config)
    if args.enable_indeed:
        enable_indeed_source(config)
    if args.search_queries:
        override_search_queries(config, args.search_queries)
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

    if args.init_db:
        print(f"Initialized database at {config.project.database_path}")
        return 0

    sources = _invoke_build_collectors(config, runtime, track_label)
    try:
        validate_collector_runtimes(sources)
    except RuntimeError as exc:
        log_error(f"Runtime preflight failed | {exc}")
        return 2

    notion = NotionClient(config.notion)

    if notion.enabled():
        synced_statuses = sync_processed_statuses_from_notion(
            database,
            notion,
            table_title=track_table_title(config.notion.daily_table_prefix, track_label),
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
            override_days=args.post_age_days,
            ignore_post_age=args.ignore_post_age,
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
                seen=len(execution.records or []),
                detail="Applying policy",
            )
        result = source_runner.execute(
            _BufferedSource(execution),
            profile_id=profile.profile_id if profile is not None else track_label,
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

    if collection_failed and not accepted_jobs:
        log_error(f"Run failed | Track: {track_label} | No jobs reached downstream synchronization")
        return 1

    export_destination = resolve_export_destination(
        explicit_destination=args.export_csv,
        skip_export=args.skip_export,
        timezone_name=config.project.timezone,
        export_dir=config.project.export_dir,
        started_at=window.started_at,
        file_prefix=config.project.export_filename_prefix,
    )
    sink_ids = list(profile.sinks if profile is not None else ("csv", "notion_daily"))
    if args.skip_export or export_destination is None:
        sink_ids = [sink_id for sink_id in sink_ids if sink_id != "csv"]
    if args.skip_notion:
        sink_ids = [sink_id for sink_id in sink_ids if sink_id != "notion_daily"]
    sink_policy = policy_from_legacy(
        config.filters,
        max_post_age_hours=config.project.recent_post_age_hours,
    )
    publish_context = PublishContext(
        run_id=f"profile:{profile.profile_id if profile is not None else track_label}",
        profile_id=profile.profile_id if profile is not None else track_label,
    )
    for sink_id in sink_ids:
        sink = build_sink(
            registry,
            sink_id,
            SinkBuildRequest(
                repository=database,
                policy=sink_policy,
                started_at=window.started_at,
                profile_id=publish_context.profile_id,
                profile_label=track_label,
                timezone_name=config.project.timezone,
                csv_destination=export_destination,
                notion_client=notion,
                notion_table_prefix=config.notion.daily_table_prefix,
                services={"logger": log_line},
            ),
        )
        result = sink.publish(
            cast(Sequence, list(accepted_jobs.values())),
            publish_context,
        )
        if sink_id == "csv":
            if result.published:
                log_line(f"Export | Wrote {result.published} rows to {export_destination}")
            else:
                log_line(f"Export | No rows to write: {export_destination}")

    log_line(f"Run finished | Track: {track_label} | Accepted total: {len(accepted_jobs)}")
    return 1 if collection_failed else 0


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
                    services={
                        "request_coalescer": runtime.request_coalescer,
                        "request_gate": runtime.linkedin_request_gate,
                        "snapshot_database": Database(config.project.database_path),
                        "event_logger": (
                            lambda message: _update_indeed_progress(
                                runtime.dashboard,
                                track_label,
                                message,
                            )
                        ),
                        "progress_callback": (
                            lambda event: _update_linkedin_progress(
                                runtime.dashboard,
                                track_label,
                                event,
                            )
                        ),
                    },
                ),
            )
        )
    return collectors


def _invoke_build_collectors(
    config: AppConfig,
    runtime: RuntimeServices,
    track_label: str,
) -> list[JobSource]:
    """Keep simple adapter test doubles compatible with the extended composition API."""

    if "runtime" in inspect.signature(build_collectors).parameters:
        return build_collectors(config, runtime=runtime, track_label=track_label)
    return build_collectors(config)


def enable_indeed_source(config: AppConfig) -> None:
    """Apply a bounded Indeed overlay without duplicating the profile search matrix."""
    try:
        source = config.sources["indeed_brightdata"]
    except KeyError as exc:
        raise ValueError(
            "Indeed cannot be enabled because sources.indeed_brightdata is not configured"
        ) from exc
    source.enabled = True
    source.max_listing_pages = source.max_listing_pages if source.max_listing_pages > 0 else 2
    source.max_detail_fetches = source.max_detail_fetches if source.max_detail_fetches > 0 else 40
    if not source.search_queries:
        source.search_queries = _first_search_matrix(config, "search_queries")
    if not source.locations:
        source.locations = _first_search_matrix(config, "locations")
    if not source.search_queries:
        raise ValueError("Indeed cannot be enabled because the track has no search queries")


def override_search_queries(config: AppConfig, queries: list[str]) -> None:
    """Apply a run-scoped search matrix without mutating checked-in profile files."""

    normalized = list(dict.fromkeys(query.strip() for query in queries if query.strip()))
    if not normalized:
        raise ValueError("At least one non-empty --query value is required")
    for settings in config.sources.values():
        settings.search_queries = list(normalized)


def _first_search_matrix(config: AppConfig, field_name: str) -> list[str]:
    for source_id, settings in config.sources.items():
        if source_id == "indeed_brightdata":
            continue
        values = getattr(settings, field_name)
        if values:
            return list(values)
    return []


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


def effective_post_age_hours(
    configured_hours: int, override_days: int | None, ignore_post_age: bool
) -> int:
    if ignore_post_age:
        return 0
    if override_days is not None:
        return override_days * 24
    return configured_hours


def resolve_export_destination(
    explicit_destination: str | None,
    skip_export: bool,
    timezone_name: str,
    export_dir: Path,
    started_at: datetime,
    file_prefix: str,
) -> Path | None:
    if skip_export:
        return None
    if explicit_destination:
        return Path(explicit_destination)
    return build_daily_export_path(
        export_dir=export_dir,
        timezone_name=timezone_name,
        started_at=started_at,
        file_prefix=file_prefix,
    )


def build_daily_export_path(
    export_dir: Path, timezone_name: str, started_at: datetime, file_prefix: str = "jobs"
) -> Path:
    local_date = started_at.astimezone(ZoneInfo(timezone_name)).date().isoformat()
    normalized_prefix = " ".join((file_prefix or "jobs").split()).strip().replace(" ", "_")
    return export_dir / f"{normalized_prefix}_{local_date}.csv"


def build_daily_table_title(
    timezone_name: str, started_at: datetime, table_prefix: str = ""
) -> str:
    local_date = started_at.astimezone(ZoneInfo(timezone_name)).date().isoformat()
    normalized_prefix = " ".join((table_prefix or "").split()).strip()
    return f"{normalized_prefix} {local_date}".strip()


def rejection_reason(
    job: JobRecord,
    started_at: datetime,
    filters: FiltersConfig,
    max_post_age_hours: int = 24,
) -> str | None:
    policy = policy_from_legacy(
        filters,
        max_post_age_hours=max_post_age_hours,
    )
    decision = default_pipeline().evaluate(
        job,
        EvaluationContext(
            profile_id="legacy",
            started_at=started_at,
            policy=policy,
        ),
    )
    return None if decision.reason is None else decision.reason.value


def job_matches_target_countries(job: JobRecord, filters: FiltersConfig) -> bool:
    if looks_like_target_countries(
        job.location_raw,
        job.country,
        filters.country,
        raw_country=job.raw_payload.get("location_country"),
    ):
        return True
    return search_location_matches_target_country(
        job.raw_payload.get("search_location"), job.country, filters
    )


def search_location_matches_target_country(
    search_location: object, country: str, filters: FiltersConfig
) -> bool:
    if not country or country not in parse_country_codes(filters.country):
        return False
    return looks_like_target_countries(str(search_location or ""), "", filters.country)


def country_rejection_reason(filters: FiltersConfig) -> str:
    return "not_target_country"


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


def sync_daily_notion(
    database: Database,
    jobs: list[AcceptedJob],
    notion: NotionClient,
    timezone_name: str,
    started_at: datetime,
    table_prefix: str,
    track_label: str,
) -> None:
    publish_daily(
        database,
        jobs,
        notion,
        timezone_name,
        started_at,
        table_prefix,
        track_label,
        logger=log_line,
        sleeper=time.sleep,
    )


def sync_processed_statuses_from_notion(
    database: Database,
    notion: NotionClient,
    *,
    table_title: str = "",
) -> int:
    return import_processed_statuses(
        database,
        notion,
        table_title=table_title,
        error_logger=log_error,
    )


def configured_notion_data_source_ids(notion: NotionClient) -> list[str]:
    return configured_data_source_ids(notion)


def track_table_title(table_prefix: str, track_label: str) -> str:
    prefix = " ".join((table_prefix or track_label).split()).strip()
    return f"{prefix} Jobs"


def build_daily_properties(
    job: JobRecord,
    property_types: dict[str, str] | None = None,
    *,
    found_date: date | None = None,
) -> dict:
    return _build_daily_properties(job, property_types, found_date=found_date)


def build_job_title(job: JobRecord) -> dict:
    return _build_job_title(job)


def build_children(job: JobRecord) -> list[dict]:
    return _build_children(job)


def rich_text_property(value: str) -> dict:
    return _rich_text_property(value)


def notion_language_name(value: str) -> str:
    return _notion_language_name(value)


def notion_page_status(page: dict) -> str:
    return _notion_page_status(page)


def notion_page_job_title_and_url(page: dict) -> tuple[str, str]:
    return _notion_page_job_title_and_url(page)


def notion_page_company_name(page: dict) -> str:
    return _notion_page_company_name(page)


def is_processed_application_status(value: str) -> bool:
    return normalize_status_name(value) in PROCESSED_APPLICATION_STATUSES


def na_value(value: str | None) -> str:
    cleaned = " ".join((value or "").split())
    return cleaned if cleaned else "N/A"


def render_location(job: JobRecord) -> str:
    return _render_location(job)


def location_options(job: JobRecord) -> list[str]:
    return _location_options(job)


def merge_daily_job(accepted_jobs: dict[str, AcceptedJob], job: JobRecord, job_id: str) -> bool:
    return merge_accepted_job(accepted_jobs, job, job_id)


def daily_job_identity(job: JobRecord) -> str:
    return canonical_identity(job)


def add_source_provenance(target: JobRecord, incoming: JobRecord) -> None:
    _add_source_provenance(target, incoming)


def accepted_database_ids(accepted: AcceptedJob) -> list[str]:
    return _accepted_database_ids(accepted)


def normalize_list(values: object) -> list[str]:
    return _normalize_list(values)


def unique_list(values: list[str]) -> list[str]:
    return _unique_list(values)


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
    dashboard.update(
        track_label,
        "Indeed",
        stage="Fetching",
        detail=detail,
    )


def determine_post_age_hours(
    database: Database,
    source_name: str,
    recent_post_age_hours: int,
    bootstrap_post_age_hours: int,
) -> int:
    if database.has_source_observations(source_name):
        return recent_post_age_hours
    return bootstrap_post_age_hours


def uses_first_seen_freshness(job: JobRecord) -> bool:
    freshness_basis = " ".join(
        str(job.raw_payload.get("freshness_basis", "")).strip().lower().split()
    )
    return freshness_basis == "first_seen"


def export_row_is_germany(row: ExportRow) -> bool:
    return _export_row_is_germany(row)


def export_row_matches_country(row: ExportRow, filters: FiltersConfig) -> bool:
    location_text = str(row["location_text"] or "")
    raw_payload_text = str(row["raw_payload_json"] or "")
    raw_country = ""
    search_location = ""
    if raw_payload_text:
        try:
            payload = json.loads(raw_payload_text)
        except json.JSONDecodeError:
            payload = {}
        raw_country = str(payload.get("location_country") or "").strip()
        search_location = str(payload.get("search_location") or "").strip()
    country = str(row["country_code"] or "")
    if looks_like_target_countries(
        location_text, country, filters.country, raw_country=raw_country
    ):
        return True
    return search_location_matches_target_country(search_location, country, filters)


def export_row_matches_track(row: ExportRow, filters: FiltersConfig) -> bool:
    return export_row_matches_policy(row, policy_from_legacy(filters))


if __name__ == "__main__":
    raise SystemExit(main())
