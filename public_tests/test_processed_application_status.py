from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from job_scraper.application.process_candidate import (
    CandidateProcessingContext,
    ProcessJobCandidate,
)
from job_scraper.domain.decisions import Decision, RejectionReason
from job_scraper.domain.policies import FilterPolicy


def test_not_interested_status_stays_excluded_from_candidate_processing() -> None:
    class Repository:
        def upsert_job(self, _job: object, _run_id: str) -> tuple[str, bool]:
            return "fictional-job", True

        def get_application_status(self, _job_id: str) -> str:
            return "not_interested"

    class Pipeline:
        def evaluate(self, _job: object, _context: object) -> Decision:
            return Decision.accept()

    processor = ProcessJobCandidate(
        Repository(),  # type: ignore[arg-type]
        Pipeline(),  # type: ignore[arg-type]
        lambda raw, _policy: raw,  # type: ignore[arg-type,return-value]
    )
    job = SimpleNamespace(raw_payload={})

    result = processor.process_normalized(  # type: ignore[arg-type]
        job,
        CandidateProcessingContext(
            profile_id="fictional-profile",
            run_id="fictional-run",
            started_at=datetime(2026, 1, 1, tzinfo=UTC),
            policy=FilterPolicy(countries=("DE",)),
        ),
    )

    assert not result.decision.accepted
    assert result.decision.reason is RejectionReason.ALREADY_PROCESSED
    assert result.decision.step == "processed_status"
