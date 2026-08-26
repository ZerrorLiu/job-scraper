from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from job_scraper.domain.context import EvaluationContext
from job_scraper.domain.decisions import RejectionReason
from job_scraper.domain.models import JobRecord, RawJobRecord
from job_scraper.domain.policies import FilterPolicy
from job_scraper.integrations.email_recommendations import (
    EmailJobCandidate,
    JobDetail,
    MailMessage,
    efinancial_url_metadata,
    enrich_email_candidate_to_raw_job,
    extract_job_candidates,
    fetch_job_detail,
    infer_linkedin_card_metadata,
    parse_job_detail_html,
)
from job_scraper.jobs.ingest_email_recommendations import (
    email_policy_for_raw,
    stable_email_dedupe_key,
)
from job_scraper.pipeline.normalize import normalize_candidate
from job_scraper.pipeline.steps import CountryStep
from job_scraper.storage.db import Database

EFINANCIAL_JOB_URL = (
    "https://www.efinancialcareers.test/jobs-Netherlands-Amsterdam-"
    "AI_Software_Engineer_-_Amsterdam-_Leading_High_Frequency_Trading_Firm.id123"
)


def test_linkedin_email_card_extracts_company_and_location_from_selected_title() -> None:
    company, location = infer_linkedin_card_metadata(
        "Embedded Systems Engineer",
        "Embedded Systems Engineer Northstar Robotics · Berlin (Hybrid) "
        "Platform Developer Blue Harbor Systems · Munich",
        "Embedded Systems Engineer",
    )

    assert company == "Northstar Robotics"
    assert location == "Berlin"


def test_linkedin_email_card_extracts_explicit_at_company() -> None:
    company, location = infer_linkedin_card_metadata(
        "Platform Developer at Blue Harbor Systems",
        "Platform Developer at Blue Harbor Systems",
        "Platform Developer at Blue Harbor Systems",
    )

    assert company == "Blue Harbor Systems"
    assert location == ""


def test_linkedin_email_card_does_not_borrow_adjacent_recommendation() -> None:
    company, location = infer_linkedin_card_metadata(
        "Embedded Systems Engineer",
        "Embedded Systems Engineer Platform Developer Blue Harbor Systems · Munich",
        "Embedded Systems Engineer",
    )

    assert (company, location) == ("", "")


def test_linkedin_email_card_stops_location_before_next_recommendation() -> None:
    company, location = infer_linkedin_card_metadata(
        "Embedded Systems Engineer",
        "Embedded Systems Engineer Northstar Robotics · Berlin "
        "Platform Developer Blue Harbor Systems · Munich",
        "Embedded Systems Engineer",
    )

    assert (company, location) == ("Northstar Robotics", "Berlin")


def test_efinancial_email_card_uses_job_url_metadata() -> None:
    message = MailMessage(
        uid="fictional-1",
        message_id="fictional-efinancial@example.test",
        subject="Recommended engineering jobs",
        sender="alerts@efinancialcareers.test",
        received_at=datetime(2026, 8, 5, tzinfo=UTC),
        text="",
        html=(
            "<html><body><div>Competitive</div>"
            f'<a href="{EFINANCIAL_JOB_URL}">Apply now</a>'
            "</body></html>"
        ),
    )

    candidates = extract_job_candidates(message)

    assert len(candidates) == 1
    assert candidates[0].title == "AI Software Engineer"
    assert candidates[0].company_name == "Leading High Frequency Trading Firm"
    assert candidates[0].location_raw == "Amsterdam"


def test_efinancial_email_card_splits_company_from_url_anchored_location() -> None:
    url = "https://www.efinancialcareers.test/jobs-USA-MO-St_Louis-C_Software_Engineer.id23882561"
    message = MailMessage(
        uid="fictional-efinancial-card",
        message_id="fictional-efinancial-card@example.test",
        subject="Recommended engineering jobs",
        sender="alerts@efinancialcareers.test",
        received_at=datetime(2026, 8, 5, tzinfo=UTC),
        text="",
        html=(
            "<div>C++ Software Engineer</div>"
            "<div>London Stock Exchange Group</div>"
            "<div>St Louis, United States</div>"
            "<div>Competitive</div>"
            f'<a href="{url}">Jetzt bewerben:</a>'
        ),
    )

    candidates = extract_job_candidates(message)

    assert len(candidates) == 1
    assert candidates[0].company_name == "London Stock Exchange Group"
    assert candidates[0].location_raw == "St Louis, United States"


