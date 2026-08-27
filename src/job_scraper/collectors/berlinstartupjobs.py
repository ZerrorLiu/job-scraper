"""Token-free reads of a regional startup board's content API.

A whole-board feed like `arbeitnow`, but the board is a content site rather
than a job-board product, so its postings arrive as articles: the description
is rendered HTML and the employer is part of the article title. See
docs/public/specs/2026-08-27-token-free-board-sources.md.

Validated against the live board on 2026-08-27. The board does carry the
employer as a structured field, but only as a taxonomy term id, and the
endpoint that resolves ids to names answers with a forbidden status -- so the
title is the only public source of the employer name, and the separator below
is load-bearing rather than a convenience.
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

BASE_URL = "https://berlinstartupjobs.com/wp-json/wp/v2/posts"
PAGE_SIZE = 100
# The board renders every posting title as "<role> // <employer>".
COMPANY_SEPARATOR = "//"


class BerlinStartupJobsDirectCollector(BaseCollector):
    source_name = "berlinstartupjobs"

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
        seen_ids: set[str] = set()
        for page in range(self.source_config.max_listing_pages):
            try:
                postings = self._fetch_page(page + 1)
            except HTTPError as exc:
                # This API answers a page past the end with a client error
                # rather than an empty list, so that is the end of the board
                # and not a failure worth reporting as one.
                if exc.code not in (400, 404):
                    self._emit(f"berlinstartupjobs | page {page + 1} failed: {exc}")
                break
            except Exception as exc:
                self._emit(f"berlinstartupjobs | page {page + 1} failed: {exc}")
                break
            if not postings:
                break
            for posting in postings:
                # The id is an integer here, so `value or ""` would turn a
                # legitimate 0 into "no id" and silently drop the posting.
                posting_id = _identifier(posting.get("id"))
                if not posting_id or posting_id in seen_ids:
                    continue
                seen_ids.add(posting_id)
                record = _to_raw_record(posting)
                if record is None:
                    self._emit(
                        f"berlinstartupjobs | posting {posting_id} has no recoverable "
                        "company name; dropped"
                    )
                    continue
                yield record
            if len(postings) < PAGE_SIZE:
                break
            if page + 1 < self.source_config.max_listing_pages:
                self.rate_limiter.sleep()

    def _fetch_page(self, page: int) -> list[dict]:
        url = f"{BASE_URL}?{urlencode({'per_page': PAGE_SIZE, 'page': page})}"
        payload = json.loads(self.fetch_text(url, headers={"Accept": "application/json"}))
        if not isinstance(payload, list):
            raise ValueError("berlinstartupjobs response was not a list")
        return [item for item in payload if isinstance(item, dict)]

    def _emit(self, message: str) -> None:
        if self.event_logger is not None:
            self.event_logger(message)


def _to_raw_record(posting: dict) -> RawJobRecord | None:
    rendered_title = _strip_html(str((posting.get("title") or {}).get("rendered") or ""))
    title, company_name = _split_title(rendered_title)
    if not title or not company_name:
        # Without the separator there is no public way to name the employer,
        # and a posting with a blank company would put one into every
        # downstream sink. Dropping it is the honest outcome.
        return None
    url = str(posting.get("link") or "").strip()
    content = posting.get("content") or {}
    return RawJobRecord(
        source=BerlinStartupJobsDirectCollector.source_name,
        source_job_id=_identifier(posting.get("id")),
        source_url=url,
        canonical_url=url,
        title=title,
        company_name=company_name,
        # The board is single-city by construction; it publishes no per-posting
        # location, so the city it covers is the location.
        location_raw="Berlin, Germany",
        posted_at_text=str(posting.get("date_gmt") or posting.get("date") or ""),
        scraped_at=datetime.now(UTC),
        job_description=_strip_html(str(content.get("rendered") or "")),
        raw_payload={
            "provider": "berlinstartupjobs",
            "class_list": [str(item) for item in posting.get("class_list") or []],
        },
    )


def _identifier(value: object) -> str:
    return "" if value is None else str(value).strip()


def _split_title(rendered_title: str) -> tuple[str, str]:
    """Split "<role> // <employer>" into its two halves.

    An employer name may itself contain the separator (a tagline after a
    colon, a URL), so the split is on the first occurrence: everything before
    it is the role, everything after it is the employer.
    """
    if COMPANY_SEPARATOR not in rendered_title:
        return rendered_title.strip(), ""
    role, _, company = rendered_title.partition(COMPANY_SEPARATOR)
    return role.strip(), company.strip()


def _strip_html(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", unescape(value))).strip()
