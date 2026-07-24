from __future__ import annotations

import asyncio
from typing import Any

import job_scraper.collectors.data_integration_adapter as adapter
from job_scraper.collectors.data_integration_adapter import (
    BrightDataBatchResult,
    execute_resilient_brightdata_url_batches,
)


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


def test_persistent_bad_url_is_isolated(monkeypatch) -> None:
    calls: list[list[str]] = []

    async def fake_url_batch(urls: list[str], **kwargs: Any) -> BrightDataBatchResult:
        del kwargs
        calls.append(list(urls))
        if "bad" in urls:
            raise adapter.DataSyncError("input rejected")
        return BrightDataBatchResult(
            snapshot_id=f"snapshot-{len(calls)}",
            request_hash="hash",
            records=[{"record_id": url} for url in urls],
        )

    monkeypatch.setattr(adapter, "execute_brightdata_url_batch_sync", fake_url_batch)
    result = asyncio.run(
        execute_resilient_brightdata_url_batches(
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
