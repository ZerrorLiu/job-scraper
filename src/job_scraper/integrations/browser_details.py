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
    message_id: str
    email_subject: str
    email_from: str
    email_date: datetime
    anchor_text: str

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
            message_id=candidate.message_id,
            email_subject=candidate.email_subject,
            email_from=candidate.email_from,
            email_date=candidate.email_date,
            anchor_text=candidate.anchor_text,
        )

    def to_dict(self) -> dict[str, object]:
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

    def to_candidate(self) -> EmailJobCandidate:
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
    if schema_version != SCHEMA_VERSION:
        raise BrowserDetailContractError(
            f"Unsupported browser detail schema_version: {schema_version!r}"
        )
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
