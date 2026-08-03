from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import job_scraper.collectors.data_integration_adapter as adapter
from job_scraper.collectors.base import SearchWindow
from job_scraper.collectors.data_integration_adapter import (
    BrightDataBatchResult,
    IndeedBrightDataCollector,
    execute_resilient_brightdata_detail_batches,
)
from job_scraper.config import HttpConfig, SourceConfig
from job_scraper.integrations.email_recommendations import EmailJobCandidate
from job_scraper.jobs.ingest_email_recommendations import raw_from_brightdata_detail


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
