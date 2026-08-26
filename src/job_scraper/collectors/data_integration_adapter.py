from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import random
from collections.abc import Callable, Coroutine, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

from job_scraper.collectors.base import BaseCollector, SearchWindow
from job_scraper.config import HttpConfig, SourceConfig
from job_scraper.domain.countries import COUNTRY_LOCATION_HINTS
from job_scraper.domain.models import RawJobRecord
from job_scraper.domain.payload_sanitization import sanitize_job_payload
from job_scraper.storage.db import Database

LOGGER = logging.getLogger(__name__)
INDEED_VIEW_JOB_URL = "https://www.indeed.com/viewjob"
BRIGHTDATA_API_BASE_URL = "https://api.brightdata.com/datasets/v3"
BRIGHTDATA_TERMINAL_FAILURE_STATES = {"canceled", "cancelled", "empty", "failed"}
BRIGHTDATA_READY_STATES = {"done", "ready"}
BRIGHTDATA_RETRYABLE_HTTP_STATUSES = {408, 429, 500, 502, 503, 504}
BRIGHTDATA_REQUEST_MAX_ATTEMPTS = 3
BRIGHTDATA_RETRY_BASE_SECONDS = 0.5
BRIGHTDATA_SNAPSHOT_TIMEOUT_SECONDS = 1800.0
BRIGHTDATA_SNAPSHOT_CANCEL_PATH = "/snapshot/{snapshot_id}/cancel"
BRIGHTDATA_WEBHOOK_PENDING_MAX_AGE_SECONDS = 259200.0  # 3 days
INDEED_MARKETS = {
    "AT": "at.indeed.com",
    "BE": "be.indeed.com",
    "CH": "ch.indeed.com",
    "DE": "de.indeed.com",
    "FR": "fr.indeed.com",
    "GB": "uk.indeed.com",
    "IE": "ie.indeed.com",
    "LU": "lu.indeed.com",
    "NL": "nl.indeed.com",
    "US": "indeed.com",
}
# Indeed only has a storefront for the markets in INDEED_MARKETS, so the
# location -> country hints are the shared reference narrowed to those markets
# rather than a fourth hand-maintained copy.
INDEED_LOCATION_COUNTRY_HINTS = {
    hint: code for code in INDEED_MARKETS for hint in COUNTRY_LOCATION_HINTS.get(code, set())
}


class DataSyncError(RuntimeError):
    """Raised when an Indeed dataset cannot be loaded or normalized safely."""


class _BrightDataHTTPError(DataSyncError):
    """Retain HTTP metadata so transient API failures can be retried safely."""

    def __init__(self, status_code: int, details: str, retry_after: float | None = None) -> None:
        super().__init__(f"Bright Data API {status_code}: {details}")
        self.status_code = status_code
        self.retry_after = retry_after


@dataclass(frozen=True, slots=True)
class BrightDataSearchInput:
    search_query: str
    geographic_zone: str
    country: str
    domain: str
    date_posted: str = ""

    def as_payload(self) -> dict[str, str]:
        return {
            "country": self.country,
            "domain": self.domain,
            "keyword_search": self.search_query,
            "location": self.geographic_zone,
            "date_posted": self.date_posted,
            "posted_by": "",
            "location_radius": "",
        }


@dataclass(slots=True)
class BrightDataBatchResult:
    snapshot_id: str
    request_hash: str
    records: list[dict[str, Any]]
    # False when the runner itself owns (or intentionally defers) marking the
    # snapshot consumed -- e.g. the webhook runner, which may return zero
    # records for a snapshot that is still pending on Bright Data's side and
    # must remain resumable on a later run rather than be treated as done.
    mark_consumed_on_collect: bool = True
    # One entry per Bright Data snapshot this result draws from. The webhook
    # runner submits one snapshot per search input (so each keyword runs as
    # its own concurrent job instead of queueing inside one shared batch);
    # `snapshot_id` above stays populated with the first one for callers that
    # only care about a single representative id.
    snapshot_ids: list[str] = field(default_factory=list)


@dataclass(slots=True)
class BrightDataDetailResolutionResult:
    batches: list[BrightDataBatchResult]
    errors_by_url: dict[str, str]


def normalize_upstream_entry(entry: Mapping[str, Any]) -> dict[str, Any] | None:
    """Map one Bright Data object into the stable Indeed interchange schema."""
    if entry.get("error") or entry.get("error_code"):
        # `include_errors=true` makes Bright Data report a URL it failed to
        # parse instead of silently omitting it. It carries no job fields, so
        # without this check `_first_text`'s "N/A" default would look like a
        # real (if garbage) record_id instead of being dropped here.
        return None
    reference_url = _first_text(
        entry,
        "reference_url",
        "url",
        "job_url",
        "jobUrl",
        "link",
        default="",
    )
    record_id = _first_text(
        entry,
        "record_id",
        "jobid",
        "jobkey",
        "jobKey",
        "job_id",
        "jobId",
        "job_posting_id",
        "position_id",
        "id",
        default="",
    )
    if not record_id and reference_url:
        record_id = hashlib.sha256(reference_url.encode("utf-8")).hexdigest()[:32]
    if not record_id:
        return None
    if not _is_indeed_platform_url(reference_url):
        reference_url = f"{INDEED_VIEW_JOB_URL}?{urlencode({'jk': record_id})}"

    attributes = entry.get("formattedAttributes") or entry.get("attributes") or []
    employment_type = _sequence_text(attributes)
    if not employment_type:
        employment_type = _first_text(
            entry,
            "employment_type",
            "employmentType",
            "job_type",
            "jobType",
            "job_employment_type",
            default="unknown",
        )

    return {
        "record_id": record_id,
        "position_title": _first_text(
            entry, "position_title", "title", "displayTitle", "job_title"
        ),
        "organization": _organization_text(entry),
        "region": _location_text(entry),
        "compensation_package": _compensation_text(entry),
        "timestamp": _first_text(
            entry,
            "date_posted_parsed",
            "timestamp",
            "formattedRelativeTime",
            "relativeTime",
            "posted_at",
            "postedAt",
            "date_posted",
            "job_posted_date",
        ),
        "reference_url": reference_url,
        "description": _first_text(
            entry,
            "description",
            "description_text",
            "snippet",
            "descriptionSnippet",
            "summary",
            "job_description",
            "job_summary",
        ),
        "employment_type": employment_type or "unknown",
        "raw_payload": sanitize_job_payload(entry),
    }


