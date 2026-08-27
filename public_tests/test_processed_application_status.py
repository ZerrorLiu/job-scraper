from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from job_scraper.application.process_candidate import (
    CandidateProcessingContext,
    ProcessJobCandidate,
)
from job_scraper.domain.decisions import Decision, RejectionReason
from job_scraper.domain.policies import FilterPolicy


def test_only_not_interested_status_excludes_candidate_processing() -> None:
    class Repository:
        def __init__(self) -> None:
            self.status = ""

        def upsert_job(self, _job: object, _run_id: str) -> tuple[str, bool]:
            return "fictional-job", True

        def get_application_status(self, _job_id: str) -> str:
            return self.status

        def has_recent_not_interested_match(self, _job: object, _started_at: object) -> bool:
            return False

    class Pipeline:
        def evaluate(self, _job: object, _context: object) -> Decision:
            return Decision.accept()

    repository = Repository()
    processor = ProcessJobCandidate(
        repository,  # type: ignore[arg-type]
        Pipeline(),  # type: ignore[arg-type]
        lambda raw, _policy: raw,  # type: ignore[arg-type,return-value]
    )
    job = SimpleNamespace(raw_payload={})

    repository.status = "applied"
    applied = processor.process_normalized(
        job,  # type: ignore[arg-type]
        CandidateProcessingContext(
            profile_id="fictional-profile",
            run_id="fictional-run",
            started_at=datetime(2026, 1, 1, tzinfo=UTC),
            policy=FilterPolicy(countries=("DE",)),
        ),
    )

    repository.status = "not_interested"
    not_interested = processor.process_normalized(
        job,  # type: ignore[arg-type]
        CandidateProcessingContext(
            profile_id="fictional-profile",
            run_id="fictional-run",
            started_at=datetime(2026, 1, 1, tzinfo=UTC),
            policy=FilterPolicy(countries=("DE",)),
        ),
    )

    assert applied.decision.accepted
    assert not_interested.decision.accepted is False
    assert not_interested.decision.reason is RejectionReason.ALREADY_PROCESSED
    assert not_interested.decision.step == "processed_status"
