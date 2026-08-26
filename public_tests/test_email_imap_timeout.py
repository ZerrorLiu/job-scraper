from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from urllib.error import URLError

import pytest

from job_scraper.config import HttpConfig
from job_scraper.integrations import email_recommendations as email_module
from job_scraper.integrations.email_recommendations import (
    MAX_DETAIL_RESPONSE_BYTES,
    DetailFetchTimeout,
    EmailIngestConfig,
    EmailJobCandidate,
    ImapEmailClient,
    fetch_text,
)
from job_scraper.jobs.ingest_email_recommendations import (
    brightdata_detail_enrichment_enabled,
    enrich_email_candidate_bounded,
)


def test_imap_client_passes_configured_timeout_to_ssl_connection(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeImap:
        def __init__(self, host: str, port: int, *, timeout: float) -> None:
            captured.update(host=host, port=port, timeout=timeout)

        def login(self, username: str, password: str) -> None:
            captured.update(username=username, password=password)

        def select(self, mailbox: str, readonly: bool) -> tuple[str, list[bytes]]:
            return "OK", []

        def uid(self, *_args: object) -> tuple[str, list[bytes]]:
            return "OK", [b""]

        def logout(self) -> None:
            captured["logged_out"] = True

    monkeypatch.setattr("imaplib.IMAP4_SSL", FakeImap)
    config = EmailIngestConfig(
        host="imap.example.test",
        port=993,
        username="candidate@example.test",
        password="fictional-password",
        folder="INBOX",
        use_ssl=True,
        lookback_days=1,
        max_messages=1,
        subject_keywords=[],
        sender_allowlist=[],
        state_path=Path("state.json"),
        track_config_paths=[],
    )

    assert ImapEmailClient(config, timeout_seconds=17).fetch_recent_messages() == []
    assert captured == {
        "host": "imap.example.test",
        "port": 993,
        "timeout": 17,
        "username": "candidate@example.test",
        "password": "fictional-password",
        "logged_out": True,
    }


def _candidate() -> EmailJobCandidate:
    return EmailJobCandidate(
        url="https://careers.example.test/jobs/fictional",
        title="Software Engineer",
        company_name="Example",
        location_raw="Berlin",
        context="Software Engineer role",
        message_id="fictional-message",
        email_subject="Fictional job",
        email_from="jobs@example.test",
        email_date=datetime.now(UTC),
        anchor_text="Software Engineer",
    )


def _runtime(timeout_seconds: int = 1) -> SimpleNamespace:
    return SimpleNamespace(
        config=SimpleNamespace(http=HttpConfig("fictional", timeout_seconds, 0, 0, 0))
    )


def test_a_failing_detail_fetch_degrades_to_the_email_card(monkeypatch) -> None:
    """A detail page that cannot be read must not abort the candidate."""

    def unreachable(*_args: object, **_kwargs: object):
        raise URLError("name resolution failed")

    monkeypatch.setattr(email_module, "urlopen", unreachable)

    result = enrich_email_candidate_bounded(_candidate(), [_runtime()], datetime.now(UTC))

    assert result.raw_payload["detail_status"] == "email_fallback"
    assert "name resolution failed" in str(result.raw_payload["detail_error"])


def test_the_detail_fetch_stops_at_its_total_budget(monkeypatch) -> None:
    """Retries share one wall-clock budget, so a slow host cannot run forever."""
    clock = iter([0.0, 0.0, 100.0, 100.0, 100.0])
    monkeypatch.setattr(email_module, "monotonic", lambda: next(clock))

    def slow(*_args: object, **_kwargs: object):
        raise TimeoutError("read timed out")

    monkeypatch.setattr(email_module, "urlopen", slow)
    monkeypatch.setattr(email_module.time, "sleep", lambda _seconds: None)

    with pytest.raises((DetailFetchTimeout, TimeoutError)):
        fetch_text("https://slow.example.test/job", HttpConfig("fictional", 5, 0, 0, 3))


def test_the_detail_fetch_caps_how_much_it_reads(monkeypatch) -> None:
    """A server that never closes the response cannot exhaust memory."""
    requested: list[int] = []

    class _Endless:
        def __enter__(self) -> _Endless:
            return self

        def __exit__(self, *_exc: object) -> None:
            return None

        def read(self, size: int | None = None) -> bytes:
            requested.append(size or -1)
            return b"a" * (size or 0)

        def geturl(self) -> str:
            return "https://slow.example.test/job"

    monkeypatch.setattr(email_module, "urlopen", lambda *_a, **_k: _Endless())

    body, _final = fetch_text("https://slow.example.test/job", HttpConfig("fictional", 5, 0, 0, 0))

    assert requested == [MAX_DETAIL_RESPONSE_BYTES]
    assert len(body) == MAX_DETAIL_RESPONSE_BYTES


def test_brightdata_email_detail_requires_explicit_opt_in(monkeypatch) -> None:
    monkeypatch.setenv("BRIGHTDATA_API_KEY", "fictional-key")
    monkeypatch.setenv("BRIGHTDATA_DATASET_ID", "fictional-dataset")
    monkeypatch.delenv("BRIGHTDATA_EMAIL_DETAIL_ENRICHMENT_ENABLED", raising=False)

    assert not brightdata_detail_enrichment_enabled()

    monkeypatch.setenv("BRIGHTDATA_EMAIL_DETAIL_ENRICHMENT_ENABLED", "true")
    assert brightdata_detail_enrichment_enabled()

    monkeypatch.delenv("BRIGHTDATA_DATASET_ID")
    assert not brightdata_detail_enrichment_enabled()
