from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import pytest

import job_scraper.collectors.data_integration_adapter as adapter
import job_scraper.jobs.ingest_email_recommendations as email_ingest
from job_scraper.collectors.base import SearchWindow
from job_scraper.collectors.data_integration_adapter import (
    BrightDataBatchResult,
    BrightDataDetailResolutionResult,
    IndeedBrightDataCollector,
    execute_resilient_brightdata_detail_batches,
)
from job_scraper.config import HttpConfig, SourceConfig
from job_scraper.integrations.email_recommendations import EmailJobCandidate
from job_scraper.jobs.ingest_email_recommendations import raw_from_brightdata_detail
from job_scraper.storage.db import Database


def test_snapshot_timeout_default_covers_observed_long_runs() -> None:
    assert adapter.BRIGHTDATA_SNAPSHOT_TIMEOUT_SECONDS == 1800.0


def test_snapshot_poll_timeout_cancels_unconsumed_snapshot(tmp_path, monkeypatch) -> None:
    database = Database(tmp_path / "jobs.db")
    database.initialize()
    database.register_snapshot(
        "slow-snapshot",
        "brightdata",
        "fictional-dataset",
        "fictional-request-hash",
        [],
        status="running",
    )

    class FakeLoop:
        def __init__(self) -> None:
            self._times = iter((0.0, 0.0, 0.0, 1.0))

        def time(self) -> float:
            return next(self._times)

    async def fake_request(*args: Any, **kwargs: Any) -> Any:
        endpoint = str(args[0]) if args else ""
        del kwargs
        if endpoint.endswith("/cancel"):
            return "OK"
        return {"status": "running"}

    async def fake_sleep(delay: float) -> None:
        del delay

    monkeypatch.setattr(adapter.asyncio, "get_running_loop", FakeLoop)
    monkeypatch.setattr(adapter, "_brightdata_json_request", fake_request)
    monkeypatch.setattr(adapter.asyncio, "sleep", fake_sleep)

    with pytest.raises(adapter.DataSyncError, match=r"within 0\.5 seconds"):
        asyncio.run(
            adapter._wait_for_brightdata_snapshot(
                "slow-snapshot",
                "fictional-key",
                poll_interval_seconds=0,
                timeout_seconds=0.5,
                request_timeout_seconds=1,
                snapshot_database=database,
            )
        )

    with database.connect() as connection:
        row = connection.execute(
            "SELECT status, consumed_at, last_error "
            "FROM external_snapshot_state WHERE snapshot_id = 'slow-snapshot'"
        ).fetchone()
    assert row is not None
    assert row["status"] == "canceled"
    assert row["consumed_at"] is None
    assert "within 0.5 seconds" in row["last_error"]
    assert (
        database.find_resumable_snapshot(
            "brightdata", "fictional-dataset", "fictional-request-hash"
        )
        is None
    )


def test_stale_running_snapshot_is_not_resumed(tmp_path) -> None:
    database = Database(tmp_path / "jobs.db")
    database.initialize()
    database.register_snapshot(
        "stale-snapshot",
        "brightdata",
        "fictional-dataset",
        "fictional-request-hash",
        [],
        status="running",
    )
    with database.connect() as connection:
        connection.execute(
            "UPDATE external_snapshot_state SET created_at = '2020-01-01T00:00:00+00:00' "
            "WHERE snapshot_id = 'stale-snapshot'"
        )

    assert (
        database.find_resumable_snapshot(
            "brightdata",
            "fictional-dataset",
            "fictional-request-hash",
            max_age_seconds=1800,
        )
        is None
    )


def test_old_ready_snapshot_remains_resumable(tmp_path) -> None:
    database = Database(tmp_path / "jobs.db")
    database.initialize()
    database.register_snapshot(
        "old-ready-snapshot",
        "brightdata",
        "fictional-dataset",
        "fictional-request-hash",
        [],
        status="ready",
    )
    with database.connect() as connection:
        connection.execute(
            "UPDATE external_snapshot_state SET created_at = '2020-01-01T00:00:00+00:00' "
            "WHERE snapshot_id = 'old-ready-snapshot'"
        )

    row = database.find_resumable_snapshot(
        "brightdata",
        "fictional-dataset",
        "fictional-request-hash",
        max_age_seconds=1800,
    )
    assert row is not None
    assert row["snapshot_id"] == "old-ready-snapshot"


