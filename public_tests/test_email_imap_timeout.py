from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from job_scraper.config import HttpConfig
from job_scraper.integrations.email_recommendations import (
    EmailIngestConfig,
    EmailJobCandidate,
    ImapEmailClient,
)
from job_scraper.jobs.ingest_email_recommendations import enrich_email_candidate_with_hard_timeout


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


def test_email_detail_hard_timeout_terminates_blocked_worker() -> None:
    class FakeQueue:
        def get_nowait(self) -> object:
            raise AssertionError("a blocked worker must not be read")

    class FakeProcess:
        terminated = False

        def start(self) -> None:
            return None

        def join(self, _timeout: int | None = None) -> None:
            return None

        def is_alive(self) -> bool:
            return not self.terminated

        def terminate(self) -> None:
            self.terminated = True

    process = FakeProcess()

    class FakeContext:
        def Queue(self, *, maxsize: int) -> FakeQueue:
            assert maxsize == 1
            return FakeQueue()

        def Process(self, **_kwargs: object) -> FakeProcess:
            return process

    candidate = EmailJobCandidate(
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
    runtime = SimpleNamespace(config=SimpleNamespace(http=HttpConfig("fictional", 1, 0, 0, 0)))

    result = enrich_email_candidate_with_hard_timeout(
        candidate,
        [runtime],
        datetime.now(UTC),
        process_context=FakeContext(),
    )

    assert process.terminated is True
    assert result.raw_payload["detail_status"] == "email_fallback"
    assert result.raw_payload["detail_error"] == "detail fetch timed out"
