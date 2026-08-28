"""`run_daily.main` end to end, offline: real database, fake sources and sinks.

These drive the actual orchestration -- preflight, acquisition, filtering,
persistence, publication, exit code -- with the network boundary replaced. What
is asserted is the orchestration's own behavior: what it does when a source
fails, when a sink is skipped, when nothing matches.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from job_scraper.domain.models import RawJobRecord, SearchWindow
from job_scraper.jobs import run_daily
from job_scraper.ports.sinks import PublishResult
from job_scraper.ports.sources import SourceCapabilities

RUNTIME_CONFIG = """
[project]
timezone = "UTC"
database_path = "jobs.db"
export_dir = "exports"
overlap_hours = 24
track_label = "Fictional Track"
export_filename_prefix = "jobs_fictional"
recent_post_age_hours = 24
bootstrap_post_age_hours = 24

[filters]
country = "DE"
include_keywords = ["engineer"]
exclude_keywords = ["recruiter"]
target_keywords = ["engineer"]
target_match_scope = "title"
minimum_english_ratio = 0.5
require_english = false
full_time_only = true

[http]
user_agent = "fictional/1.0"
timeout_seconds = 5
base_delay_seconds = 0.0
jitter_seconds = 0.0
max_retries = 0

[sources.linkedin_direct]
enabled = true
max_listing_pages = 1
max_detail_fetches = 10
detail_workers = 1
query_workers = 1
search_queries = ["engineer"]
locations = ["Berlin"]

