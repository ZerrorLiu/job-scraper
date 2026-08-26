"""Notion write-path safety: no duplicate creates, no clobbered human decisions."""

from __future__ import annotations

from urllib.error import HTTPError

import pytest

from job_scraper.adapters.sinks.notion_daily import _merge_page_properties
from job_scraper.config import NotionConfig
from job_scraper.integrations import notion as notion_module
from job_scraper.integrations.notion import NotionClient


def _client() -> NotionClient:
    return NotionClient(
        NotionConfig(
            enabled=True,
            token="fictional-internal-token",
            database_id="",
            parent_page_id="fictional-page",
        )
    )


def _server_error(endpoint: str) -> HTTPError:
    return HTTPError(endpoint, 502, "Bad Gateway", {}, None)  # type: ignore[arg-type]


def _install(monkeypatch, responder) -> list[float]:
    delays: list[float] = []
    monkeypatch.setattr(notion_module, "urlopen", responder)
    monkeypatch.setattr(notion_module.time, "sleep", lambda seconds: delays.append(seconds))
    return delays


class _Response:
    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def read(self) -> bytes:
        return b'{"ok": true}'


def test_page_creation_is_not_replayed_after_a_server_error(monkeypatch) -> None:
    """A 5xx on POST /v1/pages may already have created the page.

    Replaying it would add a duplicate row to the daily table, so the client
    must surface the error instead of retrying.
    """
    attempts = 0

    def always_502(*_args: object, **_kwargs: object):
        nonlocal attempts
        attempts += 1
        raise _server_error("https://api.notion.com/v1/pages")

    _install(monkeypatch, always_502)

    with pytest.raises(RuntimeError, match="502"):
        _client().request("https://api.notion.com/v1/pages", "POST", {"properties": {}})

    assert attempts == 1


def test_rate_limited_page_creation_is_still_retried(monkeypatch) -> None:
    """429 means the request was rejected before any page was created."""
    attempts = 0

    def rate_limited_once(*_args: object, **_kwargs: object):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise HTTPError(
                "https://api.notion.com/v1/pages",
                429,
                "Too Many Requests",
                {"Retry-After": "3"},  # type: ignore[arg-type]
                None,
            )
        return _Response()

    delays = _install(monkeypatch, rate_limited_once)

    assert _client().request("https://api.notion.com/v1/pages", "POST", {}) == {"ok": True}
    assert delays == [3.0]


def test_query_and_update_calls_still_retry_server_errors(monkeypatch) -> None:
    """Reads and PATCHes are idempotent, so a transient 5xx should be absorbed."""
    attempts = 0

    def flaky(*_args: object, **_kwargs: object):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise _server_error("https://api.notion.com/v1/data_sources/x/query")
        return _Response()

    _install(monkeypatch, flaky)
    endpoint = "https://api.notion.com/v1/data_sources/x/query"

    assert _client().request(endpoint, "POST", {"page_size": 100}) == {"ok": True}
    assert attempts == 3


def test_internal_token_environment_enables_notion(monkeypatch) -> None:
    monkeypatch.setenv("NOTION_INTEGRATION_TOKEN", "fictional-internal-token")
    client = NotionClient(NotionConfig(enabled=True, parent_page_id="fictional-page"))

    assert client.enabled()


def test_verify_access_reads_the_explicit_database_and_never_writes(monkeypatch) -> None:
    methods: list[str] = []
    endpoints: list[str] = []

    def responder(request, *_args: object, **_kwargs: object):
        methods.append(request.get_method())
        endpoints.append(request.full_url)
        return _Response()

    _install(monkeypatch, responder)
    client = NotionClient(
        NotionConfig(
            enabled=True,
            token="fictional-internal-token",
            database_id="fictional-database",
        )
    )

    client.verify_access()

    assert methods == ["GET"]
    assert endpoints == ["https://api.notion.com/v1/databases/fictional-database"]


@pytest.mark.parametrize("status_kind", ["select", "status"])
def test_updating_a_row_preserves_a_manual_status(status_kind: str) -> None:
    """Whichever property type the workspace uses, the human decision wins."""
    existing_page = {"properties": {"Status": {status_kind: {"name": "Applied"}}}}
    incoming = {"Status": {status_kind: {"name": "Not Applied"}}}

    merged = _merge_page_properties(existing_page, incoming)

    assert merged["Status"] == {status_kind: {"name": "Applied"}}


def test_updating_a_row_unions_source_labels() -> None:
    existing_page = {"properties": {"Source": {"multi_select": [{"name": "LinkedIn"}]}}}
    incoming = {"Source": {"multi_select": [{"name": "Indeed"}]}}

    merged = _merge_page_properties(existing_page, incoming)

    assert merged["Source"] == {"multi_select": [{"name": "LinkedIn"}, {"name": "Indeed"}]}
