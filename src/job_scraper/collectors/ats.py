"""Direct reads of employer applicant-tracking boards.

Every built-in source before this one reads a search or aggregation surface.
This one reads a public applicant-tracking board directly: a board token
maps to one endpoint returning that employer's whole current list, so cost
scales with the configured employer count rather than a query matrix. See
docs/public/specs/2026-08-27-employer-direct-source-coverage.md.

Provider support is a table of provider id -> request builder -> payload
parser (`_PROVIDERS` below). Adding a provider adds a row and a parser
function; it does not add a source or a configuration section.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from html import unescape
from urllib.error import HTTPError
from xml.etree import ElementTree

from job_scraper.adapters.jobposting_jsonld import extract_jobposting
from job_scraper.collectors.base import BaseCollector, SearchWindow
from job_scraper.domain.models import RawJobRecord


class AtsPayloadError(Exception):
    """A board's response did not match its provider's expected shape."""


@dataclass(frozen=True, slots=True)
class AtsBoard:
    provider: str
    token: str
    company_name: str = ""


def _load_boards(options: dict[str, object]) -> list[AtsBoard]:
    raw_boards = options.get("boards", [])
    if not isinstance(raw_boards, list):
        raise ValueError("sources.ats_direct.options.boards must be a list of board tables")
    boards: list[AtsBoard] = []
    for index, entry in enumerate(raw_boards):
        if not isinstance(entry, dict):
            raise ValueError(f"sources.ats_direct.options.boards[{index}] must be a table")
        provider = str(entry.get("provider", "")).strip().lower()
        token = str(entry.get("token", "")).strip()
        if provider not in _PROVIDERS:
            known = ", ".join(sorted(_PROVIDERS)) or "(none)"
            raise ValueError(
                f"sources.ats_direct.options.boards[{index}] has unknown provider "
                f"{provider!r}; known providers: {known}"
            )
        if not token:
            raise ValueError(f"sources.ats_direct.options.boards[{index}] is missing 'token'")
        boards.append(
            AtsBoard(
                provider=provider,
                token=token,
                company_name=str(entry.get("company_name", "")).strip(),
            )
        )
    return boards


class AtsDirectCollector(BaseCollector):
    source_name = "ats_direct"

    def __init__(
        self,
        http_config,
        source_config,
        *,
        event_logger: Callable[[str], None] | None = None,
    ) -> None:
        super().__init__(http_config, source_config)
        # Validated once at construction, before any request: an unknown
        # provider id is a configuration mistake, not a per-run failure.
        self.boards = _load_boards(source_config.options)
        self.event_logger = event_logger

    def collect(self, window: SearchWindow) -> Iterable[RawJobRecord]:
        del window  # every board is read in full; there is no freshness window to page against
        for index, board in enumerate(self.boards):
            # One request per board means a configured list is also a request
            # rate. Unpaced, a list of a few hundred boards is a burst at one
            # host: measured against the German small-employer provider, an
            # unpaced sweep drew rate-limit responses that a paced one did not.
            # Every other source paces between pages for the same reason.
            if index:
                self.rate_limiter.sleep()
            try:
                yield from _PROVIDERS[board.provider](self, board)
            except Exception as exc:
                # One bad token fails that token only -- the rest of the run
                # is unaffected, matching every other source's per-item
                # isolation (docs/public/specs/2026-08-12-acquisition-reliability-hardening.md).
                self._emit(f"ats_direct | board {board.token!r} ({board.provider}) failed: {exc}")
                continue

    def _emit(self, message: str) -> None:
        if self.event_logger is not None:
            self.event_logger(message)


def _collect_personio(collector: AtsDirectCollector, board: AtsBoard) -> list[RawJobRecord]:
    """Personio's public per-company XML recruiting feed.

    `https://{token}.jobs.personio.de/xml` is public and unauthenticated; a
    board token is the company's Personio subdomain. The description is a
    `jobDescriptions` element containing multiple titled `jobDescription`
    sections, not a single text node -- a flat text read returns an empty
    string rather than failing, so sections are joined explicitly below.

    Field names here follow Personio's publicly documented XML schema but
    have not been validated against a live board from this environment
    (offline tests use a fictional payload only, per this spec's own
    requirement). Validate against one real board before enabling this
    provider in production.
    """
    url = f"https://{board.token}.jobs.personio.de/xml"
    try:
        body = collector.fetch_text(url, headers={"Accept": "application/xml"})
    except HTTPError as exc:
        # A configured token fails for two reasons that need different
        # responses, and an undifferentiated "returned HTTP nnn" hides which:
        # 404 means the employer left this provider or renamed its board, so
        # the token is dead and belongs out of the configuration; 429 means the
        # sweep asked too fast and the same token will work next run. Saying
        # which is the difference between pruning a list and pacing a run.
        raise AtsPayloadError(_personio_http_reason(board.token, exc.code)) from exc
    try:
        root = ElementTree.fromstring(body)
    except ElementTree.ParseError as exc:
        raise AtsPayloadError(f"personio board {board.token!r} did not return valid XML") from exc

    company_name = board.company_name or board.token.replace("-", " ").replace("_", " ").title()
    records: list[RawJobRecord] = []
    for position in root.iter("position"):
        position_id = _child_text(position, "id")
        title = _child_text(position, "name")
        if not position_id or not title:
            continue
        description_parts = []
        for section in position.iter("jobDescription"):
            section_title = _child_text(section, "name")
            section_body = _strip_html(_child_text(section, "value"))
            if section_body:
                description_parts.append(
                    f"{section_title}\n{section_body}" if section_title else section_body
                )
        posting_url = f"https://{board.token}.jobs.personio.de/job/{position_id}"
        records.append(
            RawJobRecord(
                source=AtsDirectCollector.source_name,
                source_job_id=f"{board.token}-{position_id}",
                source_url=posting_url,
                canonical_url=posting_url,
                title=title,
                company_name=company_name,
                location_raw=_child_text(position, "office"),
                posted_at_text=_child_text(position, "createdAt"),
                scraped_at=datetime.now(UTC),
                job_description="\n\n".join(description_parts),
                employment_type=_child_text(position, "employmentType") or "unknown",
                raw_payload={"provider": "personio", "board_token": board.token},
            )
        )
    return records


def _personio_http_reason(token: str, code: int) -> str:
    if code == 404:
        return (
            f"personio board {token!r} does not exist (HTTP 404) -- the employer left "
            "this provider or renamed its board; remove the token from configuration"
        )
    if code == 429:
        return (
            f"personio board {token!r} was rate limited (HTTP 429) -- the sweep is "
            "paced too tightly; the token itself is fine and will be read next run"
        )
    return f"personio board {token!r} returned HTTP {code}"


def _collect_jsonld(collector: AtsDirectCollector, board: AtsBoard) -> list[RawJobRecord]:
    """A single career-page URL embedding a schema.org JobPosting.

    Reuses `adapters/jobposting_jsonld.extract_jobposting` -- the same
    extractor `linkedin_direct` and the email channel already use -- rather
    than writing a new JSON-LD reader. `board.token` holds the page URL for
    this provider.
    """
    url = board.token
    try:
        html = collector.fetch_text(url, headers={"Accept": "text/html"})
    except HTTPError as exc:
        raise AtsPayloadError(f"jsonld board {url!r} returned HTTP {exc.code}") from exc
    payload = extract_jobposting(html)
    if payload is None:
        raise AtsPayloadError(f"jsonld board {url!r} has no embedded JobPosting")

    title = str(payload.get("title") or "").strip()
    if not title:
        raise AtsPayloadError(f"jsonld board {url!r} JobPosting has no title")
    company_name = (
        board.company_name
        or str((payload.get("hiringOrganization") or {}).get("name") or "").strip()
    )
    identifier = payload.get("identifier")
    posting_id = (
        str(identifier.get("value", "")) if isinstance(identifier, dict) else str(identifier or "")
    ) or url
    return [
        RawJobRecord(
            source=AtsDirectCollector.source_name,
            source_job_id=posting_id,
            source_url=url,
            canonical_url=url,
            title=title,
            company_name=company_name or board.token,
            location_raw="",
            posted_at_text=str(payload.get("datePosted") or ""),
            scraped_at=datetime.now(UTC),
            job_description=_strip_html(str(payload.get("description") or "")),
            raw_payload={"provider": "jsonld"},
        )
    ]


_PROVIDERS: dict[str, Callable[[AtsDirectCollector, AtsBoard], list[RawJobRecord]]] = {
    "personio": _collect_personio,
    "jsonld": _collect_jsonld,
}


def _child_text(element: ElementTree.Element, tag: str) -> str:
    child = element.find(tag)
    if child is None or child.text is None:
        return ""
    return child.text.strip()


def _strip_html(value: str) -> str:
    return re.sub(r"<[^>]+>", " ", unescape(value)).strip()
