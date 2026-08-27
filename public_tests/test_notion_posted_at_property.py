"""build_daily_properties writes "Posted At" only when a table's schema has it.

Coverage: adding a Notion Date property to one track's table (a one-off,
per-table migration) must not send an unknown property name to every other
track's table, which Notion's API would reject outright.
"""

from __future__ import annotations

from datetime import UTC, datetime

from job_scraper.adapters.sinks.notion_payload import build_daily_properties
from job_scraper.domain.models import JobRecord


def _job_record(posted_at: datetime | None) -> JobRecord:
    observed_at = datetime(2026, 8, 27, tzinfo=UTC)
    return JobRecord(
        source="arbeitsagentur",
        source_job_id="1",
        source_url="https://example.test/1",
        canonical_url="https://example.test/1",
        title="Sachbearbeiter",
        company_name="Example GmbH",
        location_raw="Berlin",
        country="DE",
        city="Berlin",
        region="",
        remote_type="onsite",
        employment_type="full-time",
        seniority="unknown",
        posted_at=posted_at,
        first_seen_at=observed_at,
        scraped_at=observed_at,
        job_description="",
        description_language="German",
        english_ratio=0.0,
        keyword_hits=[],
        tech_stack=[],
        salary_text="",
        salary_min=None,
        salary_max=None,
        salary_currency=None,
        dedupe_key="1",
    )


def test_posted_at_is_written_when_the_schema_has_it() -> None:
    job = _job_record(datetime(2026, 8, 5, tzinfo=UTC))

    properties = build_daily_properties(job, {"Posted At": "date"})

    assert properties["Posted At"] == {"date": {"start": "2026-08-05"}}


def test_posted_at_is_omitted_when_the_schema_lacks_it() -> None:
    """A table without the property must never receive it -- Notion rejects
    an unrecognized property name for the whole write, not just that field."""
    job = _job_record(datetime(2026, 8, 5, tzinfo=UTC))

    properties = build_daily_properties(job, {"Status": "select"})

    assert "Posted At" not in properties


def test_posted_at_is_omitted_when_the_job_has_no_posted_date() -> None:
    job = _job_record(None)

    properties = build_daily_properties(job, {"Posted At": "date"})

    assert "Posted At" not in properties
