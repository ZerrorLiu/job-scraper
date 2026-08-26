"""Export filtering and acquisition filtering must not be able to disagree."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from job_scraper.domain.context import EvaluationContext
from job_scraper.domain.models import JobRecord
from job_scraper.domain.policies import FilterPolicy, TargetRule
from job_scraper.pipeline.engine import CandidatePipeline
from job_scraper.pipeline.export_filter import export_row_matches_policy, export_row_to_job
from job_scraper.pipeline.steps import (
    CompanyStep,
    CountryStep,
    EmploymentScopeStep,
    ExcludedTermsStep,
    LanguageStep,
    RequirementExclusionStep,
    RoleStep,
)

ACQUISITION_STEPS = CandidatePipeline(
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

POLICY = FilterPolicy(
    countries=("DE",),
    excluded_terms=("recruiter",),
    acceptance_terms=("engineer",),
    acceptance_scope="title",
    acceptance_rules=(TargetRule(name="role", keywords=("engineer",), match_scope="title"),),
    excluded_requirement_patterns=("fluent in german",),
    require_english=True,
    minimum_english_ratio=0.5,
)


def _row(**overrides: object) -> dict[str, object]:
    row = {
        "normalized_title": "Software Engineer",
        "company_name": "Fictional GmbH",
        "location_text": "Berlin, Germany",
        "country_code": "DE",
        "description_full": "We build systems with the team. You will design and deliver.",
        "description_language": "English",
        "english_ratio": 1.0,
        "employment_type": "full-time",
        "raw_payload_json": "{}",
    }
    row.update(overrides)
    return row


CASES = [
    ("accepted", {}),
    ("wrong country", {"location_text": "Paris, France", "country_code": "FR"}),
    ("excluded title term", {"normalized_title": "Technical Recruiter"}),
    ("missing role keyword", {"normalized_title": "Office Manager"}),
    ("part time", {"employment_type": "part-time"}),
    ("internship", {"normalized_title": "Engineer Internship"}),
    ("non-english", {"description_language": "German"}),
    (
        "excluded requirement",
        {"description_full": "You will design and deliver. Fluent in German is required."},
    ),
]


@pytest.mark.parametrize("label,overrides", CASES, ids=[case[0] for case in CASES])
def test_export_agrees_with_the_acquisition_pipeline(label: str, overrides: dict) -> None:
    """Same job, same policy: both paths must reach the same verdict."""
    del label
    row = _row(**overrides)
    job = export_row_to_job(row)

    export_verdict = export_row_matches_policy(row, POLICY)
    acquisition_verdict = ACQUISITION_STEPS.evaluate(
        job,
        EvaluationContext(
            profile_id="test",
            started_at=datetime(1970, 1, 1, tzinfo=UTC),
            policy=POLICY,
        ),
    ).accepted

    assert export_verdict is acquisition_verdict


def test_an_email_fallback_row_needs_the_role_in_its_title() -> None:
    """Without a real description, matching on body text would be a false positive."""
    payload = json.dumps({"detail_status": "email_fallback"})
    body_only = _row(
        normalized_title="Exciting opportunity",
        description_full="We are hiring an engineer for the team. You will design and deliver.",
        raw_payload_json=payload,
    )
    combined_policy = FilterPolicy(
        countries=("DE",),
        acceptance_terms=("engineer",),
        acceptance_scope="combined",
        require_english=False,
    )

    assert export_row_matches_policy(body_only, combined_policy) is False
    assert (
        export_row_matches_policy(
            _row(normalized_title="Engineer", raw_payload_json=payload), combined_policy
        )
        is True
    )


def test_a_row_keeps_its_search_location_country_evidence() -> None:
    row = _row(
        location_text="Remote",
        country_code="DE",
        raw_payload_json=json.dumps({"search_location": "Germany"}),
    )

    assert export_row_matches_policy(row, POLICY) is True


def test_a_malformed_payload_does_not_crash_the_export() -> None:
    assert isinstance(export_row_to_job(_row(raw_payload_json="{not json")), JobRecord)
