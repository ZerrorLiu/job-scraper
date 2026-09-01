from __future__ import annotations

from datetime import UTC, datetime
from io import StringIO
from pathlib import Path
from threading import Event

from job_scraper.cli.console import LiveRunTable
from job_scraper.collectors.base import SearchWindow
from job_scraper.integrations.email_recommendations import (
    EmailJobCandidate,
    email_candidate_to_raw_job,
)
from job_scraper.jobs import run_all_tracks
from job_scraper.jobs.run_daily import _acquire_sources, _SourceExecution


def test_dashboard_uses_one_fixed_screen_and_one_final_scrollback_table() -> None:
    stream = StringIO()
    dashboard = LiveRunTable(stream=stream, force=True)

    dashboard.start()
    dashboard.update(
        "Core C++",
        "Indeed",
        stage="Done",
        keywords_done=21,
        keywords_total=21,
    )
    dashboard.finish()

    output = stream.getvalue()
    assert "\x1b[?1049h" in output
    assert "\x1b[?1049l" in output
    normal_scrollback = output.split("\x1b[?1049l", maxsplit=1)[1]
    assert normal_scrollback.count("Job Scraper [FINAL]") == 1
    assert "Elapsed" not in normal_scrollback.splitlines()[0]
    assert "Seen Accept   Drop" in normal_scrollback
    assert "Keep" not in normal_scrollback
    assert "21/21" in normal_scrollback


def test_dashboard_keeps_online_sources_before_preseeded_email() -> None:
    dashboard = LiveRunTable()
    dashboard.update("Core C++", "Email", stage="Waiting")
    dashboard.update("Core C++", "Indeed", stage="Fetching")
    dashboard.update("Core C++", "LinkedIn", stage="Fetching")

    rendered = dashboard.render_text()

    assert rendered.index("LinkedIn") < rendered.index("Indeed") < rendered.index("Email")


def test_dashboard_can_show_cloud_state_instead_of_fake_numeric_progress() -> None:
    dashboard = LiveRunTable()
    dashboard.update(
        "Core C++",
        "Indeed",
        stage="Fetching",
        keywords_done=0,
        keywords_total=8,
        progress_text="Running",
    )

    rendered = dashboard.render_text()

    assert "Running" in rendered
    assert "0/8" not in rendered


def test_email_preparation_receives_the_shared_dashboard(monkeypatch) -> None:
    """The live email path is `prepare`; it must be handed the run's dashboard."""

    received: list[object] = []

    def fake_prepare(request: object, *, dashboard: LiveRunTable | None = None) -> str:
        received.extend([request, dashboard])
        return "prepared"

    monkeypatch.setattr(
        run_all_tracks.ingest_email_recommendations,
        "prepare_request",
        fake_prepare,
    )
    dashboard = LiveRunTable()

    request = run_all_tracks.ingest_email_recommendations.EmailPrepareRequest(
        config_path=Path("email.toml"),
        track_config_paths=(),
        lookback_days=1,
        max_messages=0,
        detail_workers=1,
        skip_notion=True,
    )
    assert run_all_tracks._invoke_email_prepare(request, dashboard) == "prepared"
    assert received == [request, dashboard]


def test_all_tracks_builds_a_typed_email_request(tmp_path: Path) -> None:
    config_paths = [tmp_path / "one.toml", tmp_path / "two.toml"]
    request = run_all_tracks.AllTracksRequest(
        config_paths=tuple(config_paths),
        config_dir=tmp_path,
        post_age_days=3,
        email_max_messages=25,
        email_detail_workers=6,
        email_folder="All Mail",
        skip_notion=True,
    )

    email_request = run_all_tracks.build_email_request(request, config_paths)

    assert email_request is not None
    assert email_request.track_config_paths == tuple(config_paths)
    assert email_request.lookback_days == 3
    assert email_request.max_messages == 25
    assert email_request.detail_workers == 6
    assert email_request.folder == "All Mail"
    assert email_request.skip_notion is True


def test_email_card_context_is_audit_data_not_a_job_description() -> None:
    candidate = EmailJobCandidate(
        url="https://example.com/jobs/ai-engineer",
        title="AI Engineer",
        company_name="Example",
        location_raw="Berlin",
        context="AI Engineer Example Berlin. Next card: Software Intern.",
        message_id="message@example.com",
        email_subject="AI jobs",
        email_from="jobs@example.com",
        email_date=datetime(2026, 7, 24, tzinfo=UTC),
        anchor_text="AI Engineer",
    )

    raw = email_candidate_to_raw_job(candidate)

    assert raw.job_description == ""
    assert raw.raw_payload["card_context"] == candidate.context
    assert raw.raw_payload["description_source"] == "none"


def test_completed_source_is_yielded_while_another_source_is_still_fetching() -> None:
    release_slow_source = Event()

    class Source:
        def __init__(self, name: str, wait: bool) -> None:
            self.source_name = name
            self.wait = wait

        def collect(self, _window: SearchWindow) -> list[object]:
            if self.wait:
                release_slow_source.wait(timeout=2)
            return []

    fast = _SourceExecution(Source("linkedin", False), 24)  # type: ignore[arg-type]
    slow = _SourceExecution(Source("indeed", True), 24)  # type: ignore[arg-type]
    completed = _acquire_sources(
        [slow, fast],
        window=SearchWindow(
            started_at=datetime(2026, 7, 24, tzinfo=UTC),
            overlap_hours=24,
            post_age_hours=24,
        ),
        track_label="Core C++",
        dashboard=None,
    )
    try:
        assert next(completed) is fast
    finally:
        release_slow_source.set()
    assert next(completed) is slow


def test_email_preparation_starts_before_online_tracks_finish(monkeypatch, tmp_path) -> None:
    email_started = Event()
    online_finished = Event()
    preparation = object()

    def fake_prepare(_argv: list[str], _dashboard: LiveRunTable | None) -> object:
        email_started.set()
        assert online_finished.wait(timeout=2)
        return preparation

    def fake_daily(_argv: list[str], _runtime: object) -> int:
        assert email_started.wait(timeout=2)
        online_finished.set()
        return 0

    monkeypatch.setattr(run_all_tracks, "_invoke_email_prepare", fake_prepare)
    monkeypatch.setattr(run_all_tracks, "_invoke_run_daily", fake_daily)
    monkeypatch.setattr(
        run_all_tracks.ingest_email_recommendations,
        "finish",
        lambda value, *, dashboard=None: 0 if value is preparation else 1,
    )

    request = run_all_tracks.AllTracksRequest(
        config_paths=(tmp_path / "track.toml",),
        config_dir=tmp_path,
        post_age_days=1,
        skip_export=True,
    )

    assert (
        run_all_tracks._execute(
            request,
            run_all_tracks.run_daily.RuntimeServices(),
            LiveRunTable(),
        )
        == 0
    )
