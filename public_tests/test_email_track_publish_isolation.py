from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import job_scraper.jobs.ingest_email_recommendations as email_ingest
from job_scraper.integrations.email_recommendations import EmailIngestConfig, EmailIngestState
from job_scraper.jobs.ingest_email_recommendations import EmailPreparation, TrackRuntime
from job_scraper.storage.db import Database


class _FakeNotion:
    def enabled(self) -> bool:
        return True


def _runtime(tmp_path: Path, label: str) -> TrackRuntime:
    database = Database(tmp_path / f"{label}.db")
    database.initialize()
    started_at = datetime(2026, 1, 1, tzinfo=UTC)
    run = database.create_run("email", started_at)
    config = SimpleNamespace(
        project=SimpleNamespace(track_label=label, timezone="UTC"),
        notion=SimpleNamespace(daily_table_prefix=label),
    )
    return TrackRuntime(
        config_path=tmp_path / f"{label}.toml",
        config=config,  # type: ignore[arg-type]
        database=database,
        notion=_FakeNotion(),  # type: ignore[arg-type]
        run=run,
        processor=None,  # type: ignore[arg-type]
        profile_id=label,
        enabled_sinks=("notion_daily",),
    )


def _run_status(database: Database, run_id: str) -> str:
    with database.connect() as connection:
        row = connection.execute(
            "SELECT status FROM scrape_runs WHERE id = ?", (run_id,)
        ).fetchone()
        assert row is not None
        return str(row["status"])


def test_one_tracks_notion_failure_does_not_mark_other_tracks_failed(
    tmp_path: Path, monkeypatch
) -> None:
    runtime_a = _runtime(tmp_path, "track-a")
    runtime_b = _runtime(tmp_path, "track-b")

    def fake_publish_daily(database, jobs, notion, timezone_name, started_at, **kwargs):
        del database, jobs, notion, timezone_name, started_at
        track_label = kwargs.get("track_label")
        if track_label == "track-b":
            raise RuntimeError("Notion database access failed for track-b")
        return SimpleNamespace(published=0, skipped=0, errors=())

    monkeypatch.setattr(email_ingest, "publish_daily", fake_publish_daily)

    state_path = tmp_path / "state.json"
    preparation = EmailPreparation(
        args=argparse.Namespace(detail_workers=1, skip_notion=False),
        email_config=EmailIngestConfig(
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
            state_path=state_path,
            track_config_paths=[],
        ),
        started_at=datetime(2026, 1, 1, tzinfo=UTC),
        state=EmailIngestState.load(state_path),
        runtimes=[runtime_a, runtime_b],
        messages_to_mark=[],
        accepted_by_message={},
        candidate_tasks=[],
        prepared_details={},
        fetched_messages=0,
        skipped_messages=0,
    )

    status_code = email_ingest.finish(preparation)

    assert status_code == 1  # partial failure surfaced, not a silent success
    assert _run_status(runtime_a.database, runtime_a.run.run_id) == "completed"
    assert _run_status(runtime_b.database, runtime_b.run.run_id) == "failed"
    # State must still be saved even though one unrelated track's Notion
    # publish failed, so already-processed messages are not re-fetched.
    assert state_path.exists()
