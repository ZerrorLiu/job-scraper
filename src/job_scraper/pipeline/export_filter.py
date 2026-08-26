"""Re-evaluate a stored row against a profile policy for export.

Exports must answer the same question the acquisition pipeline answered --
"does this job belong to this profile?" -- so this module reconstructs a
`JobRecord` from the stored row and runs the *same* pipeline steps rather than
re-implementing them. The previous parallel implementation had already begun to
drift from `pipeline/steps.py`, which meant a job could pass one and fail the
other.

Freshness and history are deliberately not applied: an export is cumulative, so
a row that was fresh when it was acquired stays in the file.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Protocol

from job_scraper.domain.context import EvaluationContext
from job_scraper.domain.models import JobRecord
from job_scraper.domain.policies import FilterPolicy, title_scoped
from job_scraper.pipeline.engine import CandidatePipeline
from job_scraper.pipeline.normalize import looks_like_germany
from job_scraper.pipeline.steps import (
    CompanyStep,
    CountryStep,
    EmploymentScopeStep,
    ExcludedTermsStep,
    LanguageStep,
    RequirementExclusionStep,
    RoleStep,
)

EXPORT_PIPELINE = CandidatePipeline(
    (
        CountryStep(),
        CompanyStep(),
        EmploymentScopeStep(),
        ExcludedTermsStep(),
        RoleStep(),
        RequirementExclusionStep(),
        LanguageStep(),
    )
)


class ExportRow(Protocol):
    def __getitem__(self, key: str) -> object: ...

    def keys(self) -> list[str]: ...


def export_row_is_germany(row: ExportRow) -> bool:
    location_text = str(row["location_text"] or "")
    payload = _payload(row)
    raw_country = str(payload.get("location_country") or "").strip()
    country = raw_country or str(row["country_code"] or "")
    return looks_like_germany(location_text, country)


def export_row_matches_policy(row: ExportRow, policy: FilterPolicy) -> bool:
    job = export_row_to_job(row)
    # A row whose detail page never loaded only has its email card text, so the
    # role must be evident from the title rather than from surrounding copy.
    if str(job.raw_payload.get("detail_status") or "") == "email_fallback":
        policy = title_scoped(policy)
    decision = EXPORT_PIPELINE.evaluate(
        job,
        EvaluationContext(profile_id="export", started_at=_EPOCH, policy=policy),
    )
    return decision.accepted


def export_row_to_job(row: ExportRow) -> JobRecord:
    """Rebuild the subset of a JobRecord the policy steps actually read."""
    try:
        english_ratio = float(str(row["english_ratio"] or 0.0))
    except (TypeError, ValueError):
        english_ratio = 0.0
    return JobRecord(
        source="",
        source_job_id="",
        source_url="",
        canonical_url="",
        title=str(row["normalized_title"] or ""),
        company_name=str(row["company_name"] or ""),
        location_raw=str(row["location_text"] or ""),
        country=str(row["country_code"] or ""),
        city="",
        region="",
        remote_type="",
        employment_type=str(row["employment_type"] or ""),
        seniority="",
        posted_at=None,
        first_seen_at=_EPOCH,
        scraped_at=_EPOCH,
        job_description=str(row["description_full"] or ""),
        description_language=str(row["description_language"] or ""),
        english_ratio=english_ratio,
        keyword_hits=[],
        tech_stack=[],
        salary_text="",
        salary_min=None,
        salary_max=None,
        salary_currency=None,
        dedupe_key="",
        raw_payload=_payload(row),
    )


_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def _payload(row: ExportRow) -> dict[str, object]:
    raw_payload_text = str(row["raw_payload_json"] or "")
    if not raw_payload_text:
        return {}
    try:
        payload = json.loads(raw_payload_text)
    except json.JSONDecodeError:
        return {}
    return dict(payload) if isinstance(payload, dict) else {}
