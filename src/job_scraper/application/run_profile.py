from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from job_scraper.application.aggregation import AcceptedJob, merge_accepted_job
from job_scraper.application.process_candidate import (
    CandidateProcessingContext,
    ProcessJobCandidate,
)
from job_scraper.domain.models import JobRecord, RunStats, SearchWindow
from job_scraper.domain.policies import FilterPolicy
from job_scraper.ports.repositories import JobRepository
from job_scraper.ports.sources import JobSource


@dataclass(frozen=True, slots=True)
class CandidateEvent:
    source_id: str
    seen_index: int
    outcome: str
    reason: str
    job: JobRecord
    stats: RunStats
    reject_counts: dict[str, int]


@dataclass(frozen=True, slots=True)
class SourceRunResult:
    source_id: str
    stats: RunStats
    reject_counts: dict[str, int]
    failed: bool = False
    error: str = ""


class RunProfileSource:
    """Run one source through the shared candidate use case."""

    def __init__(
        self,
        repository: JobRepository,
        processor: ProcessJobCandidate,
        *,
        on_candidate: Callable[[CandidateEvent], None] | None = None,
    ) -> None:
        self._repository = repository
        self._processor = processor
        self._on_candidate = on_candidate or (lambda event: None)

    def execute(
        self,
        source: JobSource,
        *,
        profile_id: str,
        started_at: datetime,
        overlap_hours: int,
        max_post_age_hours: int,
        policy: FilterPolicy,
        accepted_jobs: dict[str, AcceptedJob],
    ) -> SourceRunResult:
        stats = self._repository.create_run(source.source_name, started_at)
        reject_counts: Counter[str] = Counter()
        context = CandidateProcessingContext(
            profile_id=profile_id,
            run_id=stats.run_id,
            started_at=started_at,
            policy=policy,
        )
        window = SearchWindow(
            started_at=started_at,
            overlap_hours=overlap_hours,
            post_age_hours=max_post_age_hours,
        )
        try:
            for raw in source.collect(window):
                stats.jobs_seen += 1
                try:
                    result = self._processor.process_raw(raw, context)
                except Exception as record_exc:
                    # One malformed record should not abort the remaining
                    # records from this source; the iterator itself is still
                    # healthy. A genuinely broken iterator (source.collect
                    # raising while advancing) is still caught by the outer
                    # except below, which correctly fails the whole source.
                    stats.jobs_failed += 1
                    stats.errors.append(str(record_exc))
                    reject_counts["processing_error"] += 1
                    continue
                reason = "" if result.decision.reason is None else result.decision.reason.value
                if reason:
                    stats.jobs_filtered += 1
                    reject_counts[reason] += 1
                    outcome = "FAIL"
                else:
                    merged = merge_accepted_job(
                        accepted_jobs,
                        result.job,
                        result.job_id,
                    )
                    if merged:
                        stats.jobs_updated += 1
                        outcome = "PASS merge"
                    elif result.is_new:
                        stats.jobs_new += 1
                        outcome = "PASS new"
                    else:
                        stats.jobs_updated += 1
                        outcome = "PASS old"
                self._on_candidate(
                    CandidateEvent(
                        source_id=source.source_name,
                        seen_index=stats.jobs_seen,
                        outcome=outcome,
                        reason=reason,
                        job=result.job,
                        stats=stats,
                        reject_counts=dict(reject_counts),
                    )
                )
        except Exception as exc:
            stats.finished_at = datetime.now(UTC)
            stats.jobs_failed += 1
            stats.errors.append(str(exc))
            self._repository.finish_run(stats, "failed")
            return SourceRunResult(
                source_id=source.source_name,
                stats=stats,
                reject_counts=dict(reject_counts),
                failed=True,
                error=str(exc),
            )

        stats.finished_at = datetime.now(UTC)
        self._repository.finish_run(stats, "completed")
        return SourceRunResult(
            source_id=source.source_name,
            stats=stats,
            reject_counts=dict(reject_counts),
        )