[notion]
enabled = false
"""


class FakeSource:
    """Yields prepared records, or fails, without touching the network."""

    capabilities = SourceCapabilities(acquisition_mode="direct", platform="fictional")

    def __init__(
        self,
        source_name: str,
        records: list[RawJobRecord],
        *,
        fails_with: str = "",
    ) -> None:
        self.source_name = source_name
        self.source_config = None
        self._records = records
        self._fails_with = fails_with

    def validate_runtime(self) -> None:
        return None

    def collect(self, window: SearchWindow) -> Iterable[RawJobRecord]:
        del window
        if self._fails_with:
            raise RuntimeError(self._fails_with)
        yield from self._records


class RecordingSink:
    def __init__(self, sink_id: str, errors: tuple[str, ...] = ()) -> None:
        self.sink_id = sink_id
        self.errors = errors
        self.published_batches: list[int] = []

    def publish(self, jobs, context) -> PublishResult:
        del context
        self.published_batches.append(len(jobs))
        return PublishResult(sink_id=self.sink_id, published=len(jobs), errors=self.errors)


def _job(title: str, *, job_id: str = "1") -> RawJobRecord:
    return RawJobRecord(
        source="fictional",
        source_job_id=job_id,
        source_url=f"https://jobs.example.invalid/{job_id}",
        canonical_url=f"https://jobs.example.invalid/{job_id}",
        title=title,
        company_name="Fictional GmbH",
        location_raw="Berlin, Germany",
        posted_at_text=(datetime.now(UTC) - timedelta(hours=2)).isoformat(),
        scraped_at=datetime.now(UTC),
        job_description="You will design and deliver systems with the team.",
        employment_type="full-time",
    )


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    (tmp_path / "config.toml").write_text(RUNTIME_CONFIG, encoding="utf-8")
    return tmp_path


def _install(monkeypatch, sources: list[FakeSource], sinks: dict[str, RecordingSink]) -> None:
    monkeypatch.setattr(run_daily, "build_collectors", lambda *a, **k: sources)
    monkeypatch.setattr(run_daily, "build_sink", lambda registry, sink_id, request: sinks[sink_id])
    # No profile definition: the run falls back to the track label as its id.
    monkeypatch.setattr(run_daily, "find_profile_definition", lambda _path: None)
    monkeypatch.setattr(run_daily, "load_dotenv", lambda *a, **k: None)


def _run(workspace: Path, *extra: str) -> int:
    return run_daily.main(["--config", str(workspace / "config.toml"), *extra])


def _titles(database: Path) -> list[str]:
    connection = sqlite3.connect(database)
    try:
        return [str(row[0]) for row in connection.execute("SELECT normalized_title FROM jobs")]
    finally:
        connection.close()


def test_a_clean_run_publishes_matching_jobs_and_exits_zero(workspace, monkeypatch) -> None:
    sinks = {"csv": RecordingSink("csv")}
    _install(
        monkeypatch,
        [FakeSource("linkedin", [_job("Software Engineer"), _job("Data Engineer", job_id="2")])],
        sinks,
    )

    assert _run(workspace, "--skip-notion") == 0
    assert sinks["csv"].published_batches == [2]


def test_filtered_jobs_never_reach_a_sink(workspace, monkeypatch) -> None:
    """Only the engineer matches; the recruiter title is excluded."""
    sinks = {"csv": RecordingSink("csv")}
    _install(
        monkeypatch,
        [
            FakeSource(
                "linkedin", [_job("Software Engineer"), _job("Technical Recruiter", job_id="2")]
            )
        ],
        sinks,
    )

    assert _run(workspace, "--skip-notion") == 0
    assert sinks["csv"].published_batches == [1]


def test_a_run_with_no_matches_still_succeeds(workspace, monkeypatch) -> None:
    """An empty day is not an error."""
    sinks = {"csv": RecordingSink("csv")}
    _install(monkeypatch, [FakeSource("linkedin", [_job("Office Manager")])], sinks)

    assert _run(workspace, "--skip-notion") == 0
    assert sinks["csv"].published_batches == [0]


def test_one_failing_source_degrades_the_run_but_still_publishes(workspace, monkeypatch) -> None:
    sinks = {"csv": RecordingSink("csv")}
    _install(
        monkeypatch,
        [
            FakeSource("indeed", [], fails_with="vendor rejected the request"),
            FakeSource("linkedin", [_job("Software Engineer")]),
        ],
        sinks,
    )

    assert _run(workspace, "--skip-notion") == 1
    assert sinks["csv"].published_batches == [1]


def test_every_source_failing_stops_before_publication(workspace, monkeypatch) -> None:
    """Nothing got through, so there is nothing to publish."""
    sinks = {"csv": RecordingSink("csv")}
    _install(monkeypatch, [FakeSource("linkedin", [], fails_with="upstream is down")], sinks)

    assert _run(workspace, "--skip-notion") == 1
    assert sinks["csv"].published_batches == []


def test_a_failed_preflight_aborts_before_touching_any_source(workspace, monkeypatch) -> None:
    """Exit 2 means the run never started, which a scheduler treats differently."""

    class Unusable(FakeSource):
        def validate_runtime(self) -> None:
            raise RuntimeError("API_KEY is not configured in the environment")

    sinks = {"csv": RecordingSink("csv")}
    _install(monkeypatch, [Unusable("indeed", [_job("Software Engineer")])], sinks)

    assert _run(workspace, "--skip-notion") == 2
    assert sinks["csv"].published_batches == []


def test_a_sink_error_degrades_the_exit_code(workspace, monkeypatch) -> None:
    sinks = {"csv": RecordingSink("csv", errors=("row 3 rejected",))}
    _install(monkeypatch, [FakeSource("linkedin", [_job("Software Engineer")])], sinks)

    assert _run(workspace, "--skip-notion") == 1


def test_skip_flags_remove_the_matching_sinks(workspace, monkeypatch) -> None:
    """A skipped sink is never constructed, let alone published to."""
    built: list[str] = []
    sinks = {"csv": RecordingSink("csv"), "notion_daily": RecordingSink("notion_daily")}

    def tracking_build(registry, sink_id, request):
        built.append(sink_id)
        return sinks[sink_id]

    _install(monkeypatch, [FakeSource("linkedin", [_job("Software Engineer")])], sinks)
    monkeypatch.setattr(run_daily, "build_sink", tracking_build)

    assert _run(workspace, "--skip-notion") == 0
    assert built == ["csv"]

    built.clear()
    assert _run(workspace, "--skip-export") == 0
    assert built == ["notion_daily"]


def test_accepted_jobs_are_persisted_for_the_next_run(workspace, monkeypatch) -> None:
    sinks = {"csv": RecordingSink("csv")}
    _install(monkeypatch, [FakeSource("linkedin", [_job("Software Engineer")])], sinks)

    assert _run(workspace, "--skip-notion") == 0
    assert _titles(workspace / "jobs.db") == ["Software Engineer"]


def test_the_same_job_seen_by_two_sources_is_published_once(workspace, monkeypatch) -> None:
    """Cross-source merging happens before publication, not inside the sink."""
    sinks = {"csv": RecordingSink("csv")}
    _install(
        monkeypatch,
        [
            FakeSource("linkedin", [_job("Software Engineer")]),
            FakeSource("indeed", [_job("Software Engineer", job_id="2")]),
        ],
        sinks,
    )

    assert _run(workspace, "--skip-notion") == 0
    assert sinks["csv"].published_batches == [1]


def test_ignore_post_age_admits_an_old_posting(workspace, monkeypatch) -> None:
    stale = _job("Software Engineer")
    stale.posted_at_text = (datetime.now(UTC) - timedelta(days=30)).isoformat()
    sinks = {"csv": RecordingSink("csv")}
    _install(monkeypatch, [FakeSource("linkedin", [stale])], sinks)

    assert _run(workspace, "--skip-notion") == 0
    assert sinks["csv"].published_batches == [0]

    sinks["csv"].published_batches.clear()
    assert _run(workspace, "--skip-notion", "--ignore-post-age") == 0
    assert sinks["csv"].published_batches == [1]


def _config_with_sources(tmp_path, **enabled: bool):
    from job_scraper.config import (
        AppConfig,
        FiltersConfig,
        HttpConfig,
        NotionConfig,
        ProjectConfig,
        SourceConfig,
    )

    return AppConfig(
        project=ProjectConfig("UTC", tmp_path / "jobs.db", 24, tmp_path, "Fictional"),
        filters=FiltersConfig("US", [], [], [], "title", 0.0),
        http=HttpConfig("fictional", 1, 0, 0, 0),
        sources={name: SourceConfig(enabled=value) for name, value in enabled.items()},
        notion=NotionConfig(enabled=False),
    )


def test_source_selection_narrows_a_run_to_part_of_its_profile(tmp_path) -> None:
    """A profile's source list is a composition decision, not a schedule.

    A board sweep that spends one request per configured employer belongs on a
    weekly timer beside the profile's daily one. Without a run-scoped
    selection, expressing that means duplicating the whole track as a second
    profile just to give one source a different cadence.
    """
    config = _config_with_sources(tmp_path, fictional_daily=True, fictional_weekly=True)

    run_daily.restrict_sources(config, ["fictional_weekly"])

    assert config.sources["fictional_weekly"].enabled is True
    assert config.sources["fictional_daily"].enabled is False


def test_selecting_a_source_the_profile_does_not_enable_is_an_error(tmp_path) -> None:
    """The silent no-op this replaces is indistinguishable from a quiet run.

    A timer that acquires nothing because a profile edit renamed its source
    looks exactly like a source that legitimately found nothing new, and the
    difference only surfaces later as missing data.
    """
    config = _config_with_sources(tmp_path, fictional_daily=True)

    with pytest.raises(ValueError, match="which the profile does not enable"):
        run_daily.restrict_sources(config, ["fictional_weekly"])


def test_selecting_a_source_the_profile_disabled_is_also_an_error(tmp_path) -> None:
    """Being present in the runtime config is not the same as being enabled."""
    config = _config_with_sources(tmp_path, fictional_daily=True, fictional_weekly=False)

    with pytest.raises(ValueError, match="which the profile does not enable"):
        run_daily.restrict_sources(config, ["fictional_weekly"])


def test_an_empty_source_selection_leaves_the_profile_alone(tmp_path) -> None:
    config = _config_with_sources(tmp_path, fictional_daily=True, fictional_weekly=True)

    run_daily.restrict_sources(config, ["   "])

    assert all(settings.enabled for settings in config.sources.values())
