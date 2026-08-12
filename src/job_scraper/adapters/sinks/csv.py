from __future__ import annotations

import csv
import os
import tempfile
from collections.abc import Mapping, Sequence
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
    ) -> Sequence[Mapping[str, object]]: ...


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
        # Write to a temp file in the same directory, then atomically replace
        # the destination, so a crash mid-write cannot leave the existing
        # cumulative CSV truncated.
        descriptor, temp_path_str = tempfile.mkstemp(
            dir=self._destination.parent,
            prefix=f".{self._destination.name}.",
            suffix=".tmp",
        )
        temp_path = Path(temp_path_str)
        try:
            with os.fdopen(descriptor, "w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows({field: row[field] for field in fieldnames} for row in rows)
            os.replace(temp_path, self._destination)
        except BaseException:
            temp_path.unlink(missing_ok=True)
            raise
        return PublishResult(sink_id=self.sink_id, published=len(rows))
