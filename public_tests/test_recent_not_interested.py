from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from job_scraper.adapters.sinks.notion_workflow import import_processed_statuses
from job_scraper.application.process_candidate import (
    CandidateProcessingContext,
    ProcessJobCandidate,
)
from job_scraper.domain.decisions import Decision, RejectionReason
from job_scraper.domain.models import JobRecord
from job_scraper.storage.db import Database


class AcceptingPipeline:
    def evaluate(self, job: JobRecord, context) -> Decision:
        del job, context
        return Decision.accept()


def test_recent_not_interested_rejects_a_reposted_job(tmp_path) -> None:
    database = Database(tmp_path / "jobs.db")
    database.initialize()
    decision_at = datetime(2026, 7, 20, 10, 0, tzinfo=UTC)
    old_job = make_job("old", decision_at, "Berlin")
    old_run = database.create_run("linkedin", decision_at)
    old_job_id, _ = database.upsert_job(old_job, old_run.run_id)
    database.set_application_status(old_job_id, "not_interested", edited_at=decision_at)

    started_at = datetime(2026, 8, 5, 10, 0, tzinfo=UTC)
    repost = make_job("repost", started_at, "Berlin")
    processor = ProcessJobCandidate(database, AcceptingPipeline(), lambda job, policy: job)

    result = processor.process_normalized(
        repost,
        CandidateProcessingContext(
            profile_id="fictional-profile",
            run_id=database.create_run("linkedin", started_at).run_id,
            started_at=started_at,
            policy=SimpleNamespace(),
        ),
    )

    assert result.decision.accepted is False
    assert result.decision.reason == RejectionReason.RECENTLY_NOT_INTERESTED


def test_recent_not_interested_expires_at_thirty_days(tmp_path) -> None:
    database = Database(tmp_path / "jobs.db")
    database.initialize()
    started_at = datetime(2026, 8, 5, 10, 0, tzinfo=UTC)
    decision_at = started_at - timedelta(days=30)
    old_job = make_job("old", decision_at, "Berlin")
    old_run = database.create_run("linkedin", decision_at)
    old_job_id, _ = database.upsert_job(old_job, old_run.run_id)
    database.set_application_status(old_job_id, "not_interested", edited_at=decision_at)

    assert (
        database.has_recent_not_interested_match(
            make_job("repost", started_at, "Berlin"), started_at
        )
        is False
    )


def test_recent_not_interested_does_not_cross_roles_or_locations(tmp_path) -> None:
    database = Database(tmp_path / "jobs.db")
    database.initialize()
    decision_at = datetime(2026, 7, 20, 10, 0, tzinfo=UTC)
    old_job = make_job("old", decision_at, "Berlin")
    old_run = database.create_run("linkedin", decision_at)
    old_job_id, _ = database.upsert_job(old_job, old_run.run_id)
    database.set_application_status(old_job_id, "not_interested", edited_at=decision_at)

    started_at = datetime(2026, 8, 5, 10, 0, tzinfo=UTC)
    different_location = make_job("repost", started_at, "Munich")
    different_title = make_job("different", started_at, "Berlin", title="Data Engineer")

    assert database.has_recent_not_interested_match(different_location, started_at) is False
    assert database.has_recent_not_interested_match(different_title, started_at) is False


def test_notion_import_preserves_page_edit_time(tmp_path) -> None:
    database = Database(tmp_path / "jobs.db")
    database.initialize()
    seen_at = datetime(2026, 7, 20, 10, 0, tzinfo=UTC)
    job = make_job("job", seen_at, "Berlin")
    run = database.create_run("linkedin", seen_at)
    job_id, _ = database.upsert_job(job, run.run_id)
    database.upsert_notion_state(job_id, "page-1", "source-1", "hash-1", "synced")

    notion = SimpleNamespace(
        config=SimpleNamespace(database_id="database-1"),
        resolve_data_source_id=lambda: "source-1",
        list_data_source_pages=lambda data_source_id: [
            {
                "id": "page-1",
                "last_edited_time": "2026-07-22T12:34:56.000Z",
                "properties": {"Status": {"select": {"name": "Not Interested"}}},
            }
        ],
    )

    assert import_processed_statuses(database, notion) == 1
    with database.connect() as connection:
        row = connection.execute(
            "SELECT application_status, last_user_edit_at FROM application_state WHERE job_id = ?",
            (job_id,),
        ).fetchone()
    assert row["application_status"] == "not_interested"
    assert row["last_user_edit_at"] == "2026-07-22T12:34:56+00:00"


def make_job(
    source_job_id: str,
    seen_at: datetime,
    city: str,
    *,
    title: str = "AI Engineer",
) -> JobRecord:
    return JobRecord(
        source="linkedin",
        source_job_id=source_job_id,
        source_url=f"https://linkedin.example/jobs/{source_job_id}",
        canonical_url=f"https://linkedin.example/jobs/{source_job_id}",
        title=title,
        company_name="Acme",
        location_raw=f"{city}, Germany",
        country="DE",
        city=city,
        region="",
        remote_type="hybrid",
        employment_type="full-time",
        seniority="mid",
        posted_at=seen_at,
        first_seen_at=seen_at,
        scraped_at=seen_at,
        job_description=f"Build the {source_job_id} platform.",
        description_language="English",
        english_ratio=1.0,
        keyword_hits=["ai"],
        tech_stack=["Python"],
        salary_text="",
        salary_min=None,
        salary_max=None,
        salary_currency=None,
        dedupe_key=f"dedupe-{source_job_id}",
        raw_payload={},
    )
