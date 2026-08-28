"""The decisions one profile run makes, separated from performing that run.

`run_daily.main` is the process: it opens databases, talks to the network, and
draws a dashboard. Buried inside that procedure were a handful of ordinary
decisions -- which sinks to use, where the export goes, which exit code the run
deserves -- that need no I/O at all but could only be reached by driving the
whole procedure. Naming them here makes them directly answerable, and lets
`main` read as a sequence of steps rather than a sequence of steps interleaved
with rules.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from job_scraper.domain.policies import FilterPolicy

# Sinks whose selection depends on a run flag rather than on the profile alone.
CSV_SINK_ID = "csv"
NOTION_SINK_ID = "notion_daily"

DEFAULT_SINK_IDS: tuple[str, ...] = (CSV_SINK_ID, NOTION_SINK_ID)


@dataclass(frozen=True, slots=True)
class RunPlan:
    """What a run intends to do, decided before anything is performed."""

    profile_id: str
    track_label: str
    sink_ids: tuple[str, ...]
    export_destination: Path | None
    sink_policy: FilterPolicy

    @property
    def publishes_to_notion(self) -> bool:
        return NOTION_SINK_ID in self.sink_ids


@dataclass(frozen=True, slots=True)
class ProfileRunRequest:
    """Typed input shared by CLI, scheduler orchestration, and one profile run."""

    config_path: Path
    export_csv: str | None = None
    skip_export: bool = False
    skip_notion: bool = False
    ignore_post_age: bool = False
    post_age_days: int | None = None
    search_queries: tuple[str, ...] = ()
    only_sources: tuple[str, ...] = ()


def resolve_profile_id(profile_id: str | None, track_label: str) -> str:
    """A profile's own id when it has one, else the track label.

    A run without a profile definition still needs a stable identity to record
    decisions and publications against.
    """
    return profile_id or track_label


def select_sink_ids(
    configured: tuple[str, ...],
    *,
    skip_export: bool,
    skip_notion: bool,
    has_export_destination: bool,
) -> tuple[str, ...]:
    """Narrow the profile's sinks by this run's flags.

    Order is preserved: the profile decides the sequence, the flags only
    remove. A csv sink with nowhere to write is dropped as well as one that was
    explicitly skipped, because the sink cannot be constructed without a
    destination.
    """
    selected = list(configured)
    if skip_export or not has_export_destination:
        selected = [sink_id for sink_id in selected if sink_id != CSV_SINK_ID]
    if skip_notion:
        selected = [sink_id for sink_id in selected if sink_id != NOTION_SINK_ID]
    return tuple(selected)


def resolve_export_destination(
    *,
    explicit_destination: str | None,
    skip_export: bool,
    timezone_name: str,
    export_dir: Path,
    started_at: datetime,
    file_prefix: str,
) -> Path | None:
    if skip_export:
        return None
    if explicit_destination:
        return Path(explicit_destination)
    return build_daily_export_path(
        export_dir=export_dir,
        timezone_name=timezone_name,
        started_at=started_at,
        file_prefix=file_prefix,
    )


def build_daily_export_path(
    *,
    export_dir: Path,
    timezone_name: str,
    started_at: datetime,
    file_prefix: str = "jobs",
) -> Path:
    local_date = started_at.astimezone(ZoneInfo(timezone_name)).date().isoformat()
    normalized_prefix = " ".join((file_prefix or "jobs").split()).strip().replace(" ", "_")
    return export_dir / f"{normalized_prefix}_{local_date}.csv"


def effective_post_age_hours(
    configured_hours: int,
    *,
    override_days: int | None = None,
    ignore_post_age: bool = False,
) -> int:
    """Resolve one source's freshness window. Zero means "no age limit"."""
    if ignore_post_age:
        return 0
    if override_days is not None:
        return override_days * 24
    return configured_hours


def run_exit_code(
    *,
    collection_failed: bool,
    publish_had_errors: bool,
    accepted_count: int,
) -> int:
    """Translate what happened into an exit code a scheduler can act on.

    A source failure alone is not fatal: the other sources' jobs still reached
    the sinks, so the run is degraded (1) rather than aborted. It only becomes
    a hard failure when nothing at all got through -- but that is still exit 1,
    because 2 is reserved for a run that never started (preflight).
    """
    del accepted_count
    return 1 if collection_failed or publish_had_errors else 0


def acquisition_produced_nothing(*, collection_failed: bool, accepted_count: int) -> bool:
    """True when every source failed and no job reached publication."""
    return collection_failed and accepted_count == 0
