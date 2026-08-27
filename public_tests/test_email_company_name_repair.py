"""The email channel no longer emits a leaked, malformed company_name.

Coverage for docs/public/specs/2026-08-27-employer-direct-source-coverage.md:
`is_publisher_company` (a private three-value hardcoded set, checked at two
call sites) is gone entirely -- the email channel now relies on the shared
`excluded_company_names` denylist in CompanyStep for publisher rejection,
like every other source. Separately, `infer_company`'s generic fallback no
longer returns role text, a salary figure, an (m/w/d)-style marker, or a
call-to-action phrase as a company name.
"""

from __future__ import annotations

import job_scraper.integrations.email_recommendations as email_recommendations
from job_scraper.integrations.email_recommendations import (
    infer_company,
    infer_linkedin_card_metadata,
    looks_like_malformed_company,
)


def test_is_publisher_company_no_longer_exists() -> None:
    assert not hasattr(email_recommendations, "is_publisher_company")


def test_role_text_is_recognized_as_a_malformed_company() -> None:
    assert looks_like_malformed_company("Senior Backend Engineer")


def test_a_salary_figure_is_recognized_as_a_malformed_company() -> None:
    assert looks_like_malformed_company("55.000 - 70.000 EUR")
    assert looks_like_malformed_company("up to €65k")


def test_a_gender_neutral_marker_is_recognized_as_a_malformed_company() -> None:
    assert looks_like_malformed_company("(m/w/d)")


def test_a_call_to_action_phrase_is_recognized_as_a_malformed_company() -> None:
    assert looks_like_malformed_company("Jetzt bewerben")
    assert looks_like_malformed_company("Apply now")


def test_a_real_company_name_is_not_flagged_as_malformed() -> None:
    assert not looks_like_malformed_company("Northstar Robotics GmbH")
    assert not looks_like_malformed_company("3M")
    assert not looks_like_malformed_company("K+S")


def test_infer_company_skips_a_role_text_capture_and_falls_back_to_sender_domain() -> None:
    company = infer_company(
        context="Backend Engineer at Senior Software Engineer",
        title="Backend Engineer",
        sender="jobs@northstarrobotics.example",
    )

    assert company == "Northstarrobotics"


def test_infer_company_skips_a_salary_figure_capture() -> None:
    company = infer_company(
        context="Backend Engineer at 55.000 - 70.000 EUR",
        title="Backend Engineer",
        sender="",
    )

    assert company == "Unknown"


def test_linkedin_card_with_a_gender_marker_where_the_company_belongs_yields_no_company() -> None:
    company, _location = infer_linkedin_card_metadata(
        "Backend Engineer",
        "Backend Engineer (m/w/d) · Berlin",
    )

    assert company == ""
