from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from job_scraper.domain.context import EvaluationContext
from job_scraper.domain.decisions import Decision, RejectionReason
from job_scraper.domain.models import JobRecord, RawJobRecord
from job_scraper.domain.policies import FilterPolicy
from job_scraper.ports.processors import CandidateEvaluator, JobNormalizer
from job_scraper.ports.repositories import CandidateDecisionRecorder, JobRepository


@dataclass(frozen=True, slots=True)
class CandidateProcessingContext:
    profile_id: str
    run_id: str
    started_at: datetime
    policy: FilterPolicy


@dataclass(frozen=True, slots=True)
class CandidateProcessingResult:
    job: JobRecord
    decision: Decision
    job_id: str = ""
    is_new: bool = False


class ProcessJobCandidate:
    """Single normalization, evaluation, persistence and history use case."""

    def __init__(
        self,
        repository: JobRepository,
        pipeline: CandidateEvaluator,
        normalizer: JobNormalizer,
        *,
        decision_recorder: CandidateDecisionRecorder | None = None,
        processed_statuses: frozenset[str] = frozenset({"not_interested"}),
    ) -> None:
        self._repository = repository
        self._pipeline = pipeline
        self._normalizer = normalizer
        self._decision_recorder = decision_recorder
        self._processed_statuses = processed_statuses

    def process_raw(
        self,
        raw: RawJobRecord,
        context: CandidateProcessingContext,
    ) -> CandidateProcessingResult:
        return self.process_normalized(self._normalizer(raw, context.policy), context)

    def process_normalized(
        self,
        job: JobRecord,
        context: CandidateProcessingContext,
    ) -> CandidateProcessingResult:
        decision = self._pipeline.evaluate(
            job,
            EvaluationContext(
                profile_id=context.profile_id,
                started_at=context.started_at,
                policy=context.policy,
            ),
        )
        if not decision.accepted:
            self._record(job, decision, context)
            return CandidateProcessingResult(job=job, decision=decision)

        job_id, is_new = self._repository.upsert_job(job, context.run_id)
        status = self._repository.get_application_status(job_id)
        if status.strip().lower() in self._processed_statuses:
            rejected = Decision.reject(
                RejectionReason.ALREADY_PROCESSED,
                step="processed_status",
            )
            self._record(job, rejected, context, legacy_job_id=job_id)
            return CandidateProcessingResult(
                job=job,
                decision=rejected,
                job_id=job_id,
                is_new=is_new,
            )

        if self._repository.has_recent_not_interested_match(job, context.started_at):
            rejected = Decision.reject(
                RejectionReason.RECENTLY_NOT_INTERESTED,
                step="recent_not_interested",
            )
            self._record(job, rejected, context, legacy_job_id=job_id)
            return CandidateProcessingResult(
                job=job,
                decision=rejected,
                job_id=job_id,
                is_new=is_new,
            )

        self._record(job, Decision.accept(), context, legacy_job_id=job_id)
        return CandidateProcessingResult(
            job=job,
            decision=Decision.accept(),
            job_id=job_id,
            is_new=is_new,
        )

    def _record(
        self,
        job: JobRecord,
        decision: Decision,
        context: CandidateProcessingContext,
        *,
        legacy_job_id: str = "",
    ) -> None:
        if self._decision_recorder is None:
            return
        self._decision_recorder.record_candidate(
            job,
            decision,
            profile_id=context.profile_id,
            run_id=context.run_id,
            evaluated_at=context.started_at,
            legacy_job_id=legacy_job_id,
        )
