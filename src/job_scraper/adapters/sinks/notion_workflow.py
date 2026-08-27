from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from datetime import UTC, datetime

from job_scraper.adapters.sinks.notion_daily import NotionDailySink
from job_scraper.adapters.sinks.notion_payload import (
    notion_page_company_name,
    notion_page_job_title_and_url,
    notion_page_status,
)
from job_scraper.adapters.storage.notion_bindings import (
    NotionDatabaseBinding,
    NotionDatabaseBindingStore,
)
from job_scraper.application.aggregation import AcceptedJob
from job_scraper.integrations.notion import (
    NotionClient,
    normalize_status_name,
)
from job_scraper.ports.sinks import PublishContext, PublishResult
from job_scraper.storage.db import Database

IMPORTED_APPLICATION_STATUSES = frozenset({"new", "applied", "not_interested"})


def publish_daily(
    database: Database,
    jobs: Sequence[AcceptedJob],
    notion: NotionClient,
    timezone_name: str,
    started_at: datetime,
    table_prefix: str,
    track_label: str,
    profile_id: str,
    binding_store: NotionDatabaseBindingStore | None = None,
    *,
    logger: Callable[[str], None] = lambda message: None,
    sleeper: Callable[[float], None] = time.sleep,
) -> PublishResult:
    sink = NotionDailySink(
        database,
        notion,
        timezone_name=timezone_name,
        table_prefix=table_prefix,
        track_label=track_label,
        started_at=started_at,
        logger=logger,
        sleeper=sleeper,
        binding_store=binding_store,
        profile_id=profile_id,
    )
    return sink.publish(
        jobs,
        PublishContext(run_id="legacy", profile_id=track_label),
    )


def import_processed_statuses(
    database: Database,
    notion: NotionClient,
    *,
    table_title: str = "",
    binding_store: NotionDatabaseBindingStore | None = None,
    profile_id: str = "",
    error_logger: Callable[[str], None] = lambda message: None,
) -> int:
    try:
        data_source_ids = configured_data_source_ids(
            notion,
            table_title=table_title,
            binding_store=binding_store,
            profile_id=profile_id,
        )
    except RuntimeError as exc:
        error_logger(f"Notion | Status import skipped | {exc}")
        return 0
    imported = 0
    for data_source_id in data_source_ids:
        try:
            pages = notion.list_data_source_pages(data_source_id)
        except RuntimeError as exc:
            error_logger(f"Notion | Status import skipped for data source {data_source_id} | {exc}")
            continue
        for page in pages:
            status = notion_page_status(page)
            normalized_status = normalize_status_name(status)
            if normalized_status not in IMPORTED_APPLICATION_STATUSES:
                continue
            job_id = database.find_job_id_by_notion_page_id(str(page.get("id", "")))
            if not job_id:
                title, job_url = notion_page_job_title_and_url(page)
                company_name = notion_page_company_name(page)
                job_id = database.match_job_id_for_notion_page(title, company_name, job_url)
            if not job_id or database.get_application_status(job_id) == normalized_status:
                continue
            database.set_application_status(
                job_id, normalized_status, edited_at=_page_edited_at(page)
            )
            imported += 1
    return imported


def _page_edited_at(page: dict) -> datetime | None:
    value = str(page.get("last_edited_time", "") or "").strip()
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return None


def configured_data_source_ids(
    notion: NotionClient,
    *,
    table_title: str = "",
    binding_store: NotionDatabaseBindingStore | None = None,
    profile_id: str = "",
) -> list[str]:
    notion_config = getattr(notion, "config", None)
    bound = binding_store.load(profile_id) if binding_store is not None and profile_id else None
    if binding_store is None and bound is None and getattr(notion_config, "database_id", ""):
        return [notion.resolve_data_source_id()]
    parent_page_id = str(getattr(notion_config, "parent_page_id", ""))
    daily_database = notion.find_daily_database(
        table_title,
        bound_database_id=bound.database_id if bound is not None else "",
        parent_page_id=parent_page_id,
        is_inline=False,
    )
    if daily_database is None:
        return []
    resolved = NotionDatabaseBinding.from_database(daily_database)
    if (
        binding_store is not None
        and profile_id
        and (bound is None or bound.database_id != resolved.database_id)
    ):
        binding_store.save(profile_id, resolved)
    return [resolved.data_source_id]
