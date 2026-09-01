from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from job_scraper.adapters.sinks.csv import DEFAULT_RETAINED_EXPORTS, CsvSink
from job_scraper.adapters.sinks.notion_daily import NotionDailySink
from job_scraper.adapters.sources.brightdata.indeed import BrightDataIndeedSource
from job_scraper.adapters.sources.direct.arbeitnow import ArbeitnowDirectSource
from job_scraper.adapters.sources.direct.arbeitsagentur import ArbeitsagenturDirectSource
from job_scraper.adapters.sources.direct.ats import AtsDirectSource
from job_scraper.adapters.sources.direct.berlinstartupjobs import BerlinStartupJobsDirectSource
from job_scraper.adapters.sources.direct.linkedin import LinkedInDirectSource
from job_scraper.adapters.sources.direct.workable import WorkableDirectSource
from job_scraper.adapters.sources.email.imap import ImapEmailChannel
from job_scraper.adapters.storage.notion_bindings import NotionDatabaseBindingStore
from job_scraper.application.acquisition import RequestCoalescer, RequestGate
from job_scraper.collectors.linkedin import LinkedInProgressEvent
from job_scraper.config import HttpConfig, SourceConfig
from job_scraper.domain.policies import FilterPolicy
from job_scraper.integrations.email_recommendations import EmailIngestConfig
from job_scraper.integrations.notion import NotionClient
from job_scraper.pipeline.engine import CandidatePipeline
from job_scraper.ports.channels import JobChannel
from job_scraper.ports.sinks import JobSink
from job_scraper.ports.sources import JobSource
from job_scraper.registry.core import ComponentRegistry
from job_scraper.storage.db import Database


@dataclass(frozen=True, slots=True)
class SourceBuildRequest:
    """Everything a source adapter may be built with.

    These were previously passed as `services: dict[str, object]` and recovered
    with `.get()` plus a hand-written isinstance check per entry. Declaring them
    lets the type checker do that work: a misspelled key can no longer silently
    mean "no rate limiter", and the factories stop paying for `cast` on the way
    out. The composition root is the right place to know these concrete types.
    """

    http: HttpConfig
    settings: SourceConfig
    company_names: tuple[str, ...] = ()
    request_coalescer: RequestCoalescer | None = None
    request_gate: RequestGate | None = None
    snapshot_database: Database | None = None
    event_logger: Callable[[str], None] | None = None
    progress_callback: Callable[[LinkedInProgressEvent], None] | None = None


@dataclass(frozen=True, slots=True)
class ChannelBuildRequest:
    http: HttpConfig
    settings: EmailIngestConfig


@dataclass(frozen=True, slots=True)
class SinkBuildRequest:
    repository: Database
    policy: FilterPolicy
    started_at: datetime
    profile_id: str
    profile_label: str
    timezone_name: str
    csv_destination: Path | None = None
    retained_exports: int = DEFAULT_RETAINED_EXPORTS
    notion_client: NotionClient | None = None
    notion_table_prefix: str = ""
    notion_binding_store: NotionDatabaseBindingStore | None = None
    logger: Callable[[str], None] | None = None


def create_builtin_registry() -> ComponentRegistry:
    registry = ComponentRegistry()
    registry.register_source("linkedin_direct", _build_linkedin)
    registry.register_source("indeed_brightdata", _build_indeed)
    registry.register_source("ats_direct", _build_ats_direct)
    registry.register_source("arbeitsagentur_direct", _build_arbeitsagentur)
    registry.register_source("workable_direct", _build_workable)
    registry.register_source("arbeitnow_direct", _build_arbeitnow)
    registry.register_source("berlinstartupjobs_direct", _build_berlinstartupjobs)
    registry.register_channel("email_imap", _build_email)
    registry.register_sink("csv", _build_csv_sink)
    registry.register_sink("notion_daily", _build_notion_sink)
    return registry


def build_source(
    registry: ComponentRegistry,
    source_id: str,
    request: SourceBuildRequest,
) -> JobSource:
    return registry.sources.create(source_id, request)


