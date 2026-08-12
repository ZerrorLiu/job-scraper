from __future__ import annotations

from datetime import UTC, datetime

from job_scraper.domain.identity import canonical_identity
from job_scraper.domain.locations import merge_locations
from job_scraper.domain.models import JobRecord
from job_scraper.pipeline.language_filter import english_ratio, matches_requirement_patterns
from job_scraper.pipeline.normalize import known_location_country


def _job(**overrides: object) -> JobRecord:
    observed_at = datetime(2026, 1, 1, tzinfo=UTC)
    defaults: dict[str, object] = {
        "source": "indeed",
        "source_job_id": "fictional-1",
        "source_url": "https://de.indeed.com/viewjob?jk=fictional-1",
        "canonical_url": "https://de.indeed.com/viewjob?jk=fictional-1",
        "title": "Fictional Engineer",
        "company_name": "Example GmbH",
        "location_raw": "Berlin",
        "country": "DE",
        "city": "Berlin",
        "region": "",
        "remote_type": "onsite",
        "employment_type": "full-time",
        "seniority": "unknown",
        "posted_at": observed_at,
        "first_seen_at": observed_at,
        "scraped_at": observed_at,
        "job_description": "A complete fictional job description.",
        "description_language": "English",
        "english_ratio": 1.0,
        "keyword_hits": [],
        "tech_stack": [],
        "salary_text": "",
        "salary_min": None,
        "salary_max": None,
        "salary_currency": None,
        "dedupe_key": "fictional-1",
    }
    defaults.update(overrides)
    return JobRecord(**defaults)  # type: ignore[arg-type]


def test_merge_locations_is_order_independent_for_multi_city_postings() -> None:
    forward = merge_locations("Berlin", "Munich")
    backward = merge_locations("Munich", "Berlin")

    assert forward[0] == backward[0]
    assert forward[1] == "Multiple locations"
    assert backward[1] == "Multiple locations"


def test_distinct_multi_city_postings_do_not_share_a_canonical_identity() -> None:
    posting_a = _job(
        title="Fictional Engineer",
        location_raw="Berlin | Munich",
        city="Multiple locations",
    )
    posting_b = _job(
        title="Fictional Engineer",
        location_raw="Hamburg | Cologne",
        city="Multiple locations",
    )

    assert canonical_identity(posting_a) != canonical_identity(posting_b)


def test_same_multi_city_posting_has_stable_identity_regardless_of_scrape_order() -> None:
    # Two sources describe the same posting but list its cities in a
    # different order. merge_locations is what production code (normalize /
    # aggregation) uses to build location_raw from raw inputs, so route the
    # test through it rather than hand-constructing pre-ordered strings.
    location_raw_first, city_first, _ = merge_locations("Berlin", "Munich")
    location_raw_second, city_second, _ = merge_locations("Munich", "Berlin")
    scraped_first = _job(location_raw=location_raw_first, city=city_first)
    scraped_second = _job(location_raw=location_raw_second, city=city_second)

    assert canonical_identity(scraped_first) == canonical_identity(scraped_second)


def test_known_location_country_prefers_trailing_country_segment() -> None:
    assert known_location_country("Vienna, VA, USA") == "US"
    assert known_location_country("Berlin, Germany") == "DE"


def test_known_location_country_still_resolves_plain_city_names() -> None:
    assert known_location_country("Munich") == "DE"
    assert known_location_country("Dublin, Ireland") == "IE"


def test_english_ratio_requires_minimum_evidence() -> None:
    short_german_title = "Werkstudent Data Engineer (m/w/d)"

    assert english_ratio(short_german_title) == 0.0


def test_english_ratio_still_scores_confident_english_text() -> None:
    english_text = (
        "We are looking for a software engineer to join our team and build "
        "products for customers. Requirements: experience with systems and "
        "solutions design."
    )

    assert english_ratio(english_text) >= 0.75


def test_matches_requirement_patterns_uses_word_boundaries() -> None:
    assert matches_requirement_patterns("Germane experience is a plus.", ("german",)) is False
    assert matches_requirement_patterns("German language required.", ("german",)) is True
