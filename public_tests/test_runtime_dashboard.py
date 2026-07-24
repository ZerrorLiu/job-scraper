from __future__ import annotations

from datetime import UTC, datetime
from io import StringIO
from threading import Event

from job_scraper.cli.console import LiveRunTable
from job_scraper.collectors.base import SearchWindow
from job_scraper.integrations.email_recommendations import (
    EmailJobCandidate,
    email_candidate_to_raw_job,
)
from job_scraper.jobs import run_all_tracks
from job_scraper.jobs.run_daily import _acquire_sources, _SourceExecution
from job_scraper.pipeline.role_filter import has_excluded_keyword, is_full_time_role


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
    assert "21/21" in normal_scrollback


def test_dashboard_keeps_online_sources_before_preseeded_email() -> None:
    dashboard = LiveRunTable()
    dashboard.update("Core C++", "Email", stage="Waiting")
    dashboard.update("Core C++", "Indeed", stage="Fetching")
    dashboard.update("Core C++", "LinkedIn", stage="Fetching")

    rendered = dashboard.render_text()

    assert rendered.index("LinkedIn") < rendered.index("Indeed") < rendered.index("Email")


def test_email_ingest_receives_the_shared_dashboard(monkeypatch) -> None:
    received: list[object] = []

    def fake_email(argv: list[str], *, dashboard: LiveRunTable | None = None) -> int:
        received.extend([argv, dashboard])
        return 0

    monkeypatch.setattr(run_all_tracks.ingest_email_recommendations, "main", fake_email)
    dashboard = LiveRunTable()

    assert run_all_tracks._invoke_email_ingest(["--skip-notion"], dashboard) == 0
    assert received == [["--skip-notion"], dashboard]


def test_title_level_filters_do_not_read_job_description() -> None:
    description = (
        "Coordinate internally and externally. During hiring, a recruiter "
        "will explain the compensation range."
    )

    assert is_full_time_role("Software Engineer", description, "full-time")
    assert not has_excluded_keyword(
        "Advanced Data Analyst",
        description,
        "full-time",
        ["recruiter"],
    )


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
