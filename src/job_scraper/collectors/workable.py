"""Token-free search across one applicant-tracking vendor's hosted boards.

`ats_direct` reads one employer's board and needs that employer's board token
first. This source needs no token: the vendor exposes a public search over
every board it hosts, taking a free-text query and a location, so employers
arrive without anyone naming them. See
docs/public/specs/2026-08-27-token-free-board-sources.md.

Field names below were validated against one live query on 2026-08-27. Two
properties of the payload are worth recording because they remove work every
other source has to do: the listing response already carries full description
text, so there is no per-posting detail request, and it carries the vendor's
own language tag, which is kept in the raw payload as corroboration for the
pipeline's own language decision rather than as a substitute for it.
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

BASE_URL = "https://jobs.workable.com/api/v1/jobs"

# The vendor's workplace vocabulary is not the pipeline's. `on_site` is the
# only value that differs by more than case, and mapping it here keeps the
# adapter boundary the only place that knows the vendor's spelling.
_WORKPLACE_TO_REMOTE_TYPE = {
    "remote": "remote",
    "hybrid": "hybrid",
    "on_site": "onsite",
}


class WorkableDirectCollector(BaseCollector):
    source_name = "workable"

    def __init__(
        self,
        http_config,
        source_config,
        *,
        event_logger: Callable[[str], None] | None = None,
    ) -> None:
        super().__init__(http_config, source_config)
        self.search_queries = list(source_config.search_queries)
        self.locations = list(source_config.locations)
        self.event_logger = event_logger

    def collect(self, window: SearchWindow) -> Iterable[RawJobRecord]:
        del window  # freshness is filtered downstream by the pipeline, like every other source
        query_tasks = [
            (location, query) for location in self.locations for query in self.search_queries
        ]
        if not query_tasks:
            return
        seen_ids: set[str] = set()
        for location, query in query_tasks:
            try:
                yield from self._collect_query(location, query, seen_ids)
            except Exception as exc:
                # One failing query fails that query only and leaves the run
                # intact (docs/public/specs/2026-08-12-acquisition-reliability-hardening.md).
                self._emit(f'workable | Location "{location}" | Query "{query}" failed: {exc}')
                continue

    def _collect_query(
        self,
        location: str,
        query: str,
        seen_ids: set[str],
    ) -> list[RawJobRecord]:
        records: list[RawJobRecord] = []
        page_token = ""
        for page in range(self.source_config.max_listing_pages):
            try:
                payload = self._search_page(query, location, page_token)
            except HTTPError as exc:
                if exc.code != 429:
                    raise
                # Paging faster than the board allows ends this query and keeps
                # what it already returned. Retrying a rate limit only spends
                # the budget faster, which is why `fetch_text` does not.
                self._emit(
                    f'workable | Location "{location}" | Query "{query}" rate limited '
                    f"at page {page + 1}; keeping {len(records)} postings"
                )
                break
            postings = [item for item in payload.get("jobs") or [] if isinstance(item, dict)]
            for posting in postings:
                posting_id = str(posting.get("id") or "").strip()
                if not posting_id or posting_id in seen_ids:
                    continue
                seen_ids.add(posting_id)
                record = _to_raw_record(posting)
                if record is None:
                    continue
                record.raw_payload["query"] = query
                record.raw_payload["search_location"] = location
                records.append(record)
            page_token = str(payload.get("nextPageToken") or "")
            if not page_token:
                # No cursor is the end of the result set, not a failure.
                break
            if page + 1 < self.source_config.max_listing_pages:
                self.rate_limiter.sleep()
        return records

    def _search_page(self, query: str, location: str, page_token: str) -> dict:
        params: dict[str, str] = {"query": query, "location": location}
        if page_token:
            params["pageToken"] = page_token
        url = f"{BASE_URL}?{urlencode(params)}"
        body = self.fetch_text(url, headers={"Accept": "application/json"})
        payload = json.loads(body)
        if not isinstance(payload, dict):
            raise ValueError("workable search response was not an object")
        return payload

    def _emit(self, message: str) -> None:
        if self.event_logger is not None:
            self.event_logger(message)


def _to_raw_record(posting: dict) -> RawJobRecord | None:
    title = str(posting.get("title") or "").strip()
    company = posting.get("company")
    company_name = (
        str((company or {}).get("title") or "").strip() if isinstance(company, dict) else ""
    )
    if not title or not company_name:
        # Every accepted posting must name its employer; emitting a blank one
        # would put an empty company into every downstream sink.
        return None
    url = str(posting.get("url") or "").strip()
    workplace = str(posting.get("workplace") or "").strip().lower()
    return RawJobRecord(
        source=WorkableDirectCollector.source_name,
        source_job_id=str(posting.get("id") or ""),
        source_url=url,
        canonical_url=url,
        title=title,
        company_name=company_name,
        location_raw=_location_text(posting.get("location")),
        posted_at_text=str(posting.get("created") or ""),
        scraped_at=datetime.now(UTC),
        job_description=_description(posting),
        employment_type=str(posting.get("employmentType") or "").strip() or "unknown",
        remote_type=_WORKPLACE_TO_REMOTE_TYPE.get(workplace, "unknown"),
        raw_payload={
            "provider": "workable",
            # The vendor's own language tag. Kept as corroboration; the
            # pipeline still decides language from the description text.
            "listing_language": str(posting.get("language") or ""),
            "company_website": str((company or {}).get("website") or ""),
        },
    )


def _location_text(location: object) -> str:
    """The posting's location, which the vendor sends as an object.

    The country belongs in the text and not only in a code: downstream country
    checks read the location string, and a bare city name is not enough to
    place a posting in a country.
    """
    if not isinstance(location, dict):
        return ""
    return ", ".join(
        part
        for part in [
            str(location.get("city") or "").strip(),
            str(location.get("region") or "").strip(),
            str(location.get("countryName") or "").strip(),
        ]
        if part
    )


def _description(posting: dict) -> str:
    """Full text, assembled from the three sections the listing carries.

    The vendor splits a posting across `description`, `requirementsSection`,
    and `benefitsSection`. Reading only the first loses the requirements --
    which is the part the pipeline's requirement rules exist to read -- so all
    three are joined here rather than at any later layer.
    """
    sections = [
        _strip_html(str(posting.get(key) or ""))
        for key in ("description", "requirementsSection", "benefitsSection")
    ]
    return "\n\n".join(section for section in sections if section)


def _strip_html(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", unescape(value))).strip()