def build_channel(
    registry: ComponentRegistry,
    channel_id: str,
    request: ChannelBuildRequest,
) -> JobChannel:
    return registry.channels.create(channel_id, request)


def build_pipeline(
    registry: ComponentRegistry,
    step_ids: tuple[str, ...],
) -> CandidatePipeline:
    return CandidatePipeline([registry.steps.create(step_id) for step_id in step_ids])


def build_sink(
    registry: ComponentRegistry,
    sink_id: str,
    request: SinkBuildRequest,
) -> JobSink:
    return registry.sinks.create(sink_id, request)


def validate_component_ids(
    registry: ComponentRegistry,
    *,
    sources: tuple[str, ...],
    channels: tuple[str, ...],
    steps: tuple[str, ...],
    sinks: tuple[str, ...],
) -> None:
    _validate_ids("source", sources, registry.sources.available())
    _validate_ids("channel", channels, registry.channels.available())
    _validate_ids("step", steps, registry.steps.available())
    _validate_ids("sink", sinks, registry.sinks.available())


def _build_linkedin(request: SourceBuildRequest) -> LinkedInDirectSource:
    return LinkedInDirectSource(
        request.http,
        request.settings,
        list(request.company_names),
        request_coalescer=request.request_coalescer,
        request_gate=request.request_gate,
        event_logger=request.event_logger,
        progress_callback=request.progress_callback,
    )


def _build_indeed(request: SourceBuildRequest) -> BrightDataIndeedSource:
    if request.snapshot_database is None:
        # Resuming a paid snapshot across runs is not optional for this source:
        # with nowhere to record it, a crashed run silently re-triggers it.
        raise ValueError("indeed_brightdata requires a snapshot database")
    return BrightDataIndeedSource(
        request.http,
        request.settings,
        snapshot_database=request.snapshot_database,
        event_logger=request.event_logger,
    )


def _build_ats_direct(request: SourceBuildRequest) -> AtsDirectSource:
    return AtsDirectSource(request.http, request.settings, event_logger=request.event_logger)


def _build_arbeitsagentur(request: SourceBuildRequest) -> ArbeitsagenturDirectSource:
    return ArbeitsagenturDirectSource(
        request.http, request.settings, event_logger=request.event_logger
    )


def _build_workable(request: SourceBuildRequest) -> WorkableDirectSource:
    return WorkableDirectSource(request.http, request.settings, event_logger=request.event_logger)


def _build_arbeitnow(request: SourceBuildRequest) -> ArbeitnowDirectSource:
    return ArbeitnowDirectSource(request.http, request.settings, event_logger=request.event_logger)


def _build_berlinstartupjobs(request: SourceBuildRequest) -> BerlinStartupJobsDirectSource:
    return BerlinStartupJobsDirectSource(
        request.http, request.settings, event_logger=request.event_logger
    )


def _build_email(request: ChannelBuildRequest) -> ImapEmailChannel:
    return ImapEmailChannel(request.settings, request.http)


def _build_csv_sink(request: SinkBuildRequest) -> CsvSink:
    if request.csv_destination is None:
        raise ValueError("csv sink requires a destination")
    return CsvSink(
        request.repository,
        request.csv_destination,
        request.policy,
        retained_exports=request.retained_exports,
    )


def _build_notion_sink(request: SinkBuildRequest) -> NotionDailySink:
    if request.notion_client is None:
        raise ValueError("notion_daily sink requires a Notion client")
    return NotionDailySink(
        request.repository,
        request.notion_client,
        timezone_name=request.timezone_name,
        table_prefix=request.notion_table_prefix,
        track_label=request.profile_label,
        started_at=request.started_at,
        logger=request.logger,
        binding_store=request.notion_binding_store,
        profile_id=request.profile_id,
    )


def _validate_ids(
    kind: str,
    configured: tuple[str, ...],
    available: tuple[str, ...],
) -> None:
    unknown = sorted(set(configured) - set(available))
    if unknown:
        raise ValueError(
            f"Unknown {kind} ids: {', '.join(unknown)}; "
            f"available: {', '.join(available) or '(none)'}"
        )
