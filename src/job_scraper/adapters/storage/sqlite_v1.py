from __future__ import annotations

from datetime import datetime
from pathlib import Path

from job_scraper.domain.models import JobHistorySnapshot, JobRecord, RunStats
from job_scraper.storage.db import Database


class SQLiteV1Repository:
    """Port adapter that preserves the existing SQLite schema."""

    def __init__(self, path: Path) -> None:
        self.database = Database(path)

    def initialize(self) -> None:
        self.database.initialize()

    def create_run(self, source: str, started_at: datetime) -> RunStats:
        return self.database.create_run(source, started_at)

    def finish_run(self, stats: RunStats, status: str) -> None:
        self.database.finish_run(stats, status)

    def upsert_job(self, job: JobRecord, run_id: str) -> tuple[str, bool]:
        return self.database.upsert_job(job, run_id)

    def get_application_status(self, job_id: str) -> str:
        return self.database.get_application_status(job_id)

    def get_job_history(
        self,
        job_id: str,
        company_name: str,
        started_at: datetime,
    ) -> JobHistorySnapshot:
        return self.database.get_job_history(job_id, company_name, started_at)
