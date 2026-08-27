"""Token-free reads of a national job-board feed.

A whole-board feed rather than a search: there is no query matrix, only pages,
and every posting on the board is offered. See
docs/public/specs/2026-08-27-token-free-board-sources.md.

Two properties were established against the live feed on 2026-08-27 and shape
the code below. The feed answers a sub-second page loop with a rate-limit
status where it tolerated the collector's configured interval, so pages are
paced and the status ends paging rather than being retried. And `created_at`
is a Unix timestamp, not a formatted date -- carrying it through unconverted
would give the pipeline a ten-digit integer to parse as a date.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from html import unescape
from urllib.error import HTTPError
from urllib.parse import urlencode

from job_scraper.collectors.base import BaseCollector, SearchWindow
from job_scraper.domain.models import RawJobRecord

BASE_URL = "https://www.arbeitnow.com/api/job-board-api"


class ArbeitnowDirectCollector(BaseCollector):
    source_name = "arbeitnow"

    def __init__(
        self,
        http_config,
        source_config,
        *,
        event_logger: Callable[[str], None] | None = None,
    ) -> None:
        super().__init__(http_config, source_config)
        self.event_logger = event_logger

    def collect(self, window: SearchWindow) -> Iterable[RawJobRecord]:
        del window  # freshness is filtered downstream by the pipeline, like every other source
        seen_slugs: set[str] = set()
        for page in range(self.source_config.max_listing_pages):
            try:
                postings = self._fetch_page(page + 1)
            except HTTPError as exc:
                if exc.code != 429:
                    self._emit(f"arbeitnow | page {page + 1} failed: {exc}")
                    break
                self._emit(
                    f"arbeitnow | rate limited at page {page + 1}; "
                    f"keeping {len(seen_slugs)} postings"
                )
                break
            except Exception as exc:
                # One failing page ends paging and keeps what came before it,
                # rather than failing the run
                # (docs/public/specs/2026-08-12-acquisition-reliability-hardening.md).
                self._emit(f"arbeitnow | page {page + 1} failed: {exc}")
                break
            if not postings:
                # An empty page is the end of the board, not an error.
                break
            for posting in postings:
                slug = str(posting.get("slug") or "").strip()
                if not slug or slug in seen_slugs:
                    continue
                seen_slugs.add(slug)
                record = _to_raw_record(posting)
                if record is not None:
                    yield record
            if page + 1 < self.source_config.max_listing_pages:
                self.rate_limiter.sleep()

    def _fetch_page(self, page: int) -> list[dict]:
        url = f"{BASE_URL}?{urlencode({'page': page})}"
        payload = json.loads(self.fetch_text(url, headers={"Accept": "application/json"}))
        if not isinstance(payload, dict):
            raise ValueError("arbeitnow response was not an object")
        return [item for item in payload.get("data") or [] if isinstance(item, dict)]

    def _emit(self, message: str) -> None:
        if self.event_logger is not None:
            self.event_logger(message)


def _to_raw_record(posting: dict) -> RawJobRecord | None:
    title = str(posting.get("title") or "").strip()
    company_name = str(posting.get("company_name") or "").strip()
    if not title or not company_name:
        # Every accepted posting must name its employer.
        return None
    url = str(posting.get("url") or "").strip()
    job_types = posting.get("job_types")
    employment_type = ""
    if isinstance(job_types, list) and job_types:
        employment_type = str(job_types[0] or "").strip()
    return RawJobRecord(
        source=ArbeitnowDirectCollector.source_name,
        source_job_id=str(posting.get("slug") or ""),
        source_url=url,
        canonical_url=url,
        title=title,
        company_name=company_name,
        location_raw=_location_text(posting.get("location")),
        posted_at_text=_posted_at_text(posting.get("created_at")),
        scraped_at=datetime.now(UTC),
        job_description=_strip_html(str(posting.get("description") or "")),
        employment_type=employment_type or "unknown",
        remote_type="remote" if posting.get("remote") is True else "unknown",
        raw_payload={
            "provider": "arbeitnow",
            "tags": [str(tag) for tag in posting.get("tags") or []],
        },
    )


def _location_text(value: object) -> str:
    """The feed's location string, which has no single multi-location shape.

    Observed live: `"Berlin; Munich"`, `"Paris (France), Amsterdam"`, a bare
    `"Berlin"`, and an empty string. Only the semicolon reliably separates
    whole places, so a semicolon list is cut to its first entry -- the same
    choice every other multi-location source makes. Anything else is passed
    through whole rather than guessed at: a comma-separated list still carries
    its country words, which is what the pipeline's country check reads, and
    splitting on the comma would strip them.

    An empty location is left empty rather than defaulted. Placing a posting
    is the pipeline's decision, and inventing a country here would make an
    unplaceable posting look placed.
    """
    text = str(value or "").strip()
    if ";" not in text:
        return text
    return text.split(";")[0].strip()


def _posted_at_text(value: object) -> str:
    """`created_at` is a Unix timestamp; give the pipeline a date it can read."""
    if not isinstance(value, int | float) or isinstance(value, bool):
        return str(value or "")
    return datetime.fromtimestamp(float(value), tz=UTC).isoformat()


def _strip_html(value: str) -> str:
    # The feed HTML-escapes its HTML, so one unescape yields tags and a second
    # is not needed: strip after unescaping once.
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", unescape(value))).strip()