def test_cancelled_wait_cleans_up_provider_snapshot(tmp_path, monkeypatch) -> None:
    database = Database(tmp_path / "jobs.db")
    database.initialize()
    requests: list[tuple[str, str]] = []

    async def fake_request(endpoint: str, _api_key: str, *, method: str, **kwargs: Any) -> Any:
        del kwargs
        requests.append((endpoint, method))
        if endpoint.endswith("/trigger"):
            return {"snapshot_id": "cancelled-wait-snapshot"}
        if endpoint.endswith("/cancel"):
            return "OK"
        raise AssertionError(f"unexpected endpoint: {endpoint}")

    async def fake_wait(*args: Any, **kwargs: Any) -> None:
        del args, kwargs
        raise asyncio.CancelledError

    monkeypatch.setenv("BRIGHTDATA_API_KEY", "fictional-key")
    monkeypatch.setenv("BRIGHTDATA_DATASET_ID", "fictional-dataset")
    monkeypatch.setattr(adapter, "_brightdata_json_request", fake_request)
    monkeypatch.setattr(adapter, "_wait_for_brightdata_snapshot", fake_wait)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            adapter._execute_brightdata_snapshot(
                trigger_url="https://api.example.test/trigger",
                trigger_payload=[{"url": "https://example.test/job"}],
                api_key="fictional-key",
                dataset_id="fictional-dataset",
                request_hash="fictional-request-hash",
                snapshot_database=database,
                poll_interval_seconds=0,
                timeout_seconds=1,
                request_timeout_seconds=1,
                event_logger=None,
                trigger_detail="fictional",
            )
        )

    assert requests[-1] == (
        "https://api.brightdata.com/datasets/v3/snapshot/cancelled-wait-snapshot/cancel",
        "POST",
    )
    with database.connect() as connection:
        row = connection.execute(
            "SELECT status, last_error FROM external_snapshot_state "
            "WHERE snapshot_id = 'cancelled-wait-snapshot'"
        ).fetchone()
    assert row is not None
    assert row["status"] == "canceled"
    assert row["last_error"] == "Bright Data snapshot wait was cancelled"


