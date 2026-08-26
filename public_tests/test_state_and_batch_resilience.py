"""Durability of the email processed-message state and per-input batch isolation."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from job_scraper.collectors import data_integration_adapter as adapter
from job_scraper.collectors.data_integration_adapter import (
    BrightDataSearchInput,
    DataSyncError,
    _execute_brightdata_snapshots_via_webhook,
)
from job_scraper.integrations.email_recommendations import (
    EmailIngestState,
    MailMessage,
)


def _message(message_id: str) -> MailMessage:
    return MailMessage(
        uid="1",
        message_id=message_id,
        subject="Fictional roles for you",
        sender="alerts@example.invalid",
        received_at=datetime(2026, 1, 1, tzinfo=UTC),
        text="",
        html="",
    )


def test_state_save_leaves_no_partial_file_behind(tmp_path: Path) -> None:
    state = EmailIngestState(path=tmp_path / "state.json")
    state.mark_processed(_message("a@example.invalid"), 1)
    state.save()

    reloaded = EmailIngestState.load(tmp_path / "state.json")

    assert reloaded.is_processed("a@example.invalid")
    assert list(tmp_path.iterdir()) == [tmp_path / "state.json"]


def test_a_failed_save_does_not_destroy_the_previous_state(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "state.json"
    state = EmailIngestState(path=path)
    state.mark_processed(_message("kept@example.invalid"), 1)
    state.save()

    def exploding_replace(*_args: object, **_kwargs: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(
        "job_scraper.integrations.email_recommendations.os.replace", exploding_replace
    )
    state.mark_processed(_message("new@example.invalid"), 1)
    with pytest.raises(OSError):
        state.save()

    survivor = EmailIngestState.load(path)
    assert survivor.is_processed("kept@example.invalid")
    assert not list(tmp_path.glob(".*tmp"))


def test_saving_drops_records_too_old_to_suppress_anything(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    stale = (datetime.now(UTC) - timedelta(days=200)).isoformat()
    fresh = datetime.now(UTC).isoformat()
    path.write_text(
        json.dumps(
            {
                "processed_messages": {
                    "old@example.invalid": {"processed_at": stale},
                    "new@example.invalid": {"processed_at": fresh},
                    "undated@example.invalid": {},
                }
            }
        ),
        encoding="utf-8",
    )

    state = EmailIngestState.load(path)
    state.save()
    reloaded = EmailIngestState.load(path)

    assert not reloaded.is_processed("old@example.invalid")
    assert reloaded.is_processed("new@example.invalid")
    # No timestamp means the record predates the field; keeping it is the safe
    # side of the trade, since dropping it would republish the message.
    assert reloaded.is_processed("undated@example.invalid")


def _search_input(query: str) -> BrightDataSearchInput:
    return BrightDataSearchInput(
        search_query=query,
        geographic_zone="Fictionia",
        country="DE",
        domain="de.indeed.example",
    )


def _run_webhook_batch(monkeypatch, outcomes: dict[str, object]):
    async def fake_one(search_input, **_kwargs):
        outcome = outcomes[search_input.search_query]
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setattr(adapter, "_execute_one_brightdata_snapshot_via_webhook", fake_one)
    return asyncio.run(
        _execute_brightdata_snapshots_via_webhook(
            [_search_input(name) for name in outcomes],
            trigger_url="https://api.example.invalid/trigger",
            api_key="fictional",
            dataset_id="dataset",
            snapshot_database=None,  # type: ignore[arg-type]
            webhook_base_url="https://hooks.example.invalid",
            webhook_token="fictional",
            request_timeout_seconds=5.0,
            event_logger=None,
        )
    )


def test_one_failing_keyword_does_not_discard_its_siblings(monkeypatch) -> None:
    result = _run_webhook_batch(
        monkeypatch,
        {
            "good": ([{"record_id": "1"}], "snapshot-good"),
            "bad": DataSyncError("provider rejected this input"),
            "pending": ([], "snapshot-pending"),
        },
    )

    assert [record["record_id"] for record in result.records] == ["1"]
    # The pending snapshot id must survive so a later run can still collect it.
    assert result.snapshot_ids == ["snapshot-good", "snapshot-pending"]


def test_a_batch_where_every_input_failed_is_an_error(monkeypatch) -> None:
    with pytest.raises(DataSyncError, match="All 2 Bright Data webhook submissions failed"):
        _run_webhook_batch(
            monkeypatch,
            {
                "a": DataSyncError("boom"),
                "b": DataSyncError("boom"),
            },
        )
