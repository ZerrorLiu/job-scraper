from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import cast

from job_scraper.adapters.sinks.csv import CsvSink
from job_scraper.adapters.sinks.notion_daily import NotionDailySink
from job_scraper.adapters.sources.brightdata.indeed import BrightDataIndeedSource
from job_scraper.adapters.sources.direct.linkedin import LinkedInDirectSource
from job_scraper.adapters.sources.email.imap import ImapEmailChannel
from job_scraper.application.acquisition import RequestCoalescer, RequestGate
from job_scraper.config import HttpConfig, SourceConfig
from job_scraper.domain.policies import FilterPolicy
from job_scraper.integrations.email_recommendations import EmailIngestConfig
from job_scraper.integrations.notion import NotionClient
from job_scraper.pipeline.engine import CandidatePipeline
from job_scraper.pipeline.steps import (
    CompanyStep,
    CountryStep,
    EmploymentScopeStep,
    ExcludedTermsStep,
    FreshnessStep,
    LanguageStep,
    RequirementExclusionStep,
    RoleStep,
)
from job_scraper.ports.channels import JobChannel
from job_scraper.ports.sinks import JobSink
from job_scraper.ports.sources import JobSource
from job_scraper.registry.core import ComponentRegistry
from job_scraper.storage.db import Database


@dataclass(frozen=True, slots=True)
class SourceBuildRequest:
    http: HttpConfig
    settings: SourceConfig
    company_names: tuple[str, ...] = ()
    services: dict[str, object] = field(default_factory=dict)


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
    notion_client: NotionClient | None = None
    notion_table_prefix: str = ""
    services: dict[str, object] = field(default_factory=dict)


def create_builtin_registry() -> ComponentRegistry:
    registry = ComponentRegistry()
    registry.register_source("linkedin_direct", _build_linkedin)
    registry.register_source("indeed_brightdata", _build_indeed)
    registry.register_channel("email_imap", _build_email)
    registry.register_step("country", CountryStep)
    registry.register_step("freshness", FreshnessStep)
    registry.register_step("company", CompanyStep)
    registry.register_step("employment_scope", EmploymentScopeStep)
    registry.register_step("excluded_terms", ExcludedTermsStep)
    registry.register_step("role", RoleStep)
    registry.register_step("requirement_exclusion", RequirementExclusionStep)
    registry.register_step("language", LanguageStep)
    registry.register_sink("csv", _build_csv_sink)
    registry.register_sink("notion_daily", _build_notion_sink)
    return registry


def build_source(
    registry: ComponentRegistry,
    source_id: str,
    request: SourceBuildRequest,
) -> JobSource:
    return cast(JobSource, registry.sources.create(source_id, request))


def build_channel(
    registry: ComponentRegistry,
    channel_id: str,
    request: ChannelBuildRequest,
) -> JobChannel:
    return cast(JobChannel, registry.channels.create(channel_id, request))


def build_pipeline(
    registry: ComponentRegistry,
    step_ids: tuple[str, ...],
) -> CandidatePipeline:
    steps = [registry.steps.create(step_id) for step_id in step_ids]
    return CandidatePipeline(cast(list, steps))


def build_sink(
    registry: ComponentRegistry,
    sink_id: str,
    request: SinkBuildRequest,
) -> JobSink:
    return cast(JobSink, registry.sinks.create(sink_id, request))


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
    request_coalescer = request.services.get("request_coalescer")
    if request_coalescer is not None and not isinstance(request_coalescer, RequestCoalescer):
        raise TypeError("linkedin_direct request_coalescer service has the wrong type")
    request_gate = request.services.get("request_gate")
    if request_gate is not None and not isinstance(request_gate, RequestGate):
        raise TypeError("linkedin_direct request_gate service has the wrong type")
    event_logger = request.services.get("event_logger")
    if event_logger is not None and not callable(event_logger):
        raise TypeError("linkedin_direct event_logger service must be callable")
    progress_callback = request.services.get("progress_callback")
    if progress_callback is not None and not callable(progress_callback):
        raise TypeError("linkedin_direct progress_callback service must be callable")
    return LinkedInDirectSource(
        request.http,
        request.settings,
        list(request.company_names),
        request_coalescer=request_coalescer,
        request_gate=request_gate,
        event_logger=cast(Callable[[str], None] | None, event_logger),
        progress_callback=cast(Callable | None, progress_callback),
    )


def _build_indeed(request: SourceBuildRequest) -> BrightDataIndeedSource:
    snapshot_database = request.services.get("snapshot_database")
    if not isinstance(snapshot_database, Database):
        raise TypeError("indeed_brightdata requires a Database snapshot service")
    event_logger = request.services.get("event_logger")
    if event_logger is not None and not callable(event_logger):
        raise TypeError("indeed_brightdata event_logger service must be callable")
    return BrightDataIndeedSource(
        request.http,
        request.settings,
        snapshot_database=snapshot_database,
        event_logger=cast(Callable[[str], None] | None, event_logger),
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
    )


def _build_notion_sink(request: SinkBuildRequest) -> NotionDailySink:
    if request.notion_client is None:
        raise ValueError("notion_daily sink requires a Notion client")
    logger = request.services.get("logger")
    if logger is not None and not callable(logger):
        raise TypeError("notion_daily logger service must be callable")
    return NotionDailySink(
        request.repository,
        request.notion_client,
        timezone_name=request.timezone_name,
        table_prefix=request.notion_table_prefix,
        track_label=request.profile_label,
        started_at=request.started_at,
        logger=cast(Callable[[str], None] | None, logger),
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