def test_efinancial_does_not_borrow_location_from_adjacent_recommendation() -> None:
    message = MailMessage(
        uid="fictional-2",
        message_id="fictional-efinancial-adjacent@example.test",
        subject="Recommended engineering jobs",
        sender="alerts@efinancialcareers.test",
        received_at=datetime(2026, 8, 5, tzinfo=UTC),
        text="",
        html=(
            "<html><body><div>Singapore, Singapore</div>"
            '<a href="https://www.efinancialcareers.test/jobs-fictional-ai-engineer.id124">'
            "Apply now</a></body></html>"
        ),
    )

    candidates = extract_job_candidates(message)

    assert len(candidates) == 1
    assert candidates[0].location_raw == ""


@pytest.mark.parametrize(
    ("city_slug", "expected_city"),
    [("Paris", "Paris"), ("Zurich", "Zurich"), ("Prague", "Prague"), ("Warsaw", "Warsaw")],
)
def test_efinancial_url_metadata_recognizes_neighboring_cities(
    city_slug: str, expected_city: str
) -> None:
    title, company, location = efinancial_url_metadata(
        f"https://www.efinancialcareers.test/jobs-France-{city_slug}-AI_Engineer.id125"
    )

    assert title == "AI Engineer"
    assert company == ""
    assert location == expected_city


def test_efinancial_html_header_extracts_company_and_location() -> None:
    detail = parse_job_detail_html(
        """
        <html><body>
          <h1>C++ Software Engineer</h1>
          <div class="job-header-meta">
            <span>London Stock Exchange Group</span>
            <span aria-hidden="true">•</span>
            <span>St. Louis, USA</span>
          </div>
          <div>Festanstellung • Competitive</div>
          <p>A fictional detail page description with enough content to pass
          the detail threshold and represent the visible page body.</p>
        </body></html>
        """
    )

    assert detail.company_name == "London Stock Exchange Group"
    assert detail.location_raw == "St. Louis, USA"


def test_reprocess_updates_existing_job_metadata_by_source_url(tmp_path) -> None:
    database = Database(tmp_path / "jobs.db")
    database.initialize()
    observed_at = datetime(2026, 8, 6, tzinfo=UTC)
    old_job = JobRecord(
        source="email",
        source_job_id="old-source-id",
        source_url=EFINANCIAL_JOB_URL,
        canonical_url=EFINANCIAL_JOB_URL,
        title="AI Software Engineer",
        company_name="Unknown",
        location_raw="Amsterdam",
        country="",
        city="Amsterdam",
        region="",
        remote_type="unknown",
        employment_type="unknown",
        seniority="mid",
        posted_at=None,
        first_seen_at=observed_at,
        scraped_at=observed_at,
        job_description="A fictional job description.",
        description_language="English",
        english_ratio=1.0,
        keyword_hits=[],
        tech_stack=[],
        salary_text="",
        salary_min=None,
        salary_max=None,
        salary_currency=None,
        dedupe_key="old-dedupe-key",
    )
    run = database.create_run("email", observed_at)
    old_id, is_new = database.upsert_job(old_job, run.run_id)
    assert is_new is True

    corrected = replace(
        old_job,
        source_job_id="corrected-source-id",
        company_name="Leading High Frequency Trading Firm",
        country="NL",
        dedupe_key="corrected-dedupe-key",
    )
    corrected_id, corrected_is_new = database.upsert_job(corrected, run.run_id)

    assert corrected_is_new is False
    assert corrected_id == old_id
    with database.connect() as connection:
        row = connection.execute(
            "SELECT company_name, country_code FROM jobs WHERE id = ?", (old_id,)
        ).fetchone()
    assert row["company_name"] == "Leading High Frequency Trading Firm"
    assert row["country_code"] == "NL"


