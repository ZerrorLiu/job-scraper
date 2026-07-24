from __future__ import annotations

import json
import os
import re
import time
from dataclasses import replace
from datetime import date
from hashlib import sha256
from http.client import RemoteDisconnected
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from job_scraper.config import NotionConfig


class NotionClient:
    def __init__(self, config: NotionConfig) -> None:
        token = environment_credential("NOTION_INTEGRATION_TOKEN") or config.token
        database_id = environment_credential("NOTION_DATABASE_ID") or config.database_id
        self.config = replace(
            config,
            token=token,
            database_id=database_id,
        )
        self._resolved_data_source_id = ""

    def enabled(self) -> bool:
        target_is_configured = bool(
            self.config.database_id or self.config.data_source_id or self.config.parent_page_id
        )
        return self.config.enabled and bool(self.config.token) and target_is_configured

    def payload_hash(self, properties: dict[str, Any]) -> str:
        encoded = json.dumps(properties, sort_keys=True, ensure_ascii=True).encode("utf-8")
        return sha256(encoded).hexdigest()

    def build_properties(self, row: dict[str, Any]) -> dict[str, Any]:
        properties = {
            "Status": {
                "select": {"name": notion_status_name(row.get("application_status", "new"))}
            },
            "Job": {"title": [{"text": {"content": row["normalized_title"][:200]}}]},
            "Company": {"rich_text": [{"text": {"content": row["company_name"][:200]}}]},
            "Location": {"rich_text": [{"text": {"content": (row["city"] or "N/A")[:200]}}]},
            "Language": {"select": {"name": row.get("description_language") or "Unknown"}},
            "Source": {
                "multi_select": [{"name": notion_source_name(str(row.get("source") or "unknown"))}]
            },
        }
        return properties

    def create_or_update_page(
        self, page_id: str | None, properties: dict[str, Any]
    ) -> dict[str, Any]:
        if page_id:
            endpoint = f"https://api.notion.com/v1/pages/{page_id}"
            body = {"properties": properties}
            method = "PATCH"
        else:
            endpoint = "https://api.notion.com/v1/pages"
            body = {
                "parent": {"data_source_id": self.resolve_data_source_id()},
                "properties": properties,
            }
            method = "POST"

        return self.request(endpoint, method, body)

    def ensure_daily_database(
        self,
        table_title: str,
        legacy_titles: list[str] | None = None,
        *,
        parent_page_id: str = "",
        is_inline: bool = True,
    ) -> dict[str, Any]:
        if self.config.database_id:
            database_id = normalize_notion_id(self.config.database_id)
            database = self.request(
                f"https://api.notion.com/v1/databases/{database_id}",
                "GET",
            )
            data_sources = database.get("data_sources", [])
            if not data_sources:
                raise RuntimeError("Configured Notion database has no data source")
            self._resolved_data_source_id = str(data_sources[0].get("id", "")).strip()
            if not self._resolved_data_source_id:
                raise RuntimeError("Configured Notion database returned an empty data source ID")
            if notion_title_text(database.get("title", [])) != table_title:
                self.update_database_title(database_id, table_title)
            self.sync_daily_schema(self._resolved_data_source_id)
            return database

        container_page_id = (
            normalize_notion_id(parent_page_id)
            if parent_page_id
            else str(self.ensure_container_page()["id"])
        )
        legacy_titles = [title for title in (legacy_titles or []) if title and title != table_title]
        direct_blocks = self.list_child_blocks(container_page_id)
        candidate_blocks = [
            block for block in direct_blocks if block.get("type") == "child_database"
        ]
        if not is_inline:
            for page in (block for block in direct_blocks if block.get("type") == "child_page"):
                candidate_blocks.extend(
                    block
                    for block in self.list_child_blocks(str(page.get("id", "")))
                    if block.get("type") == "child_database"
                )
        for block in candidate_blocks:
            if block.get("type") != "child_database":
                continue
            database_title = block.get("child_database", {}).get("title")
            if database_title == table_title:
                if parent_page_id:
                    self.move_database(
                        str(block["id"]),
                        container_page_id,
                        is_inline=is_inline,
                    )
                database = self.request(f"https://api.notion.com/v1/databases/{block['id']}", "GET")
                self.sync_daily_schema(database["data_sources"][0]["id"])
                return database
            if database_title in legacy_titles:
                self.update_database_title(str(block["id"]), table_title)
                if parent_page_id:
                    self.move_database(
                        str(block["id"]),
                        container_page_id,
                        is_inline=is_inline,
                    )
                database = self.request(f"https://api.notion.com/v1/databases/{block['id']}", "GET")
                self.sync_daily_schema(database["data_sources"][0]["id"])
                return database
        database = self.request(
            "https://api.notion.com/v1/databases",
            "POST",
            {
                "parent": {"type": "page_id", "page_id": container_page_id},
                "is_inline": is_inline,
                "title": [{"type": "text", "text": {"content": table_title}}],
                "initial_data_source": {
                    "title": [{"type": "text", "text": {"content": "Jobs"}}],
                    "properties": {
                        "Status": {
                            "select": {
                                "options": [
                                    {"name": "Not Applied", "color": "gray"},
                                    {"name": "Applied", "color": "green"},
                                    {"name": "Not Interested", "color": "red"},
                                ]
                            }
                        },
                        "Job": {"title": {}},
                        "Company": {"rich_text": {}},
                        "Location": {"rich_text": {}},
                        "Source": {
                            "multi_select": {
                                "options": [
                                    {"name": "LinkedIn", "color": "blue"},
                                    {"name": "Indeed", "color": "purple"},
                                    {"name": "Email", "color": "orange"},
                                ]
                            }
                        },
                        "Date": {"date": {}},
                        "Language": {
                            "select": {
                                "options": [
                                    {"name": "English", "color": "green"},
                                    {"name": "German", "color": "yellow"},
                                    {"name": "Mixed", "color": "orange"},
                                    {"name": "Unknown", "color": "gray"},
                                    {"name": "N/A", "color": "default"},
                                ]
                            }
                        },
                    },
                },
            },
        )
        return database

    def resolve_data_source_id(self) -> str:
        if self._resolved_data_source_id:
            return self._resolved_data_source_id
        if self.config.data_source_id:
            self._resolved_data_source_id = self.config.data_source_id
            return self._resolved_data_source_id
        if not self.config.database_id:
            raise RuntimeError("NOTION_DATABASE_ID is not configured")
        database = self.request(
            f"https://api.notion.com/v1/databases/{normalize_notion_id(self.config.database_id)}",
            "GET",
        )
        data_sources = database.get("data_sources", [])
        if not data_sources:
            raise RuntimeError("Configured Notion database has no data source")
        self._resolved_data_source_id = str(data_sources[0].get("id", "")).strip()
        if not self._resolved_data_source_id:
            raise RuntimeError("Configured Notion database returned an empty data source ID")
        return self._resolved_data_source_id

    def update_database_title(self, database_id: str, table_title: str) -> dict[str, Any]:
        return self.request(
            f"https://api.notion.com/v1/databases/{database_id}",
            "PATCH",
            {"title": [{"type": "text", "text": {"content": table_title}}]},
        )

    def sync_daily_schema(self, data_source_id: str) -> dict[str, Any]:
        try:
            current_data_source = self.request(
                f"https://api.notion.com/v1/data_sources/{data_source_id}",
                "GET",
            )
        except RuntimeError:
            current_data_source = {}
        current_properties = current_data_source.get("properties", {})
        legacy_statuses = self._legacy_page_statuses(data_source_id)
        properties: dict[str, Any] = {
            "Status": {
                "select": {
                    "options": [
                        {"name": "Not Applied", "color": "gray"},
                        {"name": "Applied", "color": "green"},
                        {"name": "Not Interested", "color": "red"},
                    ]
                }
            },
            "Date": {"date": {}},
            "Language": {
                "select": {
                    "options": [
                        {"name": "English", "color": "green"},
                        {"name": "German", "color": "yellow"},
                        {"name": "Mixed", "color": "orange"},
                        {"name": "Unknown", "color": "gray"},
                        {"name": "N/A", "color": "default"},
                    ]
                }
            },
        }
        if "Source" not in current_properties:
            properties["Source"] = {
                "multi_select": {
                    "options": [
                        {"name": "LinkedIn", "color": "blue"},
                        {"name": "Indeed", "color": "purple"},
                        {"name": "Email", "color": "orange"},
                    ]
                }
            }
        for legacy_property in ("History", "Salary", "Topic", "Track", "Tags", "Score"):
            if legacy_property in current_properties:
                properties[legacy_property] = None
        body = {"properties": properties}
        try:
            response = self.request(
                f"https://api.notion.com/v1/data_sources/{data_source_id}", "PATCH", body
            )
        except RuntimeError:
            return {}
        for page_id, status_name in legacy_statuses:
            try:
                self.request(
                    f"https://api.notion.com/v1/pages/{page_id}",
                    "PATCH",
                    {"properties": {"Status": {"select": {"name": status_name}}}},
                )
            except RuntimeError:
                continue
        return response

    def _legacy_page_statuses(self, data_source_id: str) -> list[tuple[str, str]]:
        try:
            pages = self.list_data_source_pages(data_source_id)
        except RuntimeError:
            return []
        statuses: list[tuple[str, str]] = []
        current_names = {"Not Applied", "Applied", "Not Interested"}
        for page in pages:
            status_property = page.get("properties", {}).get("Status", {}) or {}
            raw_name = (status_property.get("select") or {}).get("name", "") or (
                status_property.get("status") or {}
            ).get("name", "")
            if not raw_name or raw_name in current_names:
                continue
            page_id = str(page.get("id", "")).strip()
            if page_id:
                statuses.append((page_id, notion_status_name(raw_name)))
        return statuses

    def ensure_job_views(
        self,
        database_id: str,
        data_source_id: str,
        local_date: date,
    ) -> dict[str, dict[str, Any]]:
        date_text = str(local_date)
        table_configuration = self._job_table_configuration(data_source_id)
        today_filter = {
            "property": "Date",
            "date": {"equals": date_text},
        }
        sorts = [
            {"property": "Date", "direction": "descending"},
            {"property": "Company", "direction": "ascending"},
        ]
        quick_filters = {
            "Status": {"select": {"is_not_empty": True}},
            "Source": {"multi_select": {"is_not_empty": True}},
        }
        references = self.list_views(database_id=database_id)
        views = [self.retrieve_view(str(reference.get("id", ""))) for reference in references]
        today = next((view for view in views if view.get("name") == "Today"), None)
        if today is None:
            today = next(
                (
                    view
                    for view in views
                    if view.get("type") == "table" and view.get("name") != "All"
                ),
                None,
            )
        if today is not None:
            today = self.update_view(
                str(today["id"]),
                {
                    "name": "Today",
                    "filter": today_filter,
                    "sorts": sorts,
                    "quick_filters": quick_filters,
                    "configuration": table_configuration,
                    "position": {"type": "start"},
                },
            )
        else:
            today = self.create_view(
                {
                    "database_id": database_id,
                    "data_source_id": data_source_id,
                    "name": "Today",
                    "type": "table",
                    "filter": today_filter,
                    "sorts": sorts,
                    "quick_filters": quick_filters,
                    "configuration": table_configuration,
                    "position": {"type": "start"},
                }
            )

        obsolete_view = next(
            (view for view in views if view.get("name") == "Not Interested"),
            None,
        )
        if obsolete_view is not None:
            self.delete_view(str(obsolete_view["id"]))

        result: dict[str, dict[str, Any]] = {"today": today}
        for status_name in ("Not Applied", "Applied"):
            status_view = next(
                (view for view in views if view.get("name") == status_name),
                None,
            )
            body = {
                "name": status_name,
                "filter": {
                    "property": "Status",
                    "select": {"equals": status_name},
                },
                "sorts": sorts,
                "quick_filters": quick_filters,
                "configuration": table_configuration,
            }
            if status_view is None:
                status_view = self.create_view(
                    {
                        "database_id": database_id,
                        "data_source_id": data_source_id,
                        "type": "table",
                        "position": {"type": "end"},
                        **body,
                    }
                )
            else:
                status_view = self.update_view(str(status_view["id"]), body)
            result[f"status:{status_name}"] = status_view

        all_view = next((view for view in views if view.get("name") == "All"), None)
        if all_view is not None:
            all_view = self.update_view(
                str(all_view["id"]),
                {
                    "name": "All",
                    "filter": None,
                    "sorts": sorts,
                    "quick_filters": quick_filters,
                    "configuration": table_configuration,
                    "position": {"type": "end"},
                },
            )
        else:
            all_view = self.create_view(
                {
                    "database_id": database_id,
                    "data_source_id": data_source_id,
                    "name": "All",
                    "type": "table",
                    "sorts": sorts,
                    "quick_filters": quick_filters,
                    "configuration": table_configuration,
                    "position": {"type": "end"},
                }
            )
        result["all"] = all_view
        return result

    def _job_table_configuration(self, data_source_id: str) -> dict[str, Any]:
        data_source = self.request(
            f"https://api.notion.com/v1/data_sources/{data_source_id}",
            "GET",
        )
        properties = data_source.get("properties", {})
        widths = {
            "Job": 360,
            "Company": 180,
            "Location": 160,
            "Status": 130,
            "Date": 110,
            "Source": 140,
            "Language": 120,
        }
        visible_properties = [
            {
                "property_id": str(properties[name]["id"]),
                "visible": True,
                "width": width,
            }
            for name, width in widths.items()
            if name in properties and properties[name].get("id")
        ]
        return {
            "type": "table",
            "properties": visible_properties,
            "wrap_cells": False,
            "frozen_column_index": 1,
            "show_vertical_lines": True,
        }

    def list_views(
        self,
        *,
        database_id: str = "",
        data_source_id: str = "",
    ) -> list[dict[str, Any]]:
        if not database_id and not data_source_id:
            raise ValueError("database_id or data_source_id is required")
        query = urlencode(
            {"database_id": database_id} if database_id else {"data_source_id": data_source_id}
        )
        results: list[dict[str, Any]] = []
        cursor = ""
        while True:
            endpoint = f"https://api.notion.com/v1/views?{query}"
            if cursor:
                endpoint = f"{endpoint}&start_cursor={cursor}"
            response = self.request(endpoint, "GET")
            results.extend(response.get("results", []))
            if not response.get("has_more"):
                return results
            cursor = str(response.get("next_cursor", "") or "")

    def retrieve_view(self, view_id: str) -> dict[str, Any]:
        return self.request(f"https://api.notion.com/v1/views/{view_id}", "GET")

    def create_view(self, body: dict[str, Any]) -> dict[str, Any]:
        return self.request("https://api.notion.com/v1/views", "POST", body)

    def update_view(self, view_id: str, body: dict[str, Any]) -> dict[str, Any]:
        return self.request(f"https://api.notion.com/v1/views/{view_id}", "PATCH", body)

    def delete_view(self, view_id: str) -> dict[str, Any]:
        return self.request(f"https://api.notion.com/v1/views/{view_id}", "DELETE")

    def create_data_source_page(
        self, data_source_id: str, properties: dict[str, Any], children: list[dict] | None = None
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "parent": {"data_source_id": data_source_id},
            "properties": properties,
        }
        if children:
            body["children"] = children
        return self.request("https://api.notion.com/v1/pages", "POST", body)

    def trash_page(self, page_id: str) -> dict[str, Any]:
        return self.request(
            f"https://api.notion.com/v1/pages/{page_id}", "PATCH", {"in_trash": True}
        )

    def trash_block(self, block_id: str) -> dict[str, Any]:
        return self.request(
            f"https://api.notion.com/v1/blocks/{block_id}", "PATCH", {"in_trash": True}
        )

    def get_data_source_property_types(self, data_source_id: str) -> dict[str, str]:
        response = self.request(f"https://api.notion.com/v1/data_sources/{data_source_id}", "GET")
        return {name: prop.get("type", "") for name, prop in response.get("properties", {}).items()}

    def list_data_source_pages(
        self, data_source_id: str, page_size: int = 100
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        cursor = ""
        while True:
            body: dict[str, Any] = {"page_size": page_size}
            if cursor:
                body["start_cursor"] = cursor
            response = self.request(
                f"https://api.notion.com/v1/data_sources/{data_source_id}/query", "POST", body
            )
            results.extend(response.get("results", []))
            if not response.get("has_more"):
                return results
            cursor = str(response.get("next_cursor", "") or "")

    def list_child_blocks(self, page_id: str) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        cursor = ""
        while True:
            endpoint = f"https://api.notion.com/v1/blocks/{page_id}/children?page_size=100"
            if cursor:
                endpoint = f"{endpoint}&start_cursor={cursor}"
            response = self.request(endpoint, "GET")
            results.extend(response.get("results", []))
            if not response.get("has_more"):
                return results
            cursor = str(response.get("next_cursor", "") or "")

    def append_child_blocks(
        self,
        block_id: str,
        children: list[dict[str, Any]],
        position: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"children": children}
        if position:
            body["position"] = position
        return self.request(f"https://api.notion.com/v1/blocks/{block_id}/children", "PATCH", body)

    def ensure_container_page(self) -> dict[str, Any]:
        parent_page_id = normalize_notion_id(self.config.parent_page_id)
        for block in self.list_child_blocks(parent_page_id):
            if (
                block.get("type") == "child_page"
                and block.get("child_page", {}).get("title") == self.config.container_title
            ):
                return self.request(f"https://api.notion.com/v1/pages/{block['id']}", "GET")
        return self.request(
            "https://api.notion.com/v1/pages",
            "POST",
            {
                "parent": {"type": "page_id", "page_id": parent_page_id},
                "properties": {
                    "title": {
                        "title": [
                            {"type": "text", "text": {"content": self.config.container_title}}
                        ],
                    },
                },
                "children": [
                    {
                        "object": "block",
                        "type": "paragraph",
                        "paragraph": {
                            "rich_text": [
                                {
                                    "type": "text",
                                    "text": {
                                        "content": "Each run appends rows into the daily table. No Notion-side deduplication."
                                    },
                                }
                            ]
                        },
                    }
                ],
            },
        )

    def ensure_child_page(
        self,
        title: str,
        *,
        parent_page_id: str = "",
    ) -> dict[str, Any]:
        parent_id = normalize_notion_id(parent_page_id or self.config.parent_page_id)
        for block in self.list_child_blocks(parent_id):
            if (
                block.get("type") == "child_page"
                and block.get("child_page", {}).get("title") == title
            ):
                return self.request(f"https://api.notion.com/v1/pages/{block['id']}", "GET")
        return self.request(
            "https://api.notion.com/v1/pages",
            "POST",
            {
                "parent": {"type": "page_id", "page_id": parent_id},
                "properties": {
                    "title": {
                        "title": [{"type": "text", "text": {"content": title}}],
                    },
                },
            },
        )

    def move_database(
        self,
        database_id: str,
        parent_page_id: str,
        *,
        is_inline: bool = True,
    ) -> dict[str, Any]:
        return self.request(
            f"https://api.notion.com/v1/databases/{database_id}",
            "PATCH",
            {
                "parent": {
                    "type": "page_id",
                    "page_id": normalize_notion_id(parent_page_id),
                },
                "is_inline": is_inline,
            },
        )

    def trash_database(self, database_id: str) -> dict[str, Any]:
        return self.request(
            f"https://api.notion.com/v1/databases/{database_id}",
            "PATCH",
            {"in_trash": True},
        )

    def request(
        self, endpoint: str, method: str, body: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        payload = json.dumps(body).encode("utf-8") if body is not None else None
        last_error: Exception | None = None
        for attempt in range(4):
            request = Request(endpoint, method=method)
            request.add_header("Authorization", f"Bearer {self.config.token}")
            request.add_header("Content-Type", "application/json")
            request.add_header("Notion-Version", "2026-03-11")
            try:
                with urlopen(request, data=payload, timeout=30) as response:
                    return json.loads(response.read().decode("utf-8"))
            except HTTPError as exc:  # pragma: no cover
                details = exc.read().decode("utf-8", errors="replace")
                if exc.code in {429, 500, 502, 503, 504} and attempt < 3:
                    time.sleep(attempt + 1)
                    last_error = RuntimeError(f"Notion API {exc.code}: {details}")
                    continue
                raise RuntimeError(f"Notion API {exc.code}: {details}") from exc
            except (URLError, RemoteDisconnected, ConnectionError) as exc:  # pragma: no cover
                last_error = exc
                if attempt < 3:
                    time.sleep(attempt + 1)
                    continue
                raise RuntimeError(f"Notion request failed: {exc}") from exc
            except TimeoutError as exc:  # pragma: no cover
                last_error = exc
                if attempt < 3:
                    time.sleep(attempt + 1)
                    continue
                raise RuntimeError(f"Notion request timed out: {exc}") from exc
        if last_error:
            raise RuntimeError(f"Notion request failed: {last_error}") from last_error
        raise RuntimeError("Notion request failed without an explicit error")


def normalize_notion_id(value: str) -> str:
    candidate = value.strip()
    compact = candidate.replace("-", "")
    match = re.search(r"([0-9a-fA-F]{32})", compact)
    if match:
        raw = match.group(1)
        return f"{raw[0:8]}-{raw[8:12]}-{raw[12:16]}-{raw[16:20]}-{raw[20:32]}"
    match = re.search(r"([0-9a-fA-F-]{36})", candidate)
    if match:
        return match.group(1)
    raise ValueError("Could not extract a Notion page ID from the provided value.")


def notion_title_text(value: object) -> str:
    if not isinstance(value, list):
        return ""
    pieces: list[str] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        plain_text = str(item.get("plain_text", "") or "")
        if plain_text:
            pieces.append(plain_text)
            continue
        text = item.get("text")
        if isinstance(text, dict):
            pieces.append(str(text.get("content", "") or ""))
    return "".join(pieces).strip()


def environment_credential(name: str) -> str:
    value = os.getenv(name, "").strip()
    return "" if "YOUR_ACTUAL_" in value.upper() else value


def linked_block_url(block: dict[str, Any]) -> str:
    block_type = str(block.get("type", "") or "")
    rich_text = ((block.get(block_type, {}) or {}).get("rich_text") or []) if block_type else []
    for piece in rich_text:
        text = piece.get("text", {}) or {}
        link = text.get("link", {}) or {}
        if isinstance(link, dict) and link.get("url"):
            return str(link.get("url", "") or "")
        if piece.get("href"):
            return str(piece.get("href", "") or "")
    return ""


def compact_notion_url(value: object) -> str:
    return str(value or "").split("?", 1)[0].rstrip("/").strip().lower()


def normalize_status_name(value: object) -> str:
    normalized = str(value or "new").strip().lower()
    mapping = {
        "new": "new",
        "not applied": "new",
        "没投": "new",
        "applied": "applied",
        "投了": "applied",
        "interview": "applied",
        "offer": "applied",
        "rejected": "applied",
        "n/a": "new",
        "na": "new",
        "not fit": "not_interested",
        "not interested": "not_interested",
        "not_interested": "not_interested",
        "不考虑": "not_interested",
    }
    return mapping.get(normalized, "new")


def notion_status_name(value: object) -> str:
    normalized = normalize_status_name(value)
    if normalized == "applied":
        return "Applied"
    if normalized == "not_interested":
        return "Not Interested"
    return "Not Applied"


def notion_source_name(value: str) -> str:
    normalized = value.strip().lower()
    return {
        "linkedin": "LinkedIn",
        "linkedin_direct": "LinkedIn",
        "indeed": "Indeed",
        "indeed_brightdata": "Indeed",
        "email": "Email",
        "email_imap": "Email",
    }.get(normalized, normalized.title() or "Unknown")
