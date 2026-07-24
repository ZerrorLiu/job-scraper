from __future__ import annotations

import csv
import sqlite3
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol, cast

from job_scraper.domain.models import JobRecord
from job_scraper.domain.policies import FilterPolicy
from job_scraper.pipeline.export_filter import ExportRow, export_row_matches_policy
from job_scraper.ports.sinks import PublishContext, PublishResult


class CumulativeJobReader(Protocol):
    def export_jobs(
        self,
        languages: list[str] | None = None,
    ) -> Sequence[sqlite3.Row]: ...


class CsvSink:
    """Cumulative CSV export adapter preserving the V1 output contract."""

    sink_id = "csv"

    def __init__(
        self,
        reader: CumulativeJobReader,
        destination: Path,
        policy: FilterPolicy,
    ) -> None:
        self._reader = reader
        self._destination = destination
        self._policy = policy

    def publish(
        self,
        jobs: Sequence[JobRecord],
        context: PublishContext,
    ) -> PublishResult:
        del jobs, context
        languages = ["English"] if self._policy.require_english else None
        rows = self._reader.export_jobs(languages=languages)
        rows = [
            row for row in rows if export_row_matches_policy(cast(ExportRow, row), self._policy)
        ]
        if not rows:
            return PublishResult(sink_id=self.sink_id)

        self._destination.parent.mkdir(parents=True, exist_ok=True)
        fieldnames = list(rows[0].keys())
        with self._destination.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows({field: row[field] for field in fieldnames} for row in rows)
        return PublishResult(sink_id=self.sink_id, published=len(rows))