def test_efinancial_detail_uses_final_redirect_url(monkeypatch) -> None:
    tracking_url = "https://click.efinancialcareers.test/f/a/fictional-token"
    detail_html = """
    <html><head>
      <meta property="og:title" content="AI Software Engineer - Amsterdam- Leading High Frequency Trading Firm">
      <meta name="description" content="Build reliable software systems for a fictional engineering team. This description is intentionally long enough to represent a detail page response from a job board.">
    </head><body></body></html>
    """

    monkeypatch.setattr(
        "job_scraper.integrations.email_recommendations.fetch_text",
        lambda url, http_config: (detail_html, EFINANCIAL_JOB_URL),
    )

    detail = fetch_job_detail(tracking_url, object())

    assert detail.title == "AI Software Engineer"
    assert detail.company_name == "Leading High Frequency Trading Firm"
    assert detail.location_raw == "Amsterdam"
    assert detail.raw_payload["final_url"] == EFINANCIAL_JOB_URL


def test_efinancial_url_fills_missing_json_ld_metadata() -> None:
    detail = parse_job_detail_html(
        """
        <script type="application/ld+json">
        {
          "@type": "JobPosting",
          "title": "AI Software Engineer",
          "description": "A fictional detail page description."
        }
        </script>
        """,
        EFINANCIAL_JOB_URL,
    )

    assert detail.title == "AI Software Engineer"
    assert detail.company_name == "Leading High Frequency Trading Firm"
    assert detail.location_raw == "Amsterdam"


def test_incomplete_efinancial_detail_is_not_marked_complete(monkeypatch) -> None:
    candidate = EmailJobCandidate(
        url=EFINANCIAL_JOB_URL,
        title="AI Software Engineer",
        company_name="",
        location_raw="Amsterdam",
        context="Competitive Apply now",
        message_id="fictional-efinancial-fallback@example.test",
        email_subject="Recommended engineering jobs",
        email_from="alerts@efinancialcareers.test",
        email_date=datetime(2026, 8, 5, tzinfo=UTC),
        anchor_text="Apply now",
    )

    monkeypatch.setattr(
        "job_scraper.integrations.email_recommendations.fetch_job_detail",
        lambda url, http_config: JobDetail(
            title="AI Software Engineer",
            description="A fictional detail page description that is long enough to pass the existing detail text threshold while omitting company metadata.",
        ),
    )

    raw = enrich_email_candidate_to_raw_job(
        candidate,
        object(),
        scraped_at=datetime(2026, 8, 5, tzinfo=UTC),
    )

    assert raw.company_name == "Unknown"
    assert raw.raw_payload["detail_status"] == "email_fallback"


def test_tracking_and_direct_urls_share_detail_identity(monkeypatch) -> None:
    tracking_url = "https://click.efinancialcareers.test/f/a/fictional-token"
    candidate_values = {
        "title": "AI Software Engineer",
        "company_name": "",
        "location_raw": "",
        "context": "Competitive Apply now",
        "message_id": "fictional-identity@example.test",
        "email_subject": "Recommended engineering jobs",
        "email_from": "alerts@efinancialcareers.test",
        "email_date": datetime(2026, 8, 5, tzinfo=UTC),
        "anchor_text": "Apply now",
    }

    monkeypatch.setattr(
        "job_scraper.integrations.email_recommendations.fetch_job_detail",
        lambda url, http_config: JobDetail(
            title="AI Software Engineer",
            company_name="Leading High Frequency Trading Firm",
            location_raw="Amsterdam",
            description="A fictional detail page description with enough content to represent a successful response.",
            raw_payload={"final_url": EFINANCIAL_JOB_URL},
        ),
    )

    direct = enrich_email_candidate_to_raw_job(
        EmailJobCandidate(url=EFINANCIAL_JOB_URL, **candidate_values),
        object(),
        scraped_at=datetime(2026, 8, 5, tzinfo=UTC),
    )
    tracking = enrich_email_candidate_to_raw_job(
        EmailJobCandidate(url=tracking_url, **candidate_values),
        object(),
        scraped_at=datetime(2026, 8, 5, tzinfo=UTC),
    )

    assert tracking.canonical_url == direct.canonical_url == EFINANCIAL_JOB_URL
    assert tracking.source_job_id == direct.source_job_id


