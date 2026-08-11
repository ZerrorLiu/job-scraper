from __future__ import annotations

from datetime import datetime
from typing import Protocol

from job_scraper.domain.decisions import Decision
from job_scraper.domain.models import JobHistorySnapshot, JobRecord, RunStats


class JobRepository(Protocol):
    def create_run(self, source: str, started_at: datetime) -> RunStats: ...

    def finish_run(self, stats: RunStats, status: str) -> None: ...

    def upsert_job(self, job: JobRecord, run_id: str) -> tuple[str, bool]: ...

    def get_application_status(self, job_id: str) -> str: ...

    def has_recent_not_interested_match(self, job: JobRecord, started_at: datetime) -> bool: ...

    def get_job_history(
        self,
        job_id: str,
        company_name: str,
        started_at: datetime,
    ) -> JobHistorySnapshot: ...


class CandidateDecisionRecorder(Protocol):
    def record_candidate(
        self,
        job: JobRecord,
        decision: Decision,
        *,
        profile_id: str,
        run_id: str,
        evaluated_at: datetime,
        legacy_job_id: str = "",
    ) -> str: ...
