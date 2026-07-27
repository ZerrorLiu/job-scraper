from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from datetime import date, datetime
from threading import Lock
from typing import Protocol
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

from job_scraper.adapters.sinks.notion_payload import (
    build_children,
    build_daily_properties,
    notion_page_company_name,
    notion_page_job_title_and_url,
)
from job_scraper.domain.models import JobHistorySnapshot, JobRecord
from job_scraper.integrations.notion import NotionClient
from job_scraper.ports.sinks import PublishContext, PublishResult

_NOTION_PUBLISH_LOCK = Lock()


class AcceptedJob(Protocol):
    job: JobRecord
    job_id: str
    linked_job_ids: list[str]


class IdentifiableJob(Protocol):
    @property
    def title(self) -> str: ...

    @property
    def company_name(self) -> str: ...

    @property
    def application_url(self) -> str: ...

    @property
    def source_url(self) -> str: ...

    @property
    def canonical_url(self) -> str: ...


class NotionStateRepository(Protocol):
    def get_job_history(
        self,
        job_id: str,
        company_name: str,
        started_at: datetime,
    ) -> JobHistorySnapshot: ...

    def upsert_notion_state(
        self,
        job_id: str,
        page_id: str,
        data_source_id: str,
        payload_hash: str,
        status: str,
    ) -> None: ...