def test_email_dedupe_uses_corrected_metadata_not_card_fallbacks() -> None:
    first = SimpleNamespace(
        title="AI Software Engineer",
        company_name="Leading High Frequency Trading Firm",
        location_raw="Amsterdam",
        source_job_id="same-final-job",
        source="email",
        raw_payload={
            "email_candidate_title": "Amsterdam AI Software Engineer",
            "email_candidate_company": "Emails",
            "email_candidate_location": "Frankfurt",
        },
    )
    second = SimpleNamespace(
        title=first.title,
        company_name=first.company_name,
        location_raw=first.location_raw,
        source_job_id=first.source_job_id,
        source=first.source,
        raw_payload={
            "email_candidate_title": "AI Software Engineer",
            "email_candidate_company": "Efinancialcareers",
            "email_candidate_location": "Amsterdam",
        },
    )

    assert stable_email_dedupe_key(first) == stable_email_dedupe_key(second)


# A profile may widen the country scope for one platform. That mapping is
# configuration (the mailbox's `[platform_country_scope]` table), so tests
# supply it explicitly instead of relying on a country list baked into the
# library.
NEIGHBOURING_SCOPE = {
    "efinancialcareers": ("DE", "NL", "BE", "FR", "LU", "AT", "CH", "CZ", "PL", "DK")
}


def _location_raw(location: str, *, url: str = EFINANCIAL_JOB_URL) -> RawJobRecord:
    return RawJobRecord(
        source="email",
        source_job_id="fictional-location-job",
        source_url=url,
        canonical_url=url,
        title="AI Software Engineer",
        company_name="Fictional Company",
        location_raw=location,
        posted_at_text="",
        scraped_at=datetime(2026, 8, 5, tzinfo=UTC),
        job_description="Build reliable software for a fictional engineering team.",
    )


def test_efinancial_allows_neighboring_country_location() -> None:
    raw = _location_raw("Amsterdam, Netherlands")
    policy = email_policy_for_raw(raw, FilterPolicy(countries=("DE",)), NEIGHBOURING_SCOPE)
    job = normalize_candidate(raw, policy)

    decision = CountryStep().evaluate(
        job,
        EvaluationContext(
            profile_id="fictional-profile",
            started_at=datetime(2026, 8, 5, tzinfo=UTC),
            policy=policy,
        ),
    )

    assert job.country == "NL"
    assert decision.reason is None


def test_efinancial_rejects_explicit_singapore_location() -> None:
    raw = _location_raw("Singapore, Singapore")
    policy = email_policy_for_raw(raw, FilterPolicy(countries=("DE",)), NEIGHBOURING_SCOPE)
    job = normalize_candidate(raw, policy)

    decision = CountryStep().evaluate(
        job,
        EvaluationContext(
            profile_id="fictional-profile",
            started_at=datetime(2026, 8, 5, tzinfo=UTC),
            policy=policy,
        ),
    )

    assert job.country == "SG"
    assert decision.reason == RejectionReason.NOT_TARGET_COUNTRY


def test_location_overrides_stale_default_country_code() -> None:
    raw = _location_raw("Singapore, Singapore")
    raw.raw_payload["location_country"] = "DE"
    policy = email_policy_for_raw(raw, FilterPolicy(countries=("DE",)), NEIGHBOURING_SCOPE)
    job = normalize_candidate(raw, policy)

    decision = CountryStep().evaluate(
        job,
        EvaluationContext(
            profile_id="fictional-profile",
            started_at=datetime(2026, 8, 5, tzinfo=UTC),
            policy=policy,
        ),
    )

    assert job.country == "DE"
    assert decision.reason == RejectionReason.NOT_TARGET_COUNTRY


def test_non_efinancial_source_keeps_germany_only_scope() -> None:
    raw = _location_raw(
        "Amsterdam, Netherlands",
        url="https://www.linkedin.test/jobs/view/fictional-job",
    )
    policy = email_policy_for_raw(raw, FilterPolicy(countries=("DE",)), NEIGHBOURING_SCOPE)
    job = normalize_candidate(raw, policy)

    assert policy.countries == ("DE",)
    assert job.country == "NL"
