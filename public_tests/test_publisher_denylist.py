"""excluded_company_names rejects a publisher, not an employer.

Coverage for docs/public/specs/2026-08-27-employer-direct-source-coverage.md:
the denylist lives in CompanyStep so every source shares it, matching is
exact-after-normalization (not a substring hit inside a longer employer
name), and it produces a RejectionReason distinct from COMPANY_NOT_ALLOWED.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from job_scraper.config import load_config
from job_scraper.configuration.policy import policy_from_legacy
from job_scraper.domain.context import EvaluationContext
from job_scraper.domain.decisions import RejectionReason
from job_scraper.domain.models import JobRecord
from job_scraper.domain.policies import FilterPolicy
from job_scraper.pipeline.role_filter import company_matches_denylist
from job_scraper.pipeline.steps import CompanyStep

_MINIMAL_CONFIG = """
[project]
timezone = "UTC"
database_path = "jobs.db"
export_dir = "exports"
overlap_hours = 24

[filters]
country = "DE"
include_keywords = ["engineer"]
exclude_keywords = []
minimum_english_ratio = 0.5
excluded_company_names = ["eFinancialCareers", "Emails", "Unknown"]

[http]
user_agent = "fictional/1.0"
timeout_seconds = 10
base_delay_seconds = 1.0
jitter_seconds = 0.5
max_retries = 2

[sources.linkedin_direct]
enabled = false
"""


def _job(company_name: str) -> JobRecord:
    observed_at = datetime(2026, 1, 1, tzinfo=UTC)
    return JobRecord(
        source="ats_direct",
        source_job_id="fictional-1",
        source_url="https://example.test/fictional-1",
        canonical_url="https://example.test/fictional-1",
        title="Fictional Engineer",
        company_name=company_name,
        location_raw="Berlin",
        country="DE",
        city="Berlin",
        region="",
        remote_type="onsite",
        employment_type="full-time",
        seniority="unknown",
        posted_at=observed_at,
        first_seen_at=observed_at,
        scraped_at=observed_at,
        job_description="A complete fictional job description.",
        description_language="English",
        english_ratio=1.0,
        keyword_hits=[],
        tech_stack=[],
        salary_text="",
        salary_min=None,
        salary_max=None,
        salary_currency=None,
        dedupe_key="fictional-1",
    )


def test_an_exact_normalized_match_is_denied() -> None:
    assert company_matches_denylist("eFinancialCareers", ["efinancialcareers"])
    assert company_matches_denylist("  eFinancialCareers  ", ["efinancialcareers"])


def test_a_publisher_name_as_a_substring_of_a_longer_employer_name_is_not_denied() -> None:
    assert not company_matches_denylist(
        "Amazon Web Services GmbH",
        ["Amazon"],
    )


def test_an_empty_denylist_denies_nothing() -> None:
    assert not company_matches_denylist("Anything GmbH", [])


def test_company_step_rejects_a_denylisted_company_with_its_own_reason() -> None:
    step = CompanyStep()
    context = EvaluationContext(
        profile_id="fictional",
        started_at=datetime(2026, 1, 1, tzinfo=UTC),
        policy=FilterPolicy(
            countries=("DE",),
            excluded_company_names=("eFinancialCareers",),
        ),
    )

    decision = step.evaluate(_job("eFinancialCareers"), context)

    assert not decision.accepted
    assert decision.reason is RejectionReason.COMPANY_IS_PUBLISHER


def test_company_step_denylist_check_wins_over_a_matching_allowlist() -> None:
    """A publisher veto is a harder rule than being on the allowlist."""
    step = CompanyStep()
    context = EvaluationContext(
        profile_id="fictional",
        started_at=datetime(2026, 1, 1, tzinfo=UTC),
        policy=FilterPolicy(
            countries=("DE",),
            allowed_companies=("eFinancialCareers",),
            excluded_company_names=("eFinancialCareers",),
        ),
    )

    decision = step.evaluate(_job("eFinancialCareers"), context)

    assert not decision.accepted
    assert decision.reason is RejectionReason.COMPANY_IS_PUBLISHER


def test_company_step_still_uses_company_not_allowed_when_not_a_publisher() -> None:
    step = CompanyStep()
    context = EvaluationContext(
        profile_id="fictional",
        started_at=datetime(2026, 1, 1, tzinfo=UTC),
        policy=FilterPolicy(
            countries=("DE",),
            allowed_companies=("Example GmbH",),
            excluded_company_names=("eFinancialCareers",),
        ),
    )

    decision = step.evaluate(_job("Some Other Company"), context)

    assert not decision.accepted
    assert decision.reason is RejectionReason.COMPANY_NOT_ALLOWED


def test_excluded_company_names_loads_from_config_and_reaches_the_policy(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(_MINIMAL_CONFIG, encoding="utf-8")

    loaded = load_config(config_path)

    assert loaded.filters.excluded_company_names == ["eFinancialCareers", "Emails", "Unknown"]
    policy = policy_from_legacy(loaded.filters)
    assert policy.excluded_company_names == ("eFinancialCareers", "Emails", "Unknown")


def test_an_empty_default_denylist_changes_no_existing_behavior() -> None:
    step = CompanyStep()
    context = EvaluationContext(
        profile_id="fictional",
        started_at=datetime(2026, 1, 1, tzinfo=UTC),
        policy=FilterPolicy(countries=("DE",)),
    )

    decision = step.evaluate(_job("Any Company GmbH"), context)

    assert decision.accepted
