from __future__ import annotations

import csv
import os
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Protocol, cast

from job_scraper.domain.models import AcceptedJob
from job_scraper.domain.policies import FilterPolicy
from job_scraper.pipeline.export_filter import ExportRow, export_row_matches_policy
from job_scraper.ports.sinks import PublishContext, PublishResult

# Each run rewrites the full cumulative history into a date-stamped file, so the
# directory grows by roughly one whole export per run and never shrinks.
# Retention is available but off by default: enabling it deletes files a user
# already has, which is their call to make, not a silent upgrade side effect.
# Set `[project] retained_exports` to opt in.
DEFAULT_RETAINED_EXPORTS = 0


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
        *,
        retained_exports: int = DEFAULT_RETAINED_EXPORTS,
    ) -> None:
        self._reader = reader
        self._destination = destination
        self._policy = policy
        self._retained_exports = retained_exports

    def publish(
        self,
        jobs: Sequence[AcceptedJob],
        context: PublishContext,
    ) -> PublishResult:
        # This sink is cumulative: its content is the whole stored history
        # re-filtered, not just the jobs this run accepted.
        del jobs, context
        # Which languages to read must follow the same two-mode contract the
        # `language` step follows, or the export answers a different question
        # from the acquisition that filled the database. A non-empty
        # `allowed_description_languages` decides by membership and
        # `require_english` does not apply -- reading `require_english` here
        # regardless meant a profile that admitted German acquired German
        # postings and then silently dropped every one of them on the way out.
        # See specs/2026-08-27-description-language-policy-defect.md.
        if self._policy.allowed_description_languages:
            languages = list(self._policy.allowed_description_languages)
        elif self._policy.require_english:
            languages = ["English"]
        else:
            languages = None
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
        self._prune_previous_exports()
        return PublishResult(sink_id=self.sink_id, published=len(rows))

    def _prune_previous_exports(self) -> None:
        """Keep only the newest `retained_exports` files of this export series.

        Scoped to the sibling files this sink itself writes -- the destination's
        own `<prefix>_<date>.csv` shape -- so it can never remove another
        profile's exports or an unrelated file a user put in the directory.
        """
        if self._retained_exports <= 0:
            return
        stem = self._destination.stem
        prefix = stem.rsplit("_", 1)[0] if "_" in stem else stem
        siblings = sorted(
            (
                path
                for path in self._destination.parent.glob(f"{prefix}_*.csv")
                if path.is_file() and _is_dated_export(path, prefix)
            ),
            key=lambda path: path.name,
            reverse=True,
        )
        for stale in siblings[self._retained_exports :]:
            if stale != self._destination:
                stale.unlink(missing_ok=True)


def _is_dated_export(path: Path, prefix: str) -> bool:
    suffix = path.stem[len(prefix) + 1 :]
    parts = suffix.split("-")
    return len(parts) == 3 and all(part.isdigit() for part in parts)
