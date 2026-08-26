from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, datetime
from html import unescape
from urllib.parse import urlencode

from job_scraper.adapters.jobposting_jsonld import (
    extract_city as _extract_city,
)
from job_scraper.adapters.jobposting_jsonld import (
    extract_country as _extract_country,
)
from job_scraper.adapters.jobposting_jsonld import (
    extract_job_locations as _extract_job_locations,
)
from job_scraper.adapters.jobposting_jsonld import (
    extract_jobposting as _extract_json_ld_jobposting,
)
from job_scraper.application.acquisition import RequestCoalescer, RequestGate
from job_scraper.collectors.base import BaseCollector, SearchWindow
from job_scraper.domain.models import RawJobRecord
from job_scraper.pipeline.role_filter import company_matches_allowlist


@dataclass(frozen=True, slots=True)
class LinkedInProgressEvent:
    completed_queries: int
    total_queries: int
    query: str
    listings: int


class LinkedInCollector(BaseCollector):
    source_name = "linkedin"
    SEARCH_URL = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
    DETAIL_TEMPLATE = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}"

    def __init__(
        self,
        http_config,
        source_config,
        company_names: list[str] | None = None,
        *,
        request_coalescer: RequestCoalescer | None = None,
        request_gate: RequestGate | None = None,
        event_logger: Callable[[str], None] | None = None,
        progress_callback: Callable[[LinkedInProgressEvent], None] | None = None,
    ) -> None:
        super().__init__(http_config, source_config)
        self.search_queries = list(source_config.search_queries)
        self.locations = list(source_config.locations)
        self.company_names = company_names or []
        self.request_coalescer = request_coalescer
        self.request_gate = request_gate
        self.event_logger = event_logger
        self.progress_callback = progress_callback

    def collect(self, window: SearchWindow) -> Iterable[RawJobRecord]:
        query_tasks = [
            (location, query) for location in self.locations for query in self.search_queries
        ]
        if not query_tasks:
            return

        query_results: list[list[RawJobRecord]] = [[] for _ in query_tasks]
        worker_count = min(self.source_config.query_workers, len(query_tasks))
        if worker_count <= 1:
            for index, (location, query) in enumerate(query_tasks):
                query_results[index] = self._collect_query(window, location, query)
                self._report_query_progress(
                    index + 1, len(query_tasks), query, query_results[index]
                )
        else:
            with ThreadPoolExecutor(
                max_workers=worker_count,
                thread_name_prefix="linkedin-query",
            ) as executor:
                futures = {
                    executor.submit(self._collect_query, window, location, query): (index, query)
                    for index, (location, query) in enumerate(query_tasks)
                }
                for completed, future in enumerate(as_completed(futures), start=1):
                    index, query = futures[future]
                    query_results[index] = future.result()
                    self._report_query_progress(
                        completed,
                        len(query_tasks),
                        query,
                        query_results[index],
                    )

        selected = _deduplicate_round_robin(
            query_results,
            limit=self.source_config.max_detail_fetches,
        )
        yield from self._fetch_detail_batch(selected)

    def _collect_query(
        self,
        window: SearchWindow,
        location: str,
        query: str,
    ) -> list[RawJobRecord]:
        self._emit(f'LinkedIn | Location "{location}" | Query "{query}" | Start')
        records: list[RawJobRecord] = []
        for page in range(self.source_config.max_listing_pages):
            url = self.build_search_url(
                query=query,
                location=location,
                start=page * 25,
                post_age_hours=window.post_age_hours,
            )
            try:
                html = self._fetch_shared(url)
            except Exception as exc:
                self._emit(
                    f'LinkedIn | Location "{location}" | Query "{query}" | '
                    f"Page {page + 1} blocked: {exc}"
                )
                break
            for record in self.parse_listings(html):
                record.raw_payload["query"] = query
                record.raw_payload["search_location"] = location
                if company_matches_allowlist(record.company_name, self.company_names):
                    records.append(record)
            if page + 1 < self.source_config.max_listing_pages:
                self.rate_limiter.sleep()
        return records

    def _fetch_shared(self, url: str) -> str:
        def fetch() -> str:
            return self.fetch_text(url)

        def operation() -> str:
            if self.request_gate is not None:
                return self.request_gate.execute(fetch)
            return fetch()

        if self.request_coalescer is None:
            return operation()
        return self.request_coalescer.execute(
            f"linkedin:text:{url}",
            operation,
        )

    def _report_query_progress(
        self,
        completed: int,
        total: int,
        query: str,
        records: list[RawJobRecord],
    ) -> None:
        if self.progress_callback is not None:
            self.progress_callback(
                LinkedInProgressEvent(
                    completed_queries=completed,
                    total_queries=total,
                    query=query,
                    listings=len(records),
                )
            )
        self._emit(
            f'LinkedIn | Query "{query}" | Done | Listings {len(records)} | '
            f"Keywords {completed}/{total}"
        )

    def build_search_url(
        self,
        query: str,
        location: str,
        start: int,
        post_age_hours: int | None = None,
    ) -> str:
        params = {
            "keywords": query,
            "location": location,
        }
        if post_age_hours is None:
            params["f_TPR"] = "r86400"
        elif post_age_hours > 0:
            params["f_TPR"] = f"r{post_age_hours * 3600}"
        params["start"] = str(start)
        return f"{self.SEARCH_URL}?{urlencode(params)}"

    def _fetch_detail_batch(self, records: list[RawJobRecord]) -> Iterable[RawJobRecord]:
        if not records:
            return []

        worker_count = max(1, self.source_config.detail_workers)
        if worker_count == 1 or len(records) == 1:
            return [self._fetch_detail(record) for record in records]

        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            return list(executor.map(self._fetch_detail, records))

    def _fetch_detail(self, record: RawJobRecord) -> RawJobRecord:
        try:
            detail_html = self._fetch_shared(
                self.DETAIL_TEMPLATE.format(job_id=record.source_job_id)
            )
            return self.parse_detail(detail_html, record)
        except Exception as exc:
            record.raw_payload["detail_error"] = "detail_fetch_blocked"
            record.raw_payload["detail_error_message"] = str(exc)
            return record

    def _emit(self, message: str) -> None:
        if self.event_logger is not None:
            self.event_logger(message)

    def parse_listings(self, html: str) -> list[RawJobRecord]:
        records: list[RawJobRecord] = []
        cards = re.findall(r'(<div class="base-card[\s\S]*?</li>)', html, re.DOTALL)
        for card in cards:
            href_match = re.search(
                r'href="(https://(?:[a-z]{2}\.)?linkedin\.com/jobs/view/[^"]+)"', card
            )
            title_match = re.search(
                r"base-search-card__title[^>]*>\s*([\s\S]*?)\s*</h3>", card, re.DOTALL
            )
            company_match = re.search(
                r"base-search-card__subtitle[^>]*>[\s\S]*?<a[^>]*>([\s\S]*?)</a>", card, re.DOTALL
            )
            location_match = re.search(
                r"job-search-card__location[^>]*>\s*([\s\S]*?)\s*</span>", card, re.DOTALL
            )
            posted_match = re.search(
                r'<time[^>]*datetime="([^"]+)"[^>]*>\s*([\s\S]*?)\s*</time>', card, re.DOTALL
            )
            if not (href_match and title_match and company_match and location_match):
                continue
            source_url = unescape(href_match.group(1))
            source_job_id = _extract_job_id(source_url)
            posted_at_text = ""
            if posted_match:
                posted_visible = _strip_tags(posted_match.group(2))
                posted_at_text = posted_visible or posted_match.group(1)
            records.append(
                RawJobRecord(
                    source=self.source_name,
                    source_job_id=source_job_id,
                    source_url=source_url,
                    canonical_url=source_url,
                    title=_strip_tags(title_match.group(1)),
                    company_name=_strip_tags(company_match.group(1)),
                    location_raw=_strip_tags(location_match.group(1)),
                    posted_at_text=posted_at_text,
                    scraped_at=datetime.now(UTC),
                    raw_payload={"listing_url": source_url},
                )
            )
        return records

    def parse_detail(self, html: str, seed: RawJobRecord) -> RawJobRecord:
        payload = _extract_json_ld_jobposting(html)
        if payload:
            seed.job_description = _html_to_text(payload.get("description", ""))
            detail_locations = _extract_job_locations(payload)
            if detail_locations:
                seed.location_raw = " | ".join(detail_locations)
                seed.raw_payload["location_options"] = detail_locations
            detail_country = _extract_country(payload)
            if detail_country:
                seed.raw_payload["location_country"] = detail_country
            detail_city = _extract_city(payload)
            if detail_city:
                seed.raw_payload["location_city"] = detail_city
            return seed
        match = re.search(
            r'<div class="show-more-less-html__markup[^"]*">(.*?)</div>', html, re.DOTALL
        )
        if match:
            seed.job_description = _strip_tags(match.group(1))
        return seed


def _strip_tags(value: str) -> str:
    return re.sub(r"<[^>]+>", " ", unescape(value)).strip()


def _extract_job_id(url: str) -> str:
    match = re.search(r"-(\d+)(?:\?|$)", url)
    if match:
        return match.group(1)
    return url.rstrip("/").split("/")[-1]


def _html_to_text(value: str) -> str:
    return _strip_tags(unescape(value))


def _deduplicate_round_robin(
    batches: list[list[RawJobRecord]],
    *,
    limit: int,
) -> list[RawJobRecord]:
    selected: list[RawJobRecord] = []
    seen_ids: set[str] = set()
    row = 0
    while len(selected) < limit:
        found_candidate = False
        for batch in batches:
            if row >= len(batch):
                continue
            found_candidate = True
            record = batch[row]
            if record.source_job_id in seen_ids:
                continue
            seen_ids.add(record.source_job_id)
            selected.append(record)
            if len(selected) >= limit:
                break
        if not found_candidate:
            break
        row += 1
    return selected
