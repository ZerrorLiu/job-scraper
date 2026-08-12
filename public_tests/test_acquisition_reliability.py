from __future__ import annotations

from datetime import UTC, datetime
from urllib.error import HTTPError

import pytest

from job_scraper.application.acquisition import RequestCoalescer, _is_rate_limit_error
from job_scraper.application.process_candidate import (
    ProcessJobCandidate,
)
from job_scraper.application.run_profile import RunProfileSource
from job_scraper.domain.decisions import Decision
from job_scraper.domain.models import RawJobRecord
from job_scraper.domain.policies import FilterPolicy


def test_request_coalescer_retries_after_a_failure_instead_of_replaying_it() -> None:
    coalescer = RequestCoalescer()
    attempts = 0

    def flaky_then_ok() -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("transient failure")
        return "ok"

    with pytest.raises(RuntimeError):
        coalescer.execute("shared-key", flaky_then_ok)

    # A second, independent call with the same key must retry, not replay
    # the cached exception from the first attempt.
    result = coalescer.execute("shared-key", flaky_then_ok)

    assert result == "ok"
    assert attempts == 2


def test_is_rate_limit_error_does_not_substring_match_exception_text() -> None:
    assert _is_rate_limit_error(RuntimeError("job id 429000 not found")) is False


def test_is_rate_limit_error_trusts_http_429_status() -> None:
    error = HTTPError("https://example.test", 429, "Too Many Requests", None, None)  # type: ignore[arg-type]
    assert _is_rate_limit_error(error) is True


def _raw(source_job_id: str) -> RawJobRecord:
    return RawJobRecord(
        source="fictional",
        source_job_id=source_job_id,
        source_url=f"https://example.test/{source_job_id}",
        canonical_url=f"https://example.test/{source_job_id}",
        title=f"Fictional Engineer {source_job_id}",
        company_name="Example GmbH",
        location_raw="Berlin",
        posted_at_text="",
        scraped_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def test_one_bad_record_does_not_abort_the_rest_of_the_source() -> None:
    class Repository:
        def upsert_job(self, _job: object, _run_id: str) -> tuple[str, bool]:
            return "fictional-job", True

        def get_application_status(self, _job_id: str) -> str:
            return ""

        def has_recent_not_interested_match(self, _job: object, _started_at: object) -> bool:
            return False

        def get_job_history(self, _job_id: str, _company_name: str, _started_at: object) -> object:
            raise AssertionError("not needed for this test")

        def create_run(self, source_name: str, started_at: datetime) -> object:
            from job_scraper.domain.models import RunStats

            return RunStats(run_id="fictional-run", source=source_name, started_at=started_at)

        def finish_run(self, _stats: object, _status: str) -> None:
            return None

    class Pipeline:
        def evaluate(self, _job: object, _context: object) -> Decision:
            return Decision.accept()

    def normalizer(raw: RawJobRecord, _policy: FilterPolicy) -> object:
        if raw.source_job_id == "bad":
            raise ValueError("malformed record")
        from job_scraper.domain.models import JobRecord

        return JobRecord(
            source=raw.source,
            source_job_id=raw.source_job_id,
            source_url=raw.source_url,
            canonical_url=raw.canonical_url,
            title=raw.title,
            company_name=raw.company_name,
            location_raw=raw.location_raw,
            country="DE",
            city="Berlin",
            region="",
            remote_type="onsite",
            employment_type="full-time",
            seniority="unknown",
            posted_at=None,
            first_seen_at=raw.scraped_at,
            scraped_at=raw.scraped_at,
            job_description="",
            description_language="English",
            english_ratio=1.0,
            keyword_hits=[],
            tech_stack=[],
            salary_text="",
            salary_min=None,
            salary_max=None,
            salary_currency=None,
            dedupe_key=raw.source_job_id,
        )

    processor = ProcessJobCandidate(
        Repository(),  # type: ignore[arg-type]
        Pipeline(),  # type: ignore[arg-type]
        normalizer,  # type: ignore[arg-type]
    )

    class Source:
        source_name = "fictional"

        def collect(self, _window: object) -> list[RawJobRecord]:
            return [_raw("good-1"), _raw("bad"), _raw("good-2")]

    runner = RunProfileSource(Repository(), processor)  # type: ignore[arg-type]

    result = runner.execute(
        Source(),  # type: ignore[arg-type]
        profile_id="fictional-profile",
        started_at=datetime(2026, 1, 1, tzinfo=UTC),
        overlap_hours=0,
        max_post_age_hours=0,
        policy=FilterPolicy(countries=("DE",)),
        accepted_jobs={},
    )

    assert result.failed is False
    assert result.stats.jobs_seen == 3
    assert result.stats.jobs_failed == 1
    assert result.stats.jobs_new == 2
    assert result.reject_counts["processing_error"] == 1
