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
from job_scraper.application.aggregation import AcceptedJob
from job_scraper.integrations.notion import (
    NotionClient,
    normalize_notion_id,
    normalize_status_name,
)
from job_scraper.ports.sinks import PublishContext
from job_scraper.storage.db import Database

PROCESSED_APPLICATION_STATUSES = frozenset({"applied", "not_interested"})


def publish_daily(
    database: Database,
    jobs: Sequence[AcceptedJob],
    notion: NotionClient,
    timezone_name: str,
    started_at: datetime,
    table_prefix: str,
    track_label: str,
    *,
    logger: Callable[[str], None] = lambda message: None,
    sleeper: Callable[[float], None] = time.sleep,
) -> None:
    sink = NotionDailySink(
        database,
        notion,
        timezone_name=timezone_name,
        table_prefix=table_prefix,
        track_label=track_label,
        started_at=started_at,
        logger=logger,
        sleeper=sleeper,
    )
    sink.publish(
        jobs,
        PublishContext(run_id="legacy", profile_id=track_label),
    )


def import_processed_statuses(
    database: Database,
    notion: NotionClient,
    *,
    table_title: str = "",
    error_logger: Callable[[str], None] = lambda message: None,
) -> int:
    try:
        data_source_ids = configured_data_source_ids(notion, table_title=table_title)
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
            if normalize_status_name(status) not in PROCESSED_APPLICATION_STATUSES:
                continue
            job_id = database.find_job_id_by_notion_page_id(str(page.get("id", "")))
            if not job_id:
                title, job_url = notion_page_job_title_and_url(page)
                company_name = notion_page_company_name(page)
                job_id = database.match_job_id_for_notion_page(title, company_name, job_url)
            if not job_id or database.get_application_status(job_id) == status:
                continue
            database.set_application_status(job_id, status, edited_at=_page_edited_at(page))
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
) -> list[str]:
    notion_config = getattr(notion, "config", None)
    if notion_config is not None and getattr(notion_config, "database_id", ""):
        return [notion.resolve_data_source_id()]

    data_source_ids: list[str] = []
    if notion_config is None:
        direct_blocks = notion.list_child_blocks(str(notion.ensure_container_page()["id"]))
    else:
        parent_page_id = normalize_notion_id(notion.config.parent_page_id)
        direct_blocks = notion.list_child_blocks(parent_page_id)
    candidate_blocks = [block for block in direct_blocks if block.get("type") == "child_database"]
    for page in (block for block in direct_blocks if block.get("type") == "child_page"):
        candidate_blocks.extend(
            block
            for block in notion.list_child_blocks(str(page.get("id", "")))
            if block.get("type") == "child_database"
        )
    for block in candidate_blocks:
        database_id = str(block.get("id", "")).strip()
        if database_id:
            daily_database = notion.request(
                f"https://api.notion.com/v1/databases/{database_id}", "GET"
            )
            current_title = "".join(
                str(piece.get("plain_text", ""))
                or str((piece.get("text", {}) or {}).get("content", ""))
                for piece in daily_database.get("title", [])
                if isinstance(piece, dict)
            ).strip()
            if table_title and current_title != table_title:
                continue
            data_sources = daily_database.get("data_sources", [])
            if data_sources:
                data_source_id = str(data_sources[0].get("id", "")).strip()
                if data_source_id:
                    data_source_ids.append(data_source_id)
    return data_source_ids