def test_transient_server_errors_are_retried(monkeypatch) -> None:
    attempts = 0
    delays: list[float] = []

    def fake_request(*args: Any, **kwargs: Any) -> Any:
        nonlocal attempts
        del args, kwargs
        attempts += 1
        if attempts < 3:
            raise adapter._BrightDataHTTPError(500, "temporary failure")
        return {"ok": True}

    async def fake_sleep(delay: float) -> None:
        delays.append(delay)

    monkeypatch.setattr(adapter, "_brightdata_json_request_blocking", fake_request)
    monkeypatch.setattr(adapter.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(adapter.random, "uniform", lambda _start, _end: 0.0)

    result = asyncio.run(
        adapter._brightdata_json_request(
            "https://api.example.test/trigger",
            "test-key",
            method="POST",
            body=[{"url": "https://example.test/job"}],
            timeout_seconds=1,
        )
    )

    assert result == {"ok": True}
    assert attempts == 3
    assert delays == [0.5, 1.0]


def test_network_level_errors_are_retried_like_transient_http_errors(monkeypatch) -> None:
    attempts = 0

    def fake_request(*args: Any, **kwargs: Any) -> Any:
        nonlocal attempts
        del args, kwargs
        attempts += 1
        if attempts < 3:
            raise TimeoutError("timed out")
        return {"ok": True}

    async def fake_sleep(delay: float) -> None:
        del delay

    monkeypatch.setattr(adapter, "_brightdata_json_request_blocking", fake_request)
    monkeypatch.setattr(adapter.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(adapter.random, "uniform", lambda _start, _end: 0.0)

    result = asyncio.run(
        adapter._brightdata_json_request(
            "https://api.example.test/trigger",
            "test-key",
            method="POST",
            body=[{"url": "https://example.test/job"}],
            timeout_seconds=1,
        )
    )

    assert result == {"ok": True}
    assert attempts == 3


def test_network_level_errors_still_fail_after_exhausting_attempts(monkeypatch) -> None:
    def always_times_out(*args: Any, **kwargs: Any) -> Any:
        del args, kwargs
        raise TimeoutError("timed out")

    async def fake_sleep(delay: float) -> None:
        del delay

    monkeypatch.setattr(adapter, "_brightdata_json_request_blocking", always_times_out)
    monkeypatch.setattr(adapter.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(adapter.random, "uniform", lambda _start, _end: 0.0)

    with pytest.raises(adapter.DataSyncError):
        asyncio.run(
            adapter._brightdata_json_request(
                "https://api.example.test/trigger",
                "test-key",
                method="POST",
                body=None,
                timeout_seconds=1,
            )
        )


def test_email_brightdata_detail_enrichment_has_total_timeout(monkeypatch) -> None:
    async def slow_detail_batches(*args: Any, **kwargs: Any) -> BrightDataDetailResolutionResult:
        del args, kwargs
        await asyncio.sleep(10)
        return BrightDataDetailResolutionResult(batches=[], errors_by_url={})

    monkeypatch.setattr(
        email_ingest,
        "execute_resilient_brightdata_detail_batches",
        slow_detail_batches,
    )

    result = asyncio.run(
        email_ingest._execute_brightdata_detail_batches_bounded(
            ["https://de.indeed.com/viewjob?jk=fictional"],
            snapshot_database=None,
            request_timeout_seconds=1,
            total_timeout_seconds=0.01,
        )
    )

    assert result.batches == []
    assert result.errors_by_url == {
        "https://de.indeed.com/viewjob?jk=fictional": (
            "Bright Data detail enrichment exceeded total timeout of 0.01 seconds"
        )
    }


def test_persistent_bad_detail_url_is_isolated(monkeypatch) -> None:
    calls: list[list[str]] = []

    async def fake_detail_batch(urls: list[str], **kwargs: Any) -> BrightDataBatchResult:
        del kwargs
        calls.append(list(urls))
        if "bad" in urls:
            raise adapter.DataSyncError("input rejected")
        return BrightDataBatchResult(
            snapshot_id=f"snapshot-{len(calls)}",
            request_hash="hash",
            records=[{"record_id": url} for url in urls],
        )

    monkeypatch.setattr(adapter, "execute_brightdata_detail_batch_sync", fake_detail_batch)
    result = asyncio.run(
        execute_resilient_brightdata_detail_batches(
            ["one", "bad", "two", "three"],
            batch_size=4,
            max_concurrency=2,
        )
    )

    assert {record["record_id"] for batch in result.batches for record in batch.records} == {
        "one",
        "two",
        "three",
    }
    assert result.errors_by_url == {"bad": "input rejected"}
    assert calls[0] == ["one", "bad", "two", "three"]
    assert ["bad"] in calls


def test_direct_indeed_collection_is_single_stage_and_keeps_platform_url() -> None:
    search_calls = 0

    def fake_search_batch(*args: Any, **kwargs: Any) -> BrightDataBatchResult:
        nonlocal search_calls
        del args, kwargs
        search_calls += 1
        return BrightDataBatchResult(
            snapshot_id="search-snapshot",
            request_hash="search-hash",
            records=[
                {
                    "record_id": "fictional-1",
                    "reference_url": "https://de.indeed.com/viewjob?jk=fictional-1",
                    "external_application_url": "https://careers.example.test/fictional-1",
                    "position_title": "Fictional Engineer",
                    "organization": "Example GmbH",
                    "region": "Berlin",
                    "description": "A complete fictional job description.",
                }
            ],
        )

    collector = IndeedBrightDataCollector(
        HttpConfig("fictional-agent", 10, 0, 0, 0),
        SourceConfig(
            max_listing_pages=1,
            max_detail_fetches=10,
            results_per_input=10,
            search_queries=["fictional engineer"],
            locations=["Berlin"],
        ),
        batch_sync_runner=fake_search_batch,
    )

    records = list(
        collector.collect(SearchWindow(datetime.now(UTC), overlap_hours=24, post_age_hours=24))
    )

    assert search_calls == 1
    assert len(records) == 1
    assert records[0].source_url == "https://de.indeed.com/viewjob?jk=fictional-1"
    assert records[0].job_description == "A complete fictional job description."
    assert "external_application_url" not in records[0].raw_payload


def test_email_indeed_detail_enrichment_keeps_description_without_external_link() -> None:
    candidate = EmailJobCandidate(
        url="https://de.indeed.com/viewjob?jk=fictional-2",
        title="Fictional Engineer",
        company_name="Example GmbH",
        location_raw="Berlin",
        context="Fictional Engineer Example GmbH Berlin",
        message_id="fictional@example.test",
        email_subject="Fictional jobs",
        email_from="jobs@example.test",
        email_date=datetime(2026, 1, 1, tzinfo=UTC),
    )
    record = {
        "record_id": "fictional-2",
        "reference_url": "https://de.indeed.com/viewjob?jk=fictional-2",
        "external_application_url": "https://careers.example.test/fictional-2",
        "position_title": "Detailed Fictional Engineer",
        "organization": "Example GmbH",
        "region": "Berlin",
        "description": "A complete fictional description returned by the normal dataset.",
        "raw_payload": {},
    }

    raw = raw_from_brightdata_detail(
        candidate,
        record,
        BrightDataBatchResult("snapshot", "hash", [record]),
        datetime(2026, 1, 1, tzinfo=UTC),
    )

    assert raw.title == "Detailed Fictional Engineer"
    assert raw.job_description.startswith("A complete fictional description")
    assert raw.source_url == candidate.url
    assert "external_application_url" not in raw.raw_payload["brightdata_detail_payload"]
