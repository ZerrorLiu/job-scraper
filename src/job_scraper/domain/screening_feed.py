"""The record shape a downstream screener depends on.

These types are the published contract, so they live in `domain/` where both
the store that fills them and the application layer that serves them may depend
on them. Putting them in `application/` would have forced `storage/` to import
upward, which is the layering the rest of this package deliberately avoids.
"""

from __future__ import annotations

from dataclasses import dataclass

SCHEMA_VERSION = 2

# Statuses that mean "this job is settled". A screener that generates
# applications has nothing left to do for them, but whether to skip them is the
# caller's decision, so this is a documented default rather than a filter baked
# into the query.
SETTLED_STATUSES = ("applied", "not_interested")


@dataclass(frozen=True, slots=True)
class Publication:
    """Where one job was published by one sink.

    `container_id` is the sink's grouping object -- for Notion, the daily
    database the page lives in. Empty strings mean unpublished rather than
    unknown: a sink that has never run leaves every field empty.
    """

    sink_id: str = ""
    external_id: str = ""
    container_id: str = ""

    @property
    def is_published(self) -> bool:
        return bool(self.external_id)


@dataclass(frozen=True, slots=True)
class ScreeningFeedRecord:
    """One job as a downstream screener sees it.

    Deliberately narrower than the full job row: this is what a screener needs
    in order to decide and to write its result back, not an export of
    everything stored. Adding a field is a compatible change; removing one or
    repurposing its meaning is not, and bumps `SCHEMA_VERSION`.
    """

    job_id: str
    profile_id: str
    title: str
    company: str
    location: str
    language: str
    url: str
    description: str
    first_seen_at: str
    application_status: str
    publication: Publication
    processing_mode: str = "core"
