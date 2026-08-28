"""File contract for details rendered by an authorised local browser.

The package deliberately does not control a browser.  It emits deterministic
work items and accepts completed detail records, so a local interactive agent
can use its own browser session without exposing profile state to the project.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from urllib.parse import urlencode, urlsplit

from job_scraper.integrations.email_recommendations import (
    EmailJobCandidate,
    canonical_link_key,
    indeed_detail_url,
    platform_job_reference,
)

SCHEMA_VERSION = 1
MIN_DESCRIPTION_LENGTH = 160
TERMINAL_STATUSES = frozenset({"blocked", "unavailable"})
QUEUE_STATUSES = frozenset({"pending", "in_progress", "complete", "imported", *TERMINAL_STATUSES})
SEARCH_QUEUE_STATUSES = frozenset(
    {"pending", "in_progress", "complete", "expanded", *TERMINAL_STATUSES}
)
_BLOCKED_DESCRIPTION_MARKERS = (
    "sign in to indeed",
    "captcha",
    "access denied",
    "blocked - indeed",
)


class BrowserDetailContractError(ValueError):
    """A browser work item or result is not safe to import."""


@dataclass(frozen=True, slots=True)
class BrowserDetailTask:
    task_id: str
    url: str
    title: str
    company_name: str
    location_raw: str
    context: str
    origin: str
    observed_at: datetime
    message_id: str = ""
    email_subject: str = ""
    email_from: str = ""
    email_date: datetime | None = None
    anchor_text: str = ""
    search_task_id: str = ""
    search_query: str = ""
    search_location: str = ""

    @classmethod
    def from_candidate(cls, candidate: EmailJobCandidate) -> BrowserDetailTask:
        url = indeed_detail_url(candidate.url)
        if not url:
            raise BrowserDetailContractError("Browser detail tasks require an Indeed viewjob URL")
        _source, source_job_id = platform_job_reference(url)
        if not source_job_id:
            raise BrowserDetailContractError("Indeed browser task URL is missing its jk identifier")
        task_id = sha256(canonical_link_key(url).encode("utf-8")).hexdigest()[:24]
        return cls(
            task_id=task_id,
            url=url,
            title=candidate.title,
            company_name=candidate.company_name,
            location_raw=candidate.location_raw,
            context=candidate.context,
            origin="email",
            observed_at=candidate.email_date,
            message_id=candidate.message_id,
            email_subject=candidate.email_subject,
            email_from=candidate.email_from,
            email_date=candidate.email_date,
            anchor_text=candidate.anchor_text,
        )

    @classmethod
    def from_search_card(cls, card: BrowserSearchCard) -> BrowserDetailTask:
        url = indeed_detail_url(card.url)
        if not url or not _is_indeed_url(url):
            raise BrowserDetailContractError("Browser search cards require an Indeed viewjob URL")
        _source, source_job_id = platform_job_reference(url)
        if not source_job_id:
            raise BrowserDetailContractError("Indeed browser card URL is missing its jk identifier")
        return cls(
            task_id=sha256(canonical_link_key(url).encode("utf-8")).hexdigest()[:24],
            url=url,
            title=card.title,
            company_name=card.company_name,
            location_raw=card.location_raw,
            context=card.context,
            origin="browser_search",
            observed_at=card.task.created_at,
            search_task_id=card.task.task_id,
            search_query=card.task.query,
            search_location=card.task.location,
        )

    def to_dict(self) -> dict[str, object]:
        if self.origin == "email":
            if self.email_date is None:
                raise BrowserDetailContractError("Email browser tasks require email_date")
            return {
                "schema_version": SCHEMA_VERSION,
                "task_id": self.task_id,
                "status": "pending",
                "url": self.url,
                "title": self.title,
                "company_name": self.company_name,
                "location_raw": self.location_raw,
                "context": self.context,
                "message_id": self.message_id,
                "email_subject": self.email_subject,
                "email_from": self.email_from,
                "email_date": self.email_date.isoformat(),
                "anchor_text": self.anchor_text,
            }
        if self.origin != "browser_search":
            raise BrowserDetailContractError(f"Unsupported browser task origin {self.origin!r}")
        return {
            "schema_version": 2,
            "task_id": self.task_id,
            "status": "pending",
            "origin": self.origin,
            "url": self.url,
            "title": self.title,
            "company_name": self.company_name,
            "location_raw": self.location_raw,
            "context": self.context,
            "observed_at": self.observed_at.isoformat(),
            "search_task_id": self.search_task_id,
            "search_query": self.search_query,
            "search_location": self.search_location,
        }

    def to_candidate(self) -> EmailJobCandidate:
        if self.origin != "email" or self.email_date is None:
            raise BrowserDetailContractError("Only email browser tasks have an email candidate")
        return EmailJobCandidate(
            url=self.url,
            title=self.title,
            company_name=self.company_name,
            location_raw=self.location_raw,
            context=self.context,
            message_id=self.message_id,
            email_subject=self.email_subject,
            email_from=self.email_from,
            email_date=self.email_date,
            anchor_text=self.anchor_text,
        )


@dataclass(frozen=True, slots=True)
class BrowserDetailResult:
    task: BrowserDetailTask
    status: str
    title: str
    company_name: str
    location_raw: str
    description: str
    error: str

    @property
    def is_complete(self) -> bool:
        return self.status == "complete"

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> BrowserDetailResult:
        task = task_from_mapping(value)
        status = _required_text(value, "status").casefold()
        if status == "pending":
            raise BrowserDetailContractError("A pending browser task cannot be imported")
        if status in TERMINAL_STATUSES:
            error = _required_text(value, "error")
            return cls(task, status, "", "", "", "", error)
        if status != "complete":
            raise BrowserDetailContractError(
                "Browser result status must be complete, blocked, or unavailable"
            )
        title = _required_text(value, "title")
        company_name = _required_text(value, "company_name")
        location_raw = _required_text(value, "location_raw")
        description = _required_text(value, "description")
        if len(description) < MIN_DESCRIPTION_LENGTH:
            raise BrowserDetailContractError(
                f"Browser result description must contain at least {MIN_DESCRIPTION_LENGTH} characters"
            )
        lowered_description = description.casefold()
        if any(marker in lowered_description for marker in _BLOCKED_DESCRIPTION_MARKERS):
            raise BrowserDetailContractError(
                "Browser result appears to contain a login or blocking page"
            )
        return cls(task, status, title, company_name, location_raw, description, "")


def queue_status(value: Mapping[str, object]) -> str:
    """Validate and return a persisted local queue state."""

    status = _required_text(value, "status").casefold()
    if status not in QUEUE_STATUSES:
        raise BrowserDetailContractError(
            "Browser queue status must be pending, in_progress, complete, imported, blocked, or unavailable"
        )
    if status in TERMINAL_STATUSES:
        _required_text(value, "error")
    if status == "in_progress":
        _parse_datetime(_required_text(value, "lease_started_at"))
    return status


def task_from_mapping(value: Mapping[str, object]) -> BrowserDetailTask:
    schema_version = value.get("schema_version")
    if schema_version not in {SCHEMA_VERSION, 2}:
        raise BrowserDetailContractError(
            f"Unsupported browser detail schema_version: {schema_version!r}"
        )
    if schema_version == 2:
        return _browser_search_detail_task_from_mapping(value)
    url = _required_text(value, "url")
    candidate = EmailJobCandidate(
        url=url,
        title=_required_text(value, "title"),
        company_name=_required_text(value, "company_name"),
        location_raw=_required_text(value, "location_raw"),
        context=_required_text(value, "context"),
        message_id=_required_text(value, "message_id"),
        email_subject=_required_text(value, "email_subject"),
        email_from=_required_text(value, "email_from"),
        email_date=_parse_datetime(_required_text(value, "email_date")),
        anchor_text=_required_text(value, "anchor_text"),
    )
    task = BrowserDetailTask.from_candidate(candidate)
    task_id = _required_text(value, "task_id")
    if task_id != task.task_id:
        raise BrowserDetailContractError(
            "Browser result task_id does not match its canonical Indeed URL"
        )
    return task


def _browser_search_detail_task_from_mapping(value: Mapping[str, object]) -> BrowserDetailTask:
    if _required_text(value, "origin") != "browser_search":
        raise BrowserDetailContractError(
            "Browser search detail tasks require browser_search origin"
        )
    url = _required_text(value, "url")
    task = BrowserDetailTask(
        task_id=_required_text(value, "task_id"),
        url=indeed_detail_url(url),
        title=_required_text(value, "title"),
        company_name=_required_text(value, "company_name"),
        location_raw=_required_text(value, "location_raw"),
        context=_required_text(value, "context"),
        origin="browser_search",
        observed_at=_parse_datetime(_required_text(value, "observed_at")),
        search_task_id=_required_text(value, "search_task_id"),
        search_query=_required_text(value, "search_query"),
        search_location=_required_text(value, "search_location"),
    )
    expected_task_id = sha256(canonical_link_key(task.url).encode("utf-8")).hexdigest()[:24]
    if task.task_id != expected_task_id:
        raise BrowserDetailContractError(
            "Browser result task_id does not match its canonical Indeed URL"
        )
    return task


@dataclass(frozen=True, slots=True)
class BrowserSearchTask:
    task_id: str
    url: str
    query: str
    location: str
    created_at: datetime

    @classmethod
    def create(
        cls,
        *,
        domain: str,
        query: str,
        location: str,
        created_at: datetime,
    ) -> BrowserSearchTask:
        normalized_domain = domain.strip().casefold()
        if normalized_domain != "indeed.com" and not normalized_domain.endswith(".indeed.com"):
            raise BrowserDetailContractError("Browser search tasks require an Indeed domain")
        normalized_query = query.strip()
        normalized_location = location.strip()
        if not normalized_query or not normalized_location:
            raise BrowserDetailContractError("Browser search tasks require query and location")
        url = f"https://{normalized_domain}/jobs?{urlencode({'q': normalized_query, 'l': normalized_location})}"
        occurrence = created_at.astimezone(UTC).date().isoformat()
        task_id = sha256(f"{url}\n{occurrence}".encode()).hexdigest()[:24]
        return cls(task_id, url, normalized_query, normalized_location, created_at)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "task_id": self.task_id,
            "status": "pending",
            "url": self.url,
            "query": self.query,
            "location": self.location,
            "created_at": self.created_at.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class BrowserSearchCard:
    task: BrowserSearchTask
    url: str
    title: str
    company_name: str
    location_raw: str
    context: str


@dataclass(frozen=True, slots=True)
class BrowserSearchResult:
    task: BrowserSearchTask
    status: str
    cards: tuple[BrowserSearchCard, ...]
    error: str

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> BrowserSearchResult:
        task = search_task_from_mapping(value)
        status = search_queue_status(value)
        if status in TERMINAL_STATUSES:
            return cls(task, status, (), _required_text(value, "error"))
        if status != "complete":
            raise BrowserDetailContractError(
                "Browser search result status must be complete, blocked, or unavailable"
            )
        raw_cards = value.get("cards", [])
        if not isinstance(raw_cards, list):
            raise BrowserDetailContractError("Browser search result cards must be a list")
        cards: list[BrowserSearchCard] = []
        seen_urls: set[str] = set()
        for raw_card in raw_cards:
            if not isinstance(raw_card, Mapping):
                raise BrowserDetailContractError("Browser search result card must be an object")
            url = indeed_detail_url(_required_text(raw_card, "url"))
            if not url or not _is_indeed_url(url):
                raise BrowserDetailContractError(
                    "Browser search result card requires an Indeed viewjob URL"
                )
            if url in seen_urls:
                continue
            seen_urls.add(url)
            cards.append(
                BrowserSearchCard(
                    task=task,
                    url=url,
                    title=_required_text(raw_card, "title"),
                    company_name=_required_text(raw_card, "company_name"),
                    location_raw=_required_text(raw_card, "location_raw"),
                    context=_required_text(raw_card, "context"),
                )
            )
        return cls(task, status, tuple(cards), "")


def search_task_from_mapping(value: Mapping[str, object]) -> BrowserSearchTask:
    if value.get("schema_version") != 1:
        raise BrowserDetailContractError(
            f"Unsupported browser search schema_version: {value.get('schema_version')!r}"
        )
    url = _required_text(value, "url")
    split = urlsplit(url)
    domain = (split.hostname or "").casefold()
    task = BrowserSearchTask.create(
        domain=domain,
        query=_required_text(value, "query"),
        location=_required_text(value, "location"),
        created_at=_parse_datetime(_required_text(value, "created_at")),
    )
    if task.url != url or task.task_id != _required_text(value, "task_id"):
        raise BrowserDetailContractError(
            "Browser search task does not match its canonical query URL"
        )
    return task


def search_queue_status(value: Mapping[str, object]) -> str:
    status = _required_text(value, "status").casefold()
    if status not in SEARCH_QUEUE_STATUSES:
        raise BrowserDetailContractError(
            "Browser search status must be pending, in_progress, complete, expanded, blocked, or unavailable"
        )
    if status in TERMINAL_STATUSES:
        _required_text(value, "error")
    if status == "in_progress":
        _parse_datetime(_required_text(value, "lease_started_at"))
    return status


def _is_indeed_url(url: str) -> bool:
    hostname = (urlsplit(url).hostname or "").casefold()
    return hostname == "indeed.com" or hostname.endswith(".indeed.com")


def _required_text(value: Mapping[str, object], key: str) -> str:
    text = str(value.get(key) or "").strip()
    if not text:
        raise BrowserDetailContractError(f"Browser detail record is missing {key}")
    return text


def _parse_datetime(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise BrowserDetailContractError("Browser task email_date must be ISO-8601") from exc
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
