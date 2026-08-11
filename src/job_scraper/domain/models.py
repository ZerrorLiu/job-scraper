from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True, slots=True)
class SearchWindow:
    started_at: datetime
    overlap_hours: int
    post_age_hours: int | None = None


@dataclass(slots=True)
class RawJobRecord:
    source: str
    source_job_id: str
    source_url: str
    canonical_url: str
    title: str
    company_name: str
    location_raw: str
    posted_at_text: str
    scraped_at: datetime
    job_description: str = ""
    application_url: str = ""
    company_url: str = ""
    salary_text: str = ""
    employment_type: str = "unknown"
    remote_type: str = "unknown"
    seniority: str = "unknown"
    raw_payload: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class JobRecord:
    source: str
    source_job_id: str
    source_url: str
    canonical_url: str
    title: str
    company_name: str
    location_raw: str
    country: str
    city: str
    region: str
    remote_type: str
    employment_type: str
    seniority: str
    posted_at: datetime | None
    first_seen_at: datetime
    scraped_at: datetime
    job_description: str
    description_language: str
    english_ratio: float
    keyword_hits: list[str]
    tech_stack: list[str]
    salary_text: str
    salary_min: float | None
    salary_max: float | None
    salary_currency: str | None
    application_url: str
    company_url: str
    dedupe_key: str
    raw_payload: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class RunStats:
    run_id: str
    source: str
    started_at: datetime
    finished_at: datetime | None = None
    jobs_seen: int = 0
    jobs_new: int = 0
    jobs_updated: int = 0
    jobs_filtered: int = 0
    jobs_failed: int = 0
    errors: list[str] = field(default_factory=list)


@dataclass(slots=True)
class JobHistorySnapshot:
    status_label: str
    exact_seen_before: bool = False
    company_seen_before: bool = False
    previous_notion_page_id: str = ""
    previous_title: str = ""
    previous_company_name: str = ""
    previous_seen_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class CanonicalJob:
    """A platform-independent job identity."""

    job_id: str
    title: str
    company_name: str
    country: str
    city: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class SourcePosting:
    """One platform's representation of a canonical job."""

    posting_id: str
    canonical_job_id: str
    source_id: str
    source_job_id: str
    canonical_url: str
    first_seen_at: datetime
    last_seen_at: datetime


@dataclass(frozen=True, slots=True)
class SourceObservation:
    observation_id: str
    posting_id: str
    run_id: str
    observed_at: datetime
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ProfileMatch:
    match_id: str
    canonical_job_id: str
    profile_id: str
    accepted: bool
    reason: str
    evaluated_at: datetime


@dataclass(frozen=True, slots=True)
class ApplicationRecord:
    application_id: str
    canonical_job_id: str
    status: str
    updated_at: datetime
    external_reference: str = ""


@dataclass(frozen=True, slots=True)
class ApplicationJob:
    """An accepted job that may be inspected by the application runner."""

    canonical_job_id: str
    application_url: str
    title: str
    company_name: str
    location_text: str
    description: str
    status: str