def _is_indeed_platform_url(value: str) -> bool:
    hostname = (urlsplit(value).hostname or "").casefold()
    return hostname == "indeed.com" or hostname.endswith(".indeed.com")


async def execute_brightdata_dataset_batch_sync(
    search_inputs: Sequence[BrightDataSearchInput],
    *,
    limit_per_input: int,
    limit_multiple_results: int,
    output_path: str | Path | None = None,
    snapshot_database: Database | None = None,
    poll_interval_seconds: float | None = None,
    timeout_seconds: float = BRIGHTDATA_SNAPSHOT_TIMEOUT_SECONDS,
    request_timeout_seconds: float = 30.0,
    event_logger: Callable[[str], None] | None = None,
) -> BrightDataBatchResult:
    """Trigger or resume one bounded Snapshot for a complete Indeed search matrix."""
    if not search_inputs:
        raise ValueError("search_inputs must not be empty")
    if limit_per_input < 1:
        raise ValueError("limit_per_input must be greater than 0")
    if limit_multiple_results < 1:
        raise ValueError("limit_multiple_results must be greater than 0")
    if poll_interval_seconds is not None and poll_interval_seconds < 0:
        raise ValueError("poll_interval_seconds must not be negative")
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be greater than 0")

    api_key = _required_environment_value("BRIGHTDATA_API_KEY")
    dataset_id = _required_environment_value("BRIGHTDATA_DATASET_ID")
    trigger_parameters = {
        "dataset_id": dataset_id,
        "include_errors": "true",
        "type": "discover_new",
        "discover_by": "keyword",
        "limit_per_input": str(limit_per_input),
        "limit_multiple_results": str(limit_multiple_results),
        "format": "json",
    }
    trigger_url = f"{BRIGHTDATA_API_BASE_URL}/trigger?{urlencode(trigger_parameters)}"
    trigger_payload = [item.as_payload() for item in search_inputs]
    request_hash = hashlib.sha256(
        json.dumps(
            {
                "dataset_id": dataset_id,
                "limit_per_input": limit_per_input,
                "limit_multiple_results": limit_multiple_results,
                "inputs": trigger_payload,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()

    webhook_base_url = os.getenv("BRIGHTDATA_WEBHOOK_URL", "").strip()
    webhook_token = os.getenv("BRIGHTDATA_WEBHOOK_TOKEN", "").strip()
    if webhook_base_url and webhook_token:
        if snapshot_database is None:
            raise DataSyncError(
                "BRIGHTDATA_WEBHOOK_URL is configured but no snapshot_database was provided "
                "to track pending snapshots across runs"
            )
        return await _execute_brightdata_snapshots_via_webhook(
            search_inputs,
            trigger_url=trigger_url,
            api_key=api_key,
            dataset_id=dataset_id,
            snapshot_database=snapshot_database,
            webhook_base_url=webhook_base_url,
            webhook_token=webhook_token,
            request_timeout_seconds=request_timeout_seconds,
            event_logger=event_logger,
        )

    markets = sorted({f"{item.country}/{item.domain}" for item in search_inputs})
    snapshot_id, downloaded = await _execute_brightdata_snapshot(
        trigger_url=trigger_url,
        trigger_payload=trigger_payload,
        api_key=api_key,
        dataset_id=dataset_id,
        request_hash=request_hash,
        snapshot_database=snapshot_database,
        poll_interval_seconds=poll_interval_seconds,
        timeout_seconds=timeout_seconds,
        request_timeout_seconds=request_timeout_seconds,
        event_logger=event_logger,
        trigger_detail=(f"Markets {', '.join(markets)} | Global limit {limit_multiple_results}"),
    )

    ingested_records = _normalize_brightdata_entries(
        downloaded,
        snapshot_id=snapshot_id,
        search_inputs=search_inputs,
        event_logger=event_logger,
    )

    if output_path is not None:
        _write_json(Path(output_path), ingested_records)
    _log_cloud_event(
        f"Cloud snapshot downloaded | Snapshot {snapshot_id} | "
        f"Raw rows {len(downloaded)} | Mapped rows {len(ingested_records)}",
        event_logger,
    )
    return BrightDataBatchResult(
        snapshot_id=snapshot_id,
        request_hash=request_hash,
        records=ingested_records,
        snapshot_ids=[snapshot_id],
    )


def _normalize_brightdata_entries(
    downloaded: Iterable[object],
    *,
    snapshot_id: str,
    search_inputs: Sequence[BrightDataSearchInput],
    event_logger: Callable[[str], None] | None = None,
) -> list[dict[str, Any]]:
    """Deduplicate and tag every downloaded entry -- no count-based cutoff here.

    Per-query bounding happens later in `_select_brightdata_records`, which
    can see each record's originating query. Cutting off early here (by raw
    download order) would let one query's results crowd out every other
    query's before that grouping ever gets a chance to run.
    """
    ingested_records: list[dict[str, Any]] = []
    seen_record_ids: set[str] = set()
    for entry in downloaded:
        if not isinstance(entry, Mapping):
            continue
        error_detail = entry.get("error") or entry.get("error_code")
        if error_detail:
            failed_url = str((entry.get("input") or {}).get("url") or entry.get("url") or "")
            _log_cloud_event(
                f"Snapshot {snapshot_id} | Bright Data could not parse a candidate URL | "
                f"{failed_url} | {error_detail}",
                event_logger,
            )
            continue
        normalized = normalize_upstream_entry(entry)
        if normalized is None or normalized["record_id"] in seen_record_ids:
            continue
        seen_record_ids.add(normalized["record_id"])
        query, location = _brightdata_record_context(entry, search_inputs)
        normalized["brightdata_query"] = query
        normalized["brightdata_location"] = location
        normalized["raw_payload"].update(
            {
                "brightdata_snapshot_id": snapshot_id,
                "brightdata_query": query,
                "brightdata_location": location,
            }
        )
        ingested_records.append(normalized)
    return ingested_records


async def _execute_brightdata_snapshots_via_webhook(
    search_inputs: Sequence[BrightDataSearchInput],
    *,
    trigger_url: str,
    api_key: str,
    dataset_id: str,
    snapshot_database: Database,
    webhook_base_url: str,
    webhook_token: str,
    request_timeout_seconds: float,
    event_logger: Callable[[str], None] | None,
) -> BrightDataBatchResult:
    """Non-blocking Indeed acquisition backed by the vps-dev webhook receiver.

    Submits one independent Bright Data snapshot per search input rather than
    one combined batch. Empirically, a single Bright Data snapshot covering
    13 keywords processes them with limited internal concurrency (~2h16m
    total, keywords starting minutes to an hour apart), while independently
    submitted single-keyword snapshots run genuinely in parallel (confirmed:
    three submitted together advanced in lockstep). Splitting per input lets
    every keyword actually run at once instead of queueing behind the rest.

    Never waits on Bright Data: each input independently reuses a snapshot
    the receiver already downloaded, checks whether one it is still watching
    has become ready, or submits a new one with `notify` pointed at the
    receiver -- all without blocking. A given run may therefore contribute
    records for only some inputs while others are still in flight; a later
    run picks up whatever becomes ready next.
    """
    # Failures are isolated per input. Without return_exceptions, one bad
    # keyword would discard every sibling's records *and* the snapshot ids that
    # were already submitted and registered, orphaning paid Bright Data work
    # that no later run would know to collect.
    per_input_results = await asyncio.gather(
        *(
            _execute_one_brightdata_snapshot_via_webhook(
                search_input,
                trigger_url=trigger_url,
                api_key=api_key,
                dataset_id=dataset_id,
                snapshot_database=snapshot_database,
                webhook_base_url=webhook_base_url,
                webhook_token=webhook_token,
                request_timeout_seconds=request_timeout_seconds,
                event_logger=event_logger,
            )
            for search_input in search_inputs
        ),
        return_exceptions=True,
    )
    all_records: list[dict[str, Any]] = []
    all_snapshot_ids: list[str] = []
    failures = 0
    for search_input, outcome in zip(search_inputs, per_input_results, strict=True):
        if isinstance(outcome, BaseException):
            failures += 1
            _log_cloud_event(
                f"Webhook snapshot failed | Query {search_input.search_query!r} | {outcome}",
                event_logger,
            )
            continue
        records, snapshot_id = outcome
        all_records.extend(records)
        if snapshot_id:
            all_snapshot_ids.append(snapshot_id)
    if failures and not all_snapshot_ids:
        raise DataSyncError(f"All {failures} Bright Data webhook submissions failed for this run")
    request_hash = hashlib.sha256(json.dumps(sorted(all_snapshot_ids)).encode("utf-8")).hexdigest()
    return BrightDataBatchResult(
        snapshot_id=all_snapshot_ids[0] if all_snapshot_ids else "",
        request_hash=request_hash,
        records=all_records,
        mark_consumed_on_collect=False,
        snapshot_ids=all_snapshot_ids,
    )


async def _execute_one_brightdata_snapshot_via_webhook(
    search_input: BrightDataSearchInput,
    *,
    trigger_url: str,
    api_key: str,
    dataset_id: str,
    snapshot_database: Database,
    webhook_base_url: str,
    webhook_token: str,
    request_timeout_seconds: float,
    event_logger: Callable[[str], None] | None,
) -> tuple[list[dict[str, Any]], str]:
    item_payload = [search_input.as_payload()]
    item_hash = hashlib.sha256(
        json.dumps(
            {"dataset_id": dataset_id, "input": item_payload},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()

    resumable = snapshot_database.find_resumable_snapshot(
        "brightdata",
        dataset_id,
        item_hash,
        max_age_seconds=BRIGHTDATA_WEBHOOK_PENDING_MAX_AGE_SECONDS,
    )
    if resumable is not None:
        snapshot_id = str(resumable["snapshot_id"])
        status = str(resumable["status"])
        if status != "ready":
            ready_ids = await _webhook_request(
                webhook_base_url,
                webhook_token,
                "GET",
                "/snapshots",
                request_timeout_seconds=request_timeout_seconds,
            )
            if not isinstance(ready_ids, Mapping) or snapshot_id not in (
                ready_ids.get("ready") or []
            ):
                _log_cloud_event(
                    f"Webhook snapshot still pending | Query {search_input.search_query!r} | "
                    f"Snapshot {snapshot_id}",
                    event_logger,
                )
                return [], snapshot_id
            snapshot_database.update_snapshot_status(snapshot_id, "ready")

        downloaded = await _webhook_request(
            webhook_base_url,
            webhook_token,
            "GET",
            f"/snapshots/{snapshot_id}",
            request_timeout_seconds=request_timeout_seconds,
        )
        if not isinstance(downloaded, list):
            raise DataSyncError(
                f"Webhook receiver snapshot payload for {snapshot_id} was not a JSON array"
            )
        records = _normalize_brightdata_entries(
            downloaded,
            snapshot_id=snapshot_id,
            search_inputs=[search_input],
            event_logger=event_logger,
        )
        await _webhook_request(
            webhook_base_url,
            webhook_token,
            "POST",
            f"/snapshots/{snapshot_id}/ack",
            request_timeout_seconds=request_timeout_seconds,
        )
        snapshot_database.mark_snapshot_consumed(snapshot_id)
        _log_cloud_event(
            f"Webhook snapshot collected | Query {search_input.search_query!r} | "
            f"Snapshot {snapshot_id} | Raw rows {len(downloaded)} | Mapped rows {len(records)}",
            event_logger,
        )
        return records, snapshot_id

    notify_url = f"{webhook_base_url}/webhook?{urlencode({'token': webhook_token})}"
    trigger_response = await _brightdata_json_request(
        f"{trigger_url}&{urlencode({'notify': notify_url})}",
        api_key,
        method="POST",
        body=item_payload,
        timeout_seconds=request_timeout_seconds,
    )
    if not isinstance(trigger_response, Mapping):
        raise DataSyncError("Bright Data trigger response was not a JSON object")
    snapshot_id = str(trigger_response.get("snapshot_id") or "").strip()
    if not snapshot_id:
        raise DataSyncError("Bright Data trigger response did not include snapshot_id")
    snapshot_database.register_snapshot(
        snapshot_id, "brightdata", dataset_id, item_hash, item_payload
    )
    await _webhook_request(
        webhook_base_url,
        webhook_token,
        "POST",
        "/register",
        body={"snapshot_id": snapshot_id},
        request_timeout_seconds=request_timeout_seconds,
    )
    _log_cloud_event(
        f"Submitted webhook snapshot | Query {search_input.search_query!r} | Snapshot {snapshot_id}",
        event_logger,
    )
    return [], snapshot_id


async def _webhook_request(
    base_url: str,
    token: str,
    method: str,
    path: str,
    *,
    body: Any = None,
    request_timeout_seconds: float,
) -> Any:
    separator = "&" if "?" in path else "?"
    endpoint = f"{base_url}{path}{separator}{urlencode({'token': token})}"
    payload = json.dumps(body).encode("utf-8") if body is not None else None
    request = Request(endpoint, data=payload, method=method)
    request.add_header("Accept", "application/json")
    # Cloudflare's default bot protection blocks urllib's stock User-Agent
    # ("Python-urllib/x.y") as a known non-browser signature.
    request.add_header("User-Agent", "job-scraper-webhook-client/1.0")
    if payload is not None:
        request.add_header("Content-Type", "application/json")
    try:
        return await asyncio.to_thread(_open_json_request, request, request_timeout_seconds)
    except HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")
        raise DataSyncError(f"Webhook receiver {exc.code} on {path}: {details}") from exc
    except (TimeoutError, URLError) as exc:
        raise DataSyncError(f"Webhook receiver request to {path} failed: {exc}") from exc


def _open_json_request(request: Request, timeout_seconds: float) -> Any:
    with urlopen(request, timeout=timeout_seconds) as response:
        return json.loads(response.read().decode("utf-8"))


async def execute_brightdata_detail_batch_sync(
    urls: Sequence[str],
    *,
    snapshot_database: Database | None = None,
    poll_interval_seconds: float | None = None,
    timeout_seconds: float = BRIGHTDATA_SNAPSHOT_TIMEOUT_SECONDS,
    request_timeout_seconds: float = 30.0,
    event_logger: Callable[[str], None] | None = None,
) -> BrightDataBatchResult:
    """Retrieve details for concrete Indeed job URLs through the Web Scraper API."""
    unique_urls = list(dict.fromkeys(url.strip() for url in urls if url.strip()))
    if not unique_urls:
        raise ValueError("urls must not be empty")
    api_key = _required_environment_value("BRIGHTDATA_API_KEY")
    dataset_id = _required_environment_value("BRIGHTDATA_DATASET_ID")
    trigger_payload = [{"url": url} for url in unique_urls]
    trigger_parameters = {
        "dataset_id": dataset_id,
        "include_errors": "true",
        "format": "json",
    }
    trigger_url = f"{BRIGHTDATA_API_BASE_URL}/trigger?{urlencode(trigger_parameters)}"
    request_hash = hashlib.sha256(
        json.dumps(
            {
                "dataset_id": dataset_id,
                "mode": "url",
                "inputs": trigger_payload,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    snapshot_id, downloaded = await _execute_brightdata_snapshot(
        trigger_url=trigger_url,
        trigger_payload=trigger_payload,
        api_key=api_key,
        dataset_id=dataset_id,
        request_hash=request_hash,
        snapshot_database=snapshot_database,
        poll_interval_seconds=poll_interval_seconds,
        timeout_seconds=timeout_seconds,
        request_timeout_seconds=request_timeout_seconds,
        event_logger=event_logger,
        trigger_detail="Concrete Indeed job URLs",
    )
    records: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for entry in downloaded:
        if not isinstance(entry, Mapping):
            continue
        normalized = normalize_upstream_entry(entry)
        if normalized is None or normalized["record_id"] in seen_ids:
            continue
        seen_ids.add(normalized["record_id"])
        normalized["raw_payload"]["brightdata_snapshot_id"] = snapshot_id
        normalized["raw_payload"]["brightdata_input_mode"] = "url"
        records.append(normalized)
    _log_cloud_event(
        f"Detail snapshot mapped | Snapshot {snapshot_id} | "
        f"Inputs {len(unique_urls)} | Records {len(records)}",
        event_logger,
    )
    return BrightDataBatchResult(
        snapshot_id=snapshot_id,
        request_hash=request_hash,
        records=records,
    )


async def execute_resilient_brightdata_detail_batches(
    urls: Sequence[str],
    *,
    batch_size: int = 10,
    max_concurrency: int = 3,
    snapshot_database: Database | None = None,
    poll_interval_seconds: float | None = None,
    timeout_seconds: float = BRIGHTDATA_SNAPSHOT_TIMEOUT_SECONDS,
    request_timeout_seconds: float = 30.0,
    event_logger: Callable[[str], None] | None = None,
) -> BrightDataDetailResolutionResult:
    """Retrieve detail batches independently and isolate a persistently bad input."""
    unique_urls = list(dict.fromkeys(url.strip() for url in urls if url.strip()))
    if not unique_urls:
        return BrightDataDetailResolutionResult(batches=[], errors_by_url={})
    if batch_size < 1:
        raise ValueError("batch_size must be at least 1")
    if max_concurrency < 1:
        raise ValueError("max_concurrency must be at least 1")

    semaphore = asyncio.Semaphore(max_concurrency)
    batches: list[BrightDataBatchResult] = []
    errors_by_url: dict[str, str] = {}

    async def resolve_chunk(chunk: list[str], label: str) -> None:
        try:
            async with semaphore:
                result = await execute_brightdata_detail_batch_sync(
                    chunk,
                    snapshot_database=snapshot_database,
                    poll_interval_seconds=poll_interval_seconds,
                    timeout_seconds=timeout_seconds,
                    request_timeout_seconds=request_timeout_seconds,
                    event_logger=event_logger,
                )
        except Exception as exc:
            if len(chunk) == 1:
                errors_by_url[chunk[0]] = str(exc)
                _log_cloud_event(
                    f"Indeed detail retrieval failed | Batch {label} | Inputs 1 | {exc}",
                    event_logger,
                )
                return
            midpoint = len(chunk) // 2
            _log_cloud_event(
                f"Indeed detail batch failed; splitting | Batch {label} | "
                f"Inputs {len(chunk)} | {exc}",
                event_logger,
            )
            await asyncio.gather(
                resolve_chunk(chunk[:midpoint], f"{label}.1"),
                resolve_chunk(chunk[midpoint:], f"{label}.2"),
            )
            return
        batches.append(result)

    initial_chunks = [
        unique_urls[offset : offset + batch_size]
        for offset in range(0, len(unique_urls), batch_size)
    ]
    await asyncio.gather(
        *(resolve_chunk(chunk, str(index)) for index, chunk in enumerate(initial_chunks, start=1))
    )
    return BrightDataDetailResolutionResult(
        batches=batches,
        errors_by_url=errors_by_url,
    )


async def _execute_brightdata_snapshot(
    *,
    trigger_url: str,
    trigger_payload: list[dict[str, str]],
    api_key: str,
    dataset_id: str,
    request_hash: str,
    snapshot_database: Database | None,
    poll_interval_seconds: float | None,
    timeout_seconds: float,
    request_timeout_seconds: float,
    event_logger: Callable[[str], None] | None,
    trigger_detail: str,
) -> tuple[str, list[object]]:
    resumable = (
        snapshot_database.find_resumable_snapshot(
            "brightdata",
            dataset_id,
            request_hash,
            max_age_seconds=BRIGHTDATA_SNAPSHOT_TIMEOUT_SECONDS,
        )
        if snapshot_database is not None
        else None
    )
    status = str(resumable["status"]) if resumable is not None else ""
    snapshot_id = str(resumable["snapshot_id"]) if resumable is not None else ""
    if snapshot_id:
        _log_cloud_event(
            f"Resuming cloud snapshot | Snapshot {snapshot_id} | "
            f"Inputs {len(trigger_payload)} | Status {status}",
            event_logger,
        )
    else:
        _log_cloud_event(
            f"Triggering batched cloud snapshot | Inputs {len(trigger_payload)} | {trigger_detail}",
            event_logger,
        )
        trigger_response = await _brightdata_json_request(
            trigger_url,
            api_key,
            method="POST",
            body=trigger_payload,
            timeout_seconds=request_timeout_seconds,
        )
        if not isinstance(trigger_response, Mapping):
            raise DataSyncError("Bright Data trigger response was not a JSON object")
        snapshot_id = str(trigger_response.get("snapshot_id") or "").strip()
        if not snapshot_id:
            raise DataSyncError("Bright Data trigger response did not include snapshot_id")
        status = "starting"
        if snapshot_database is not None:
            snapshot_database.register_snapshot(
                snapshot_id,
                "brightdata",
                dataset_id,
                request_hash,
                trigger_payload,
            )
    if status != "ready":
        try:
            await _wait_for_brightdata_snapshot(
                snapshot_id,
                api_key,
                poll_interval_seconds=poll_interval_seconds,
                timeout_seconds=timeout_seconds,
                request_timeout_seconds=request_timeout_seconds,
                snapshot_database=snapshot_database,
                event_logger=event_logger,
            )
        except asyncio.CancelledError:
            cancellation_error = "Bright Data snapshot wait was cancelled"
            try:
                await _cancel_brightdata_snapshot(
                    snapshot_id,
                    api_key,
                    request_timeout_seconds=request_timeout_seconds,
                )
            except DataSyncError as exc:
                cancellation_error = f"{cancellation_error}; provider cancellation failed: {exc}"
                LOGGER.warning("%s", cancellation_error)
                if snapshot_database is not None:
                    snapshot_database.update_snapshot_status(
                        snapshot_id,
                        "timed_out",
                        last_error=cancellation_error,
                    )
            else:
                if snapshot_database is not None:
                    snapshot_database.update_snapshot_status(
                        snapshot_id,
                        "canceled",
                        last_error=cancellation_error,
                    )
            raise
    downloaded = await _brightdata_json_request(
        f"{BRIGHTDATA_API_BASE_URL}/snapshot/{snapshot_id}?format=json",
        api_key,
        method="GET",
        timeout_seconds=request_timeout_seconds,
    )
    if not isinstance(downloaded, list):
        raise DataSyncError("Bright Data snapshot download was not a JSON array")
    return snapshot_id, downloaded


async def _wait_for_brightdata_snapshot(
    snapshot_id: str,
    api_key: str,
    *,
    poll_interval_seconds: float | None,
    timeout_seconds: float,
    request_timeout_seconds: float,
    snapshot_database: Database | None = None,
    event_logger: Callable[[str], None] | None = None,
) -> None:
    loop = asyncio.get_running_loop()
    started_at = loop.time()
    deadline = loop.time() + timeout_seconds
    last_logged_status = ""
    last_logged_at = float("-inf")
    while True:
        progress = await _brightdata_json_request(
            f"{BRIGHTDATA_API_BASE_URL}/progress/{snapshot_id}",
            api_key,
            method="GET",
            timeout_seconds=request_timeout_seconds,
        )
        if not isinstance(progress, Mapping):
            raise DataSyncError("Bright Data progress response was not a JSON object")
        status = str(progress.get("status") or "").strip().casefold()
        elapsed = loop.time() - started_at
        if status != last_logged_status or elapsed - last_logged_at >= 60:
            _log_cloud_event(
                f"Cloud snapshot progress | Snapshot {snapshot_id} | "
                f"Status {status or 'unknown'} | Elapsed {elapsed:.0f}s",
                event_logger,
            )
            last_logged_status = status
            last_logged_at = elapsed
        if snapshot_database is not None:
            snapshot_database.update_snapshot_status(snapshot_id, status or "running")
        if status in BRIGHTDATA_READY_STATES:
            if snapshot_database is not None:
                snapshot_database.update_snapshot_status(snapshot_id, "ready")
            return
        if status in BRIGHTDATA_TERMINAL_FAILURE_STATES:
            details = progress.get("error") or progress.get("message") or "no failure details"
            if snapshot_database is not None:
                snapshot_database.update_snapshot_status(
                    snapshot_id,
                    status,
                    last_error=str(details),
                )
            raise DataSyncError(
                f"Bright Data snapshot {snapshot_id} ended with status {status}: {details}"
            )
        if loop.time() >= deadline:
            timeout_error = (
                f"Bright Data snapshot {snapshot_id} did not become ready within "
                f"{timeout_seconds:g} seconds"
            )
            try:
                await _cancel_brightdata_snapshot(
                    snapshot_id,
                    api_key,
                    request_timeout_seconds=request_timeout_seconds,
                )
            except DataSyncError as exc:
                cancellation_error = f"Bright Data snapshot cancellation failed: {exc}"
                LOGGER.warning("%s", cancellation_error)
                if snapshot_database is not None:
                    snapshot_database.update_snapshot_status(
                        snapshot_id,
                        "timed_out",
                        last_error=f"{timeout_error}; {cancellation_error}",
                    )
            else:
                if snapshot_database is not None:
                    snapshot_database.update_snapshot_status(
                        snapshot_id,
                        "canceled",
                        last_error=timeout_error,
                    )
            raise DataSyncError(timeout_error)
        interval = (
            poll_interval_seconds
            if poll_interval_seconds is not None
            else _adaptive_poll_interval(elapsed)
        )
        await asyncio.sleep(interval)


async def _cancel_brightdata_snapshot(
    snapshot_id: str,
    api_key: str,
    *,
    request_timeout_seconds: float,
) -> Any:
    endpoint = f"{BRIGHTDATA_API_BASE_URL}{BRIGHTDATA_SNAPSHOT_CANCEL_PATH.format(snapshot_id=snapshot_id)}"
    return await _brightdata_json_request(
        endpoint,
        api_key,
        method="POST",
        timeout_seconds=request_timeout_seconds,
    )


async def _brightdata_json_request(
    endpoint: str,
    api_key: str,
    *,
    method: str,
    body: Any = None,
    timeout_seconds: float,
) -> Any:
    for attempt in range(1, BRIGHTDATA_REQUEST_MAX_ATTEMPTS + 1):
        try:
            return await asyncio.to_thread(
                _brightdata_json_request_blocking,
                endpoint,
                api_key,
                method,
                body,
                timeout_seconds,
            )
        except _BrightDataHTTPError as exc:
            retryable = exc.status_code in BRIGHTDATA_RETRYABLE_HTTP_STATUSES
            if not retryable or attempt == BRIGHTDATA_REQUEST_MAX_ATTEMPTS:
                suffix = f" after {attempt} attempts" if attempt > 1 else ""
                raise DataSyncError(f"{exc}{suffix}") from exc
            exponential_delay = BRIGHTDATA_RETRY_BASE_SECONDS * (2 ** (attempt - 1))
            retry_delay = max(exc.retry_after or 0.0, exponential_delay)
            retry_delay += random.uniform(0.0, BRIGHTDATA_RETRY_BASE_SECONDS)
            LOGGER.warning(
                "Transient Bright Data HTTP %s during %s; retrying attempt %s/%s in %.2fs",
                exc.status_code,
                method,
                attempt + 1,
                BRIGHTDATA_REQUEST_MAX_ATTEMPTS,
                retry_delay,
            )
            await asyncio.sleep(retry_delay)
        except (TimeoutError, URLError) as exc:
            # Network-level failures (DNS, connection reset, socket timeout)
            # get the same retry treatment as a retryable HTTP status,
            # instead of aborting immediately with zero retries.
            if attempt == BRIGHTDATA_REQUEST_MAX_ATTEMPTS:
                suffix = f" after {attempt} attempts" if attempt > 1 else ""
                raise DataSyncError(f"Bright Data request failed: {exc}{suffix}") from exc
            exponential_delay = BRIGHTDATA_RETRY_BASE_SECONDS * (2 ** (attempt - 1))
            retry_delay = exponential_delay + random.uniform(0.0, BRIGHTDATA_RETRY_BASE_SECONDS)
            LOGGER.warning(
                "Transient Bright Data network error during %s; retrying attempt %s/%s in %.2fs",
                method,
                attempt + 1,
                BRIGHTDATA_REQUEST_MAX_ATTEMPTS,
                retry_delay,
            )
            await asyncio.sleep(retry_delay)
    raise AssertionError("Bright Data retry loop exited unexpectedly")


def _brightdata_json_request_blocking(
    endpoint: str,
    api_key: str,
    method: str,
    body: Any,
    timeout_seconds: float,
) -> Any:
    payload = json.dumps(body).encode("utf-8") if body is not None else None
    request = Request(endpoint, data=payload, method=method)
    request.add_header("Authorization", f"Bearer {api_key}")
    request.add_header("Accept", "application/json")
    if payload is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            content = response.read().decode("utf-8")
    except HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")
        retry_after: float | None = None
        if exc.headers is not None:
            try:
                retry_after = float(exc.headers.get("Retry-After", ""))
            except (TypeError, ValueError):
                retry_after = None
        raise _BrightDataHTTPError(exc.code, details, retry_after) from exc
    # TimeoutError/URLError propagate as-is so the async retry loop in
    # _brightdata_json_request can retry them like a transient HTTP error,
    # instead of being pre-wrapped into a DataSyncError it can't catch.
    try:
        return json.loads(content)
    except json.JSONDecodeError as exc:
        if content.strip() == "OK":
            return content.strip()
        raise DataSyncError("Bright Data API returned invalid JSON") from exc


def _required_environment_value(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value or "YOUR_ACTUAL_" in value.upper():
        raise DataSyncError(f"{name} is not configured in the environment")
    return value


def resolve_indeed_market(
    geographic_zone: str,
    *,
    country: str | None = None,
    domain: str | None = None,
) -> tuple[str, str]:
    country_code = str(country or "").strip().upper()
    if not country_code:
        normalized_zone = geographic_zone.strip().casefold()
        # Longest hint first so "united kingdom" is not shadowed by a shorter
        # substring match from another market.
        country_code = next(
            (
                code
                for hint, code in sorted(
                    INDEED_LOCATION_COUNTRY_HINTS.items(),
                    key=lambda item: len(item[0]),
                    reverse=True,
                )
                if hint in normalized_zone
            ),
            "",
        )
    if not country_code:
        # No silent default: guessing a market would quietly search the wrong
        # country and bill for it. The profile knows its own locations.
        raise DataSyncError(
            f"Could not determine an Indeed market for location {geographic_zone!r}; "
            "set sources.indeed_brightdata.options.country (and optionally .domain) "
            "for this profile"
        )
    indeed_domain = str(domain or "").strip().lower() or INDEED_MARKETS.get(country_code, "")
    if not indeed_domain:
        raise DataSyncError(
            f"No Indeed domain is configured for country {country_code!r}; "
            "set sources.indeed_brightdata.options.domain for this profile"
        )
    return country_code, indeed_domain


def brightdata_date_posted_filter(post_age_hours: int | None) -> str:
    """Map the local freshness window to the nearest supported Indeed preset."""
    if post_age_hours is None or post_age_hours <= 0:
        return ""
    if post_age_hours <= 24:
        return "Last 24 hours"
    if post_age_hours <= 72:
        return "Last 3 days"
    if post_age_hours <= 168:
        return "Last 7 days"
    if post_age_hours <= 336:
        return "Last 14 days"
    return ""


def _adaptive_poll_interval(elapsed_seconds: float) -> float:
    if elapsed_seconds < 60:
        return 5.0
    if elapsed_seconds < 300:
        return 15.0
    return 30.0


def _brightdata_record_context(
    entry: Mapping[str, Any],
    search_inputs: Sequence[BrightDataSearchInput],
) -> tuple[str, str]:
    for key in ("input", "discovery_input", "discover_input"):
        candidate = entry.get(key)
        if isinstance(candidate, Mapping):
            query = _first_text(candidate, "keyword_search", "query", "keyword", default="")
            location = _first_text(candidate, "location", "geographic_zone", default="")
            if query or location:
                return query, location

    query = _first_text(entry, "keyword_search", "search_query", default="")
    location = _first_text(entry, "search_location", default="")
    if query or location:
        return query, location
    if len(search_inputs) == 1:
        return search_inputs[0].search_query, search_inputs[0].geographic_zone
    return "", ""


class IndeedBrightDataCollector(BaseCollector):
    """Feed Bright Data's live Indeed dataset into the shared RawJobRecord pipeline."""

    source_name = "indeed"

    def __init__(
        self,
        http_config: HttpConfig,
        source_config: SourceConfig,
        *,
        batch_sync_runner: Callable[..., Any] | None = None,
        snapshot_database: Database | None = None,
        event_logger: Callable[[str], None] | None = None,
    ) -> None:
        super().__init__(http_config, source_config)
        self.batch_sync_runner = batch_sync_runner or execute_brightdata_dataset_batch_sync
        self.snapshot_database = snapshot_database
        self.event_logger = event_logger
        self._uses_live_environment = batch_sync_runner is None

    def validate_runtime(self) -> None:
        if self._uses_live_environment:
            _required_environment_value("BRIGHTDATA_API_KEY")
            _required_environment_value("BRIGHTDATA_DATASET_ID")

    def collect(self, window: SearchWindow) -> Iterable[RawJobRecord]:
        locations = list(self.source_config.locations)
        date_posted = brightdata_date_posted_filter(window.post_age_hours)
        search_inputs: list[BrightDataSearchInput] = []
        options = self.source_config.options
        configured_country = str(options.get("country") or "").strip() or None
        configured_domain = str(options.get("domain") or "").strip() or None
        for query in self.source_config.search_queries:
            for location in locations:
                country, domain = resolve_indeed_market(
                    location,
                    country=configured_country,
                    domain=configured_domain,
                )
                search_inputs.append(
                    BrightDataSearchInput(
                        search_query=query,
                        geographic_zone=location,
                        country=country,
                        domain=domain,
                        date_posted=date_posted,
                    )
                )
        if not search_inputs:
            return

        result = self.batch_sync_runner(
            search_inputs,
            limit_per_input=self.source_config.max_detail_fetches,
            limit_multiple_results=self.source_config.max_detail_fetches,
            output_path=None,
            snapshot_database=self.snapshot_database,
            request_timeout_seconds=self.http_config.timeout_seconds,
            event_logger=self.event_logger,
        )
        batch = asyncio.run(result) if isinstance(result, Coroutine) else result
        if isinstance(batch, BrightDataBatchResult):
            records = batch.records
            snapshot_ids = batch.snapshot_ids or ([batch.snapshot_id] if batch.snapshot_id else [])
            mark_consumed_on_collect = batch.mark_consumed_on_collect
        else:
            records = batch
            snapshot_ids = []
            mark_consumed_on_collect = True

        selected_records = _select_brightdata_records(
            records,
            maximum_records_per_query=self.source_config.max_detail_fetches,
        )
        completed = False
        try:
            for record in selected_records:
                query = str(record.get("brightdata_query") or "").strip()
                location = str(record.get("brightdata_location") or "").strip()
                yield _to_raw_job(
                    record,
                    query,
                    location,
                    transport="brightdata_dataset_api",
                )
            completed = True
        finally:
            if completed and mark_consumed_on_collect and self.snapshot_database is not None:
                for snapshot_id in snapshot_ids:
                    self.snapshot_database.mark_snapshot_consumed(snapshot_id)


def _select_brightdata_records(
    records: Iterable[Mapping[str, Any]],
    *,
    maximum_records_per_query: int,
) -> list[dict[str, Any]]:
    """Keep dedup'd records, capped per originating query.

    A per-query budget (rather than one flat total) means a broad keyword
    that alone matches hundreds of postings can't crowd out every other
    keyword's results -- each query gets its own allowance.
    """
    selected: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    counts_by_query: dict[str, int] = {}
    for record in records:
        source_job_id = str(record.get("record_id") or "").strip()
        if not source_job_id or source_job_id in seen_ids:
            continue
        query = str(record.get("brightdata_query") or "").strip()
        if counts_by_query.get(query, 0) >= maximum_records_per_query:
            continue
        seen_ids.add(source_job_id)
        counts_by_query[query] = counts_by_query.get(query, 0) + 1
        selected.append(dict(record))
    return selected


def _to_raw_job(
    record: Mapping[str, Any],
    query: str,
    location: str,
    *,
    transport: str = "brightdata_dataset_api",
) -> RawJobRecord:
    reference_url = str(record.get("reference_url") or "").strip()
    raw_payload = record.get("raw_payload")
    payload = dict(raw_payload) if isinstance(raw_payload, Mapping) else {}
    payload.update({"query": query, "search_location": location, "transport": transport})
    return RawJobRecord(
        source="indeed",
        source_job_id=str(record.get("record_id") or "").strip(),
        source_url=reference_url,
        canonical_url=reference_url,
        title=str(record.get("position_title") or "").strip(),
        company_name=str(record.get("organization") or "").strip(),
        location_raw=str(record.get("region") or "").strip(),
        posted_at_text=str(record.get("timestamp") or "").strip(),
        scraped_at=datetime.now(UTC),
        job_description=str(record.get("description") or "").strip(),
        salary_text=str(record.get("compensation_package") or "").strip(),
        employment_type=str(record.get("employment_type") or "unknown").strip() or "unknown",
        raw_payload=payload,
    )


def _first_text(entry: Mapping[str, Any], *keys: str, default: str = "N/A") -> str:
    for key in keys:
        value = entry.get(key)
        if isinstance(value, Mapping):
            continue
        if value is not None and str(value).strip():
            return str(value).strip()
    return default


def _organization_text(entry: Mapping[str, Any]) -> str:
    value = entry.get("company")
    if isinstance(value, Mapping):
        for key in ("name", "display_name", "company_name"):
            if value.get(key):
                return str(value[key]).strip()
    return _first_text(entry, "organization", "company", "companyName", "company_name")


def _compensation_text(entry: Mapping[str, Any]) -> str:
    scalar = _first_text(
        entry,
        "compensation_package",
        "salaryText",
        "formattedSalary",
        "salary",
        "compensation",
        "job_base_pay_range",
        default="",
    )
    if scalar:
        return scalar
    pay_range = entry.get("job_base_pay_range")
    if not isinstance(pay_range, Mapping):
        return "Not specified"
    minimum = _first_text(pay_range, "min_amount", "min", "minimum", default="")
    maximum = _first_text(pay_range, "max_amount", "max", "maximum", default="")
    currency = _first_text(pay_range, "currency", "currency_code", default="")
    interval = _first_text(pay_range, "unit", "interval", default="")
    amount = " - ".join(part for part in (minimum, maximum) if part)
    suffix = " ".join(part for part in (currency, interval) if part)
    return " ".join(part for part in (amount, suffix) if part) or "Not specified"


def _location_text(entry: Mapping[str, Any]) -> str:
    for key in ("extractedLocation", "location"):
        extracted = entry.get(key)
        if isinstance(extracted, Mapping):
            parts = [
                str(extracted.get(part) or "").strip() for part in ("city", "state", "country")
            ]
            value = ", ".join(part for part in parts if part)
            if value:
                return value
        elif extracted is not None and str(extracted).strip():
            return str(extracted).strip()
    return _first_text(entry, "region", "formattedLocation", "location_name", "job_location")


def _sequence_text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if not isinstance(value, Sequence):
        return ""
    parts: list[str] = []
    for item in value:
        if isinstance(item, Mapping):
            text = _first_text(item, "label", "text", "value", default="")
        else:
            text = str(item).strip()
        if text:
            parts.append(text)
    return ", ".join(parts)


def _write_json(output_path: Path, records: Iterable[Mapping[str, Any]]) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_suffix(output_path.suffix + ".tmp")
    temporary.write_text(json.dumps(list(records), ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(output_path)


def _log_cloud_event(
    message: str,
    event_logger: Callable[[str], None] | None = None,
) -> None:
    LOGGER.info("Indeed Bright Data | %s", message)
    if event_logger is not None:
        event_logger(f"Indeed Bright Data | {message}")
