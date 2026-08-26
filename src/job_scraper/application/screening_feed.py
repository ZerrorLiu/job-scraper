"""Serving the downstream screening contract.

A downstream screener needs three things this project already knows: which jobs
appeared in a window, where each one was published, and whether it has been
applied to. Until now the only way to get them was to open this project's
SQLite files directly, which made the reader depend on table names, on the
`data/jobs_<profile>.db` filename convention, and on which store currently owns
publication state -- today that is still the V1 `notion_sync_state`, because
V2's `external_publications` is a frozen migration snapshot.

Serving a versioned document instead moves the dependency onto a shape this
project promises to keep. The V1/V2 split can then be resolved without the
downstream reader noticing: `SCHEMA_VERSION` tracks the shape, and storage
moves do not change the shape.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from typing import Any

from job_scraper.domain.screening_feed import SCHEMA_VERSION, ScreeningFeedRecord


def build_feed_document(
    records: list[ScreeningFeedRecord],
    *,
    since: datetime,
    until: datetime,
    generated_at: datetime,
) -> dict[str, Any]:
    """Wrap records in the envelope the contract promises.

    The window travels with the records because a screener that caches results
    needs to know what it was handed, and reconstructing the window from the
    records themselves is wrong exactly when the window came back empty.
    """
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at.isoformat(),
        "window": {"since": since.isoformat(), "until": until.isoformat()},
        "record_count": len(records),
        "records": [asdict(record) for record in records],
    }


def select_screenable(
    records: list[ScreeningFeedRecord],
    *,
    published_only: bool,
    excluded_statuses: tuple[str, ...],
) -> list[ScreeningFeedRecord]:
    """Narrow a feed the way a generating screener usually wants it.

    Kept here rather than left to each caller so that "what job-scraper
    considers screenable" has one definition, while the full feed stays
    available to a reader that wants to see settled or unpublished jobs.
    """
    normalized = {status.strip().casefold() for status in excluded_statuses if status.strip()}
    selected = []
    for record in records:
        if published_only and not record.publication.is_published:
            continue
        if record.application_status.strip().casefold() in normalized:
            continue
        selected.append(record)
    return selected