class NotionDailySink:
    sink_id = "notion_daily"

    def __init__(
        self,
        repository: NotionStateRepository,
        client: NotionClient,
        *,
        timezone_name: str,
        table_prefix: str,
        track_label: str,
        started_at: datetime,
        logger: Callable[[str], None] | None = None,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self._repository = repository
        self._client = client
        self._timezone_name = timezone_name
        self._table_prefix = table_prefix
        self._track_label = track_label
        self._started_at = started_at
        self._logger = logger or (lambda message: None)
        self._sleeper = sleeper

    def publish(
        self,
        jobs: Sequence[AcceptedJob],
        context: PublishContext,
    ) -> PublishResult:
        with _NOTION_PUBLISH_LOCK:
            return self._publish_locked(jobs, context)

    def _publish_locked(
        self,
        jobs: Sequence[AcceptedJob],
        context: PublishContext,
    ) -> PublishResult:
        del context
        if not self._client.enabled():
            self._logger("Notion | Skipped")
            return PublishResult(sink_id=self.sink_id)

        table_title = self._table_title()
        legacy_titles = [self._legacy_daily_table_title()]
        if self._table_prefix:
            legacy_titles.append(self._legacy_daily_table_title(prefix=""))
        parent_page_id = ""
        notion_config = getattr(self._client, "config", None)
        if notion_config is not None and not getattr(notion_config, "database_id", ""):
            parent_page_id = str(getattr(notion_config, "parent_page_id", ""))
        if parent_page_id:
            daily_database = self._client.ensure_daily_database(
                table_title,
                legacy_titles=legacy_titles,
                parent_page_id=parent_page_id,
                is_inline=False,
            )
        else:
            daily_database = self._client.ensure_daily_database(
                table_title,
                legacy_titles=legacy_titles,
            )
        data_source_id = daily_database["data_sources"][0]["id"]
        database_id = str(daily_database.get("id", "")).strip()
        local_date = self._local_date()
        if database_id:
            try:
                self._client.ensure_job_views(
                    database_id,
                    data_source_id,
                    local_date,
                )
            except RuntimeError as exc:
                self._logger(f"Notion | View setup skipped | {exc}")
        property_types = self._client.get_data_source_property_types(data_source_id)
        existing_pages = self._client.list_data_source_pages(data_source_id)
        allow_history_backfill = len(existing_pages) == 0
        existing_index = _existing_page_index(existing_pages)
        inserted = 0
        updated = 0
        skipped = 0
        self._logger(
            f"Notion | Track {self._track_label} | "
            f"Writing up to {len(jobs)} jobs into table {table_title}"
        )
        for accepted in jobs:
            job_ids = _accepted_database_ids(accepted)
            histories = [
                self._repository.get_job_history(
                    job_id,
                    accepted.job.company_name,
                    self._started_at,
                )
                for job_id in job_ids
            ]
            existing_page = _find_existing_page(existing_index, accepted.job)
            if existing_page is not None:
                properties = build_daily_properties(
                    accepted.job,
                    property_types,
                    found_date=_page_date(existing_page) or local_date,
                )
                properties = _merge_page_properties(existing_page, properties)
                page = self._client.create_or_update_page(
                    str(existing_page.get("id", "")),
                    properties,
                )
                self._sync_url_children(
                    str(page.get("id", existing_page.get("id", ""))), accepted.job
                )
                for job_id in job_ids:
                    self._repository.upsert_notion_state(
                        job_id,
                        str(page.get("id", existing_page.get("id", ""))),
                        data_source_id,
                        self._client.payload_hash(properties),
                        "synced",
                    )
                updated += 1
                continue
            if (
                any(
                    history.exact_seen_before and history.previous_notion_page_id
                    for history in histories
                )
                and not allow_history_backfill
            ):
                skipped += 1
                continue
            properties = build_daily_properties(
                accepted.job,
                property_types,
                found_date=local_date,
            )
            page = self._client.create_data_source_page(
                data_source_id,
                properties,
                build_children(accepted.job),
            )
            for job_id in job_ids:
                self._repository.upsert_notion_state(
                    job_id,
                    str(page.get("id", "")),
                    data_source_id,
                    self._client.payload_hash(properties),
                    "synced",
                )
            inserted += 1
            if inserted % 5 == 0 or inserted == len(jobs):
                self._logger(f"Notion | Progress {inserted}/{len(jobs)}")
            self._sleeper(0.35)
        self._logger(
            f"Notion | Done | Track {self._track_label} | "
            f"Inserted {inserted} rows into {table_title} | "
            f"Updated {updated} existing rows | "
            f"Skipped exact duplicates {skipped}"
        )
        return PublishResult(
            sink_id=self.sink_id,
            published=inserted + updated,
            skipped=skipped,
        )

    def _sync_url_children(self, page_id: str, job: JobRecord) -> None:
        if not page_id:
            return
        desired_blocks = {
            "Apply URL": build_children(job)[-2],
            "Job URL": build_children(job)[-1],
        }
        existing_blocks = self._client.list_child_blocks(page_id)
        found: set[str] = set()
        for block in existing_blocks:
            label = _url_block_label(block)
            if label not in desired_blocks:
                continue
            block_id = str(block.get("id", "")).strip()
            if not block_id:
                continue
            payload = desired_blocks[label]
            self._client.update_block(block_id, {"paragraph": payload["paragraph"]})
            found.add(label)
        missing = [desired_blocks[label] for label in desired_blocks if label not in found]
        if missing:
            self._client.append_child_blocks(page_id, missing)

    def _table_title(self, prefix: str | None = None) -> str:
        normalized_prefix = " ".join(
            (self._table_prefix if prefix is None else prefix).split()
        ).strip()
        if not normalized_prefix:
            normalized_prefix = self._track_label
        return f"{normalized_prefix} Jobs".strip()

    def _legacy_daily_table_title(self, prefix: str | None = None) -> str:
        normalized_prefix = " ".join(
            (self._table_prefix if prefix is None else prefix).split()
        ).strip()
        return f"{normalized_prefix} {self._local_date().isoformat()}".strip()

    def _local_date(self) -> date:
        return self._started_at.astimezone(ZoneInfo(self._timezone_name)).date()


def _accepted_database_ids(accepted: AcceptedJob) -> list[str]:
    values = [accepted.job_id, *accepted.linked_job_ids]
    return list(dict.fromkeys(value for value in values if value))


def _existing_page_index(pages: list[dict]) -> dict[str, dict]:
    index: dict[str, dict] = {}
    for page in pages:
        title, url = notion_page_job_title_and_url(page)
        company = notion_page_company_name(page)
        if url:
            index[f"url:{_normalized_match_url(url)}"] = page
        if title and company:
            index[f"text:{title.casefold()}|{company.casefold()}"] = page
    return index


def _find_existing_page(index: dict[str, dict], job: IdentifiableJob) -> dict | None:
    job_urls = [
        _normalized_match_url(url)
        for url in (job.application_url, job.source_url, job.canonical_url)
        if url and url.strip()
    ]
    for url in job_urls:
        if url and f"url:{url}" in index:
            return index[f"url:{url}"]
    text_match = index.get(f"text:{job.title.casefold()}|{job.company_name.casefold()}")
    if text_match is None or not job_urls:
        return text_match
    _existing_title, existing_url = notion_page_job_title_and_url(text_match)
    return text_match if not existing_url else None


def _normalized_match_url(url: str) -> str:
    value = url.strip()
    try:
        parts = urlsplit(value)
        query = urlencode(parse_qsl(parts.query, keep_blank_values=True), doseq=True)
        return urlunsplit(
            (
                parts.scheme.casefold(),
                parts.netloc.casefold(),
                parts.path,
                query,
                "",
            )
        )
    except ValueError:
        return value


def _merge_page_properties(page: dict, incoming: dict) -> dict:
    current = page.get("properties", {})
    merged = dict(incoming)
    for field_name in ("Source",):
        existing_values = [
            str(option.get("name", "")).strip()
            for option in (current.get(field_name, {}) or {}).get("multi_select", [])
            if str(option.get("name", "")).strip()
        ]
        incoming_values = [
            str(option.get("name", "")).strip()
            for option in (incoming.get(field_name, {}) or {}).get("multi_select", [])
            if str(option.get("name", "")).strip()
        ]
        values = list(dict.fromkeys([*existing_values, *incoming_values]))
        merged[field_name] = {"multi_select": [{"name": value} for value in values]}
    existing_status = (current.get("Status", {}) or {}).get("select")
    if existing_status:
        merged["Status"] = {"select": {"name": existing_status.get("name", "Not Applied")}}
    return merged


def _page_date(page: dict) -> date | None:
    start = ((page.get("properties", {}).get("Date", {}) or {}).get("date") or {}).get("start")
    if not start:
        return None
    try:
        return date.fromisoformat(str(start)[:10])
    except ValueError:
        return None


def _url_block_label(block: dict) -> str:
    rich_text = (block.get("paragraph") or {}).get("rich_text") or []
    text = "".join(
        str(item.get("plain_text") or (item.get("text") or {}).get("content") or "")
        for item in rich_text
    )
    return text.split(":", 1)[0].strip() if ":" in text else ""
