from __future__ import annotations

from http.client import IncompleteRead
from urllib.error import URLError

import job_scraper.collectors.base as base
import job_scraper.integrations.email_recommendations as email_recommendations
from job_scraper.collectors.base import BaseCollector
from job_scraper.config import HttpConfig, SourceConfig


class _FakeResponse:
    def __init__(self, body: bytes, url: str = "https://example.test/detail") -> None:
        self._body = body
        self._url = url

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def read(self, size: int | None = None) -> bytes:
        # Mirrors http.client.HTTPResponse: the caller may cap the read.
        return self._body if size is None else self._body[:size]

    def geturl(self) -> str:
        return self._url


class _FictionalCollector(BaseCollector):
    source_name = "fictional"

    def collect(self, window: object):  # type: ignore[override]
        raise NotImplementedError


def test_email_detail_fetch_honors_configured_max_retries(monkeypatch) -> None:
    attempts = 0

    def flaky_urlopen(*_args: object, **_kwargs: object) -> _FakeResponse:
        nonlocal attempts
        attempts += 1
        if attempts < 2:
            raise URLError("connection reset")
        return _FakeResponse(b"<html>ok</html>")

    monkeypatch.setattr(email_recommendations, "urlopen", flaky_urlopen)
    monkeypatch.setattr(email_recommendations.time, "sleep", lambda _seconds: None)

    http_config = HttpConfig(
        user_agent="fictional-agent",
        timeout_seconds=5,
        base_delay_seconds=0,
        jitter_seconds=0,
        max_retries=2,
    )

    body, final_url = email_recommendations.fetch_text("https://example.test/detail", http_config)

    assert body == "<html>ok</html>"
    assert final_url == "https://example.test/detail"
    assert attempts == 2


def test_email_detail_fetch_still_fails_with_zero_configured_retries(monkeypatch) -> None:
    def always_fails(*_args: object, **_kwargs: object) -> _FakeResponse:
        raise URLError("connection reset")

    monkeypatch.setattr(email_recommendations, "urlopen", always_fails)

    http_config = HttpConfig(
        user_agent="fictional-agent",
        timeout_seconds=5,
        base_delay_seconds=0,
        jitter_seconds=0,
        max_retries=0,
    )

    try:
        email_recommendations.fetch_text("https://example.test/detail", http_config)
    except URLError:
        pass
    else:
        raise AssertionError("expected URLError to propagate with zero retries")


def test_base_collector_retries_incomplete_read_and_connection_reset(monkeypatch) -> None:
    attempts = 0

    def flaky_urlopen(*_args: object, **_kwargs: object) -> _FakeResponse:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise IncompleteRead(b"partial")
        if attempts == 2:
            raise ConnectionResetError("reset by peer")
        return _FakeResponse(b"page body")

    monkeypatch.setattr(base, "urlopen", flaky_urlopen)
    monkeypatch.setattr(base.time, "sleep", lambda _seconds: None)

    collector = _FictionalCollector(
        HttpConfig(
            user_agent="fictional-agent",
            timeout_seconds=5,
            base_delay_seconds=0,
            jitter_seconds=0,
            max_retries=2,
        ),
        SourceConfig(enabled=True),
    )

    body = collector.fetch_text("https://example.test/listing")

    assert body == "page body"
    assert attempts == 3
