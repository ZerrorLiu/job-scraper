"""The feed read, against a real store rather than a stub.

The contract's value is that a downstream screener stops opening these tables
itself. That only holds if the read here actually returns what the tables hold,
including the two LEFT joins that decide whether a job looks published or
settled -- so these run against a real SQLite file.
"""

from datetime import UTC, datetime

from job_scraper.domain.models import JobRecord
from job_scraper.storage.db import Database

WINDOW_START = datetime(2026, 8, 26, tzinfo=UTC)
WINDOW_END = datetime(2026, 8, 27, tzinfo=UTC)


def _job(**overrides) -> JobRecord:
    seen_at = overrides.pop("seen_at", datetime(2026, 8, 26, 9, 0, tzinfo=UTC))
    fields = {
        "source": "linkedin",
        "source_job_id": "src-1",
        "source_url": "https://example.invalid/jobs/1",
        "canonical_url": "https://example.invalid/jobs/1",
        "title": "Senior C++ Engineer",
        "company_name": "Example GmbH",
        "location_raw": "Berlin, Germany",
        "country": "DE",
        "city": "Berlin",
        "region": "",
        "remote_type": "unknown",
        "employment_type": "full_time",
        "seniority": "senior",
        "posted_at": None,
        "first_seen_at": seen_at,
        "scraped_at": seen_at,
        "job_description": "Write modern C++.",
        "description_language": "English",
        "english_ratio": 1.0,
        "keyword_hits": [],
        "tech_stack": [],
        "salary_text": "",
        "salary_min": None,
        "salary_max": None,
        "salary_currency": None,
        "dedupe_key": "dedupe-1",
    }
    fields.update(overrides)
    return JobRecord(**fields)


def _seed(tmp_path) -> Database:
    database = Database(tmp_path / "jobs.db")
    database.initialize()
    return database


def test_a_published_job_carries_its_page_and_container(tmp_path):
    database = _seed(tmp_path)
    run = database.create_run("linkedin", WINDOW_START)
    job_id, _ = database.upsert_job(_job(), run.run_id)
    database.upsert_notion_state(job_id, "page-1", "database-1", "hash-1", "synced")

    records = database.read_screening_feed(
        profile_id="cpp", since=WINDOW_START, until_exclusive=WINDOW_END
    )

    assert len(records) == 1
    assert records[0].profile_id == "cpp"
    assert records[0].title == "Senior C++ Engineer"
    assert records[0].publication.is_published
    assert records[0].publication.external_id == "page-1"
    assert records[0].publication.container_id == "database-1"
    assert records[0].publication.sink_id == "notion_daily"


def test_an_unpublished_job_is_still_returned(tmp_path):
    """The LEFT join must not silently shorten the feed."""
    database = _seed(tmp_path)
    run = database.create_run("linkedin", WINDOW_START)
    database.upsert_job(_job(), run.run_id)

    records = database.read_screening_feed(
        profile_id="cpp", since=WINDOW_START, until_exclusive=WINDOW_END
    )

    assert len(records) == 1
    assert not records[0].publication.is_published
    assert records[0].publication.sink_id == ""


def test_a_job_with_no_application_row_defaults_to_new(tmp_path):
    database = _seed(tmp_path)
    run = database.create_run("linkedin", WINDOW_START)
    database.upsert_job(_job(), run.run_id)

    records = database.read_screening_feed(
        profile_id="cpp", since=WINDOW_START, until_exclusive=WINDOW_END
    )

    assert records[0].application_status == "new"


def test_application_status_is_reported(tmp_path):
    database = _seed(tmp_path)
    run = database.create_run("linkedin", WINDOW_START)
    job_id, _ = database.upsert_job(_job(), run.run_id)
    database.set_application_status(job_id, "applied")

    records = database.read_screening_feed(
        profile_id="cpp", since=WINDOW_START, until_exclusive=WINDOW_END
    )

    assert records[0].application_status == "applied"


def test_the_window_is_half_open(tmp_path):
    """`until` is exclusive, so a job seen exactly at the boundary is next day's."""
    database = _seed(tmp_path)
    run = database.create_run("linkedin", WINDOW_START)
    database.upsert_job(_job(seen_at=WINDOW_END, dedupe_key="dedupe-boundary"), run.run_id)

    records = database.read_screening_feed(
        profile_id="cpp", since=WINDOW_START, until_exclusive=WINDOW_END
    )

    assert records == []


def test_a_job_before_the_window_is_excluded(tmp_path):
    database = _seed(tmp_path)
    run = database.create_run("linkedin", WINDOW_START)
    database.upsert_job(
        _job(seen_at=datetime(2026, 8, 20, tzinfo=UTC), dedupe_key="dedupe-old"), run.run_id
    )

    records = database.read_screening_feed(
        profile_id="cpp", since=WINDOW_START, until_exclusive=WINDOW_END
    )

    assert records == []


def test_the_url_falls_back_to_the_source_url(tmp_path):
    database = _seed(tmp_path)
    run = database.create_run("linkedin", WINDOW_START)
    database.upsert_job(_job(canonical_url=""), run.run_id)

    records = database.read_screening_feed(
        profile_id="cpp", since=WINDOW_START, until_exclusive=WINDOW_END
    )

    assert records[0].url == "https://example.invalid/jobs/1"


def test_reading_an_empty_store_returns_no_records(tmp_path):
    database = _seed(tmp_path)

    records = database.read_screening_feed(
        profile_id="cpp", since=WINDOW_START, until_exclusive=WINDOW_END
    )

    assert records == []
