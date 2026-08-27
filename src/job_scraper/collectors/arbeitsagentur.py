"""Direct reads of Germany's public statutory employment-service job search API.

Every acquisition source before this one reads a commercial, ranked surface.
This one reads a public, unmetered, unauthenticated search API whose
population skews toward small employers that never advertise commercially.
See docs/public/specs/2026-08-27-public-employment-agency-source.md.

Field and parameter names below were validated against one live query/detail
pair on 2026-08-27. They are worth re-checking after any endpoint move,
because the two endpoints are versioned independently and their payloads do
not share a naming convention: the same posting is `referenznummer` in the
search response and reached by base64 of that value in the detail path, and
its text is `stellenangebotsBeschreibung` in detail where the search response
has no description at all.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from urllib.error import HTTPError
from urllib.parse import quote, urlencode

from job_scraper.collectors.base import BaseCollector, SearchWindow
from job_scraper.domain.models import RawJobRecord

# The fixed public client identifier the provider's own web client sends;
# not a per-installation credential, so it is a constant rather than
# environment configuration.
CLIENT_ID_HEADER = "X-API-Key"
CLIENT_ID = "jobboerse-jobsuche"

BASE_URL = "https://rest.arbeitsagentur.de/jobboerse/jobsuche-service"
# Defaults reflect the versions in effect when this source was written. The
# provider versions the two endpoints independently -- search has already
# moved once while detail did not -- so both are configured as independently
# pinned paths (`sources.arbeitsagentur_direct.options.search_path` /
# `.detail_path`) rather than hardcoded, and a future move needs a config
# change here, not a code change.
DEFAULT_SEARCH_PATH = "pc/v6/jobs"
DEFAULT_DETAIL_PATH = "pc/v4/jobdetails"
PAGE_SIZE = 25


class ArbeitsagenturEndpointError(Exception):
    """A named, non-retried failure distinct from a rejected credential.

    A 403 from this API has meant the endpoint version moved, not that the
    fixed public client identifier was rejected -- retrying it or reporting
    it as an authentication failure sends whoever investigates it looking
    for a key that does not exist.
    """


class ArbeitsagenturDirectCollector(BaseCollector):
    source_name = "arbeitsagentur"

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
        options = source_config.options
        self.exclude_private_intermediary = bool(options.get("exclude_private_intermediary", False))
        self.exclude_temporary_employment = bool(options.get("exclude_temporary_employment", False))
        self.search_path = str(options.get("search_path") or DEFAULT_SEARCH_PATH).strip("/")
        self.detail_path = str(options.get("detail_path") or DEFAULT_DETAIL_PATH).strip("/")
        self.event_logger = event_logger

    def collect(self, window: SearchWindow) -> Iterable[RawJobRecord]:
        del window  # freshness is filtered downstream by the pipeline, like every other source
        query_tasks = [
            (location, query) for location in self.locations for query in self.search_queries
        ]
        if not query_tasks:
            return
        seen_refnr: set[str] = set()
        for location, query in query_tasks:
            try:
                yield from self._collect_query(location, query, seen_refnr)
            except Exception as exc:
                # One failing query fails that query only and leaves the run
                # intact (docs/public/specs/2026-08-12-acquisition-reliability-hardening.md).
                self._emit(
                    f'arbeitsagentur | Location "{location}" | Query "{query}" failed: {exc}'
                )
                continue

    def _collect_query(
        self,
        location: str,
        query: str,
        seen_refnr: set[str],
    ) -> list[RawJobRecord]:
        records: list[RawJobRecord] = []
        for page in range(self.source_config.max_listing_pages):
            postings = self._search_page(query, location, page)
            if not postings:
                # A query returning zero results is a normal empty result,
                # not an error -- and end of pagination, not a failure.
                break
            for posting in postings:
                refnr = str(posting.get("referenznummer") or "").strip()
                if not refnr or refnr in seen_refnr:
                    continue
                seen_refnr.add(refnr)
                record = self._fetch_detail(posting, refnr)
                if record is not None:
                    record.raw_payload["query"] = query
                    record.raw_payload["search_location"] = location
                    records.append(record)
            if len(postings) < PAGE_SIZE:
                break
            if page + 1 < self.source_config.max_listing_pages:
                self.rate_limiter.sleep()
        return records

    def _search_page(self, query: str, location: str, page: int) -> list[dict]:
        params: dict[str, object] = {
            "was": query,
            "wo": location,
            "page": page + 1,
            "size": PAGE_SIZE,
        }
        # Both exclusions are applied by the API rather than by inspecting each
        # posting. The corresponding per-posting flags are optional in both
        # payloads -- a posting that omits one is indistinguishable from a
        # posting that sets it false -- so filtering here is the only form that
        # cannot silently under-exclude, and it spends no detail request on a
        # posting that is about to be dropped. Unknown parameters are ignored
        # silently by this API, so each name below was confirmed to change the
        # result count before being relied on.
        if self.exclude_private_intermediary:
            params["pav"] = "false"
        if self.exclude_temporary_employment:
            params["zeitarbeit"] = "false"
        url = f"{BASE_URL}/{self.search_path}?{urlencode(params)}"
        body = self._fetch("search", url)
        payload = json.loads(body)
        postings = payload.get("ergebnisliste") or []
        return [posting for posting in postings if isinstance(posting, dict)]

    def _fetch_detail(self, posting: dict, refnr: str) -> RawJobRecord | None:
        # The detail path keys the posting reference in base64, not as the
        # bare reference: sending the bare value is a 404, not an error that
        # names the cause.
        encoded = quote(base64.b64encode(refnr.encode()).decode(), safe="")
        url = f"{BASE_URL}/{self.detail_path}/{encoded}"
        try:
            body = self._fetch("detail", url)
        except Exception:
            # A detail fetch that fails drops that posting, not the listing
            # page that found it: without it there is no full description
            # text, and every accepted posting must carry one.
            return None
        detail = json.loads(body)
        return _to_raw_record(posting, detail)

    def _fetch(self, endpoint_name: str, url: str) -> str:
        try:
            return self.fetch_text(url, headers={CLIENT_ID_HEADER: CLIENT_ID})
        except HTTPError as exc:
            if exc.code == 403:
                raise ArbeitsagenturEndpointError(
                    f"{endpoint_name} endpoint returned 403 for {url} -- this has meant the "
                    "endpoint path moved, not a rejected credential"
                ) from exc
            raise

    def _emit(self, message: str) -> None:
        if self.event_logger is not None:
            self.event_logger(message)


def _first_address(posting: dict, detail: dict) -> dict:
    """The posting's address, which both payloads carry as a list of locations.

    A posting can name several work locations; the search matched on one of
    them and the API does not say which, so the first is taken. Preferring the
    search payload keeps the address consistent with the location that found
    it when the two disagree.
    """
    for source in (posting, detail):
        locations = source.get("stellenlokationen")
        if isinstance(locations, list):
            for entry in locations:
                if isinstance(entry, dict) and isinstance(entry.get("adresse"), dict):
                    return entry["adresse"]
    return {}


def _to_raw_record(posting: dict, detail: dict) -> RawJobRecord:
    refnr = str(posting.get("referenznummer") or detail.get("referenznummer") or "")
    employer = str(posting.get("firma") or detail.get("firma") or "").strip()
    address = _first_address(posting, detail)
    # The country belongs in the text, not only in the resolved country code.
    # Downstream country checks short-circuit on a country name found *inside*
    # the location text and otherwise fall back to matching it against a list
    # of known places -- which a small town is not on. Every other source
    # carries a country word here because its surface writes one; this one
    # gives a bare town, so without `land` a posting in a small German town is
    # judged not to be in Germany despite a `DE` country code.
    location_text = ", ".join(
        part
        for part in [
            str(address.get("ort") or "").strip(),
            str(address.get("plz") or "").strip(),
            str(address.get("land") or "").strip().title(),
        ]
        if part
    )
    # Only the detail payload carries description text, and it names the field
    # differently from every other field the two payloads share -- reading the
    # search response's naming here yields an empty description without failing.
    description = str(detail.get("stellenangebotsBeschreibung") or "").strip()
    posting_url = f"https://www.arbeitsagentur.de/jobsuche/jobdetail/{refnr}"
    # Both flags are optional in both payloads. `None` means the provider did
    # not say, which is not the same as false, so it is preserved rather than
    # coerced -- the exclusions themselves are applied by the API at search
    # time and do not depend on these.
    is_intermediary = detail.get("istPrivateArbeitsvermittlung")
    is_temporary = detail.get("istArbeitnehmerUeberlassung")
    return RawJobRecord(
        source=ArbeitsagenturDirectCollector.source_name,
        source_job_id=refnr,
        source_url=posting_url,
        canonical_url=posting_url,
        title=str(
            posting.get("stellenangebotsTitel")
            or detail.get("stellenangebotsTitel")
            or posting.get("hauptberuf")
            or ""
        ).strip(),
        company_name=employer,
        location_raw=location_text,
        posted_at_text=str(
            posting.get("datumErsteVeroeffentlichung")
            or detail.get("datumErsteVeroeffentlichung")
            or ""
        ),
        scraped_at=datetime.now(UTC),
        job_description=description,
        employment_type="temporary" if is_temporary else "unknown",
        raw_payload={
            "is_private_intermediary": is_intermediary,
            "is_temporary_employment": is_temporary,
            # The employer's own application page when the posting carries one.
            # Not the canonical url: this one is absent from most postings and
            # points off the surface that identifies the posting.
            "external_url": str(posting.get("externeURL") or "").strip() or None,
        },
    )
