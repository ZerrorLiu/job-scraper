"""The downstream screening contract.

These tests pin the shape a downstream screener depends on. A change that
breaks one of them is a contract change, not an implementation detail, and
needs `SCHEMA_VERSION` to move with it.
"""

from datetime import UTC, date, datetime

import pytest

from job_scraper.application.screening_feed import build_feed_document, select_screenable
from job_scraper.cli.feed import resolve_date_window, resolve_window
from job_scraper.domain.screening_feed import (
    SCHEMA_VERSION,
    SETTLED_STATUSES,
    Publication,
    ScreeningFeedRecord,
)


def _record(**overrides) -> ScreeningFeedRecord:
    fields = {
        "job_id": "job-1",
        "profile_id": "cpp",
        "title": "Senior C++ Engineer",
        "company": "Example GmbH",
        "location": "Berlin",
        "language": "en",
        "url": "https://example.invalid/jobs/1",
        "description": "Write C++.",
        "first_seen_at": "2026-08-26T09:00:00+00:00",
        "application_status": "new",
        "publication": Publication("notion_daily", "page-1", "database-1"),
    }
    fields.update(overrides)
    return ScreeningFeedRecord(**fields)


def test_unpublished_job_reports_itself_as_unpublished():
    assert not Publication().is_published
    assert Publication("notion_daily", "page-1", "db-1").is_published


def test_document_carries_version_window_and_count():
    since = datetime(2026, 8, 25, 22, 0, tzinfo=UTC)
    until = datetime(2026, 8, 26, 22, 0, tzinfo=UTC)
    generated = datetime(2026, 8, 26, 23, 30, tzinfo=UTC)

    document = build_feed_document([_record()], since=since, until=until, generated_at=generated)

    assert document["schema_version"] == SCHEMA_VERSION
    assert document["window"] == {"since": since.isoformat(), "until": until.isoformat()}
    assert document["generated_at"] == generated.isoformat()
    assert document["record_count"] == 1


def test_empty_window_still_reports_the_window_it_was_asked_for():
    """A caching screener cannot reconstruct this from zero records."""
    since = datetime(2026, 8, 25, 22, 0, tzinfo=UTC)
    until = datetime(2026, 8, 26, 22, 0, tzinfo=UTC)

    document = build_feed_document([], since=since, until=until, generated_at=until)

    assert document["record_count"] == 0
    assert document["window"]["since"] == since.isoformat()


def test_record_serializes_every_contract_field():
    document = build_feed_document(
        [_record()],
        since=datetime(2026, 8, 26, tzinfo=UTC),
        until=datetime(2026, 8, 27, tzinfo=UTC),
        generated_at=datetime(2026, 8, 27, tzinfo=UTC),
    )
    record = document["records"][0]

    assert set(record) == {
        "job_id",
        "profile_id",
        "title",
        "company",
        "location",
        "language",
        "url",
        "description",
        "first_seen_at",
        "application_status",
        "publication",
    }
    assert record["publication"] == {
        "sink_id": "notion_daily",
        "external_id": "page-1",
        "container_id": "database-1",
    }


def test_published_only_drops_unpublished_jobs():
    records = [_record(job_id="a"), _record(job_id="b", publication=Publication())]

    kept = select_screenable(records, published_only=True, excluded_statuses=())

    assert [record.job_id for record in kept] == ["a"]


def test_settled_statuses_are_excluded_case_insensitively():
    records = [
        _record(job_id="a", application_status="new"),
        _record(job_id="b", application_status="Applied"),
        _record(job_id="c", application_status="not_interested"),
    ]

    kept = select_screenable(records, published_only=False, excluded_statuses=SETTLED_STATUSES)

    assert [record.job_id for record in kept] == ["a"]


def test_nothing_is_dropped_when_no_filter_is_requested():
    records = [
        _record(job_id="a", application_status="applied"),
        _record(job_id="b", publication=Publication()),
    ]

    kept = select_screenable(records, published_only=False, excluded_statuses=())

    assert [record.job_id for record in kept] == ["a", "b"]


def test_window_covers_whole_local_days_not_a_rolling_24h():
    """A screener asking for "today" means the calendar day, whatever the hour."""
    now = datetime(2026, 8, 26, 20, 30, tzinfo=UTC)

    since, until = resolve_window("Europe/Berlin", 1, now)

    # 2026-08-26 in Berlin (UTC+2) runs 00:00..24:00 local == 22:00 the previous
    # day .. 22:00 today in UTC.
    assert since == datetime(2026, 8, 25, 22, 0, tzinfo=UTC)
    assert until == datetime(2026, 8, 26, 22, 0, tzinfo=UTC)


def test_since_days_counts_calendar_days_back():
    now = datetime(2026, 8, 26, 20, 30, tzinfo=UTC)

    since, until = resolve_window("Europe/Berlin", 3, now)

    assert since == datetime(2026, 8, 23, 22, 0, tzinfo=UTC)
    assert until == datetime(2026, 8, 26, 22, 0, tzinfo=UTC)


def test_since_days_zero_is_treated_as_today_only():
    now = datetime(2026, 8, 26, 20, 30, tzinfo=UTC)

    assert resolve_window("Europe/Berlin", 0, now) == resolve_window("Europe/Berlin", 1, now)


def test_explicit_dates_cover_whole_local_days_inclusive_of_until():
    """An operator reading "the 24th through the 26th" means all three days."""
    since, until = resolve_date_window("Europe/Berlin", date(2026, 8, 24), date(2026, 8, 26))

    assert since == datetime(2026, 8, 23, 22, 0, tzinfo=UTC)
    assert until == datetime(2026, 8, 26, 22, 0, tzinfo=UTC)


def test_a_single_explicit_date_is_that_one_day():
    since, until = resolve_date_window("Europe/Berlin", date(2026, 8, 24), None)

    assert since == datetime(2026, 8, 23, 22, 0, tzinfo=UTC)
    assert until == datetime(2026, 8, 24, 22, 0, tzinfo=UTC)


def test_a_reversed_explicit_range_is_rejected():
    with pytest.raises(ValueError, match="before"):
        resolve_date_window("Europe/Berlin", date(2026, 8, 26), date(2026, 8, 24))
