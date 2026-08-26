"""The decisions a run makes, exercised without performing the run."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from job_scraper.application.run_plan import (
    DEFAULT_SINK_IDS,
    acquisition_produced_nothing,
    build_daily_export_path,
    effective_post_age_hours,
    resolve_export_destination,
    resolve_profile_id,
    run_exit_code,
    select_sink_ids,
)

STARTED_AT = datetime(2026, 3, 15, 23, 30, tzinfo=UTC)


class TestSinkSelection:
    def test_the_profile_decides_the_sinks_and_their_order(self) -> None:
        assert select_sink_ids(
            ("notion_daily", "csv"),
            skip_export=False,
            skip_notion=False,
            has_export_destination=True,
        ) == ("notion_daily", "csv")

    def test_skip_export_removes_only_the_csv_sink(self) -> None:
        assert select_sink_ids(
            DEFAULT_SINK_IDS,
            skip_export=True,
            skip_notion=False,
            has_export_destination=False,
        ) == ("notion_daily",)

    def test_skip_notion_removes_only_the_notion_sink(self) -> None:
        assert select_sink_ids(
            DEFAULT_SINK_IDS,
            skip_export=False,
            skip_notion=True,
            has_export_destination=True,
        ) == ("csv",)

    def test_csv_is_dropped_when_there_is_nowhere_to_write_it(self) -> None:
        """The sink cannot be constructed without a destination."""
        assert select_sink_ids(
            DEFAULT_SINK_IDS,
            skip_export=False,
            skip_notion=False,
            has_export_destination=False,
        ) == ("notion_daily",)

    def test_skipping_everything_leaves_no_sinks_rather_than_failing(self) -> None:
        assert (
            select_sink_ids(
                DEFAULT_SINK_IDS,
                skip_export=True,
                skip_notion=True,
                has_export_destination=False,
            )
            == ()
        )

    def test_a_sink_the_flags_do_not_know_about_is_left_alone(self) -> None:
        """Flags remove specific sinks; they must not filter by allowlist."""
        assert select_sink_ids(
            ("csv", "future_sink"),
            skip_export=True,
            skip_notion=True,
            has_export_destination=False,
        ) == ("future_sink",)


class TestExportDestination:
    def test_the_daily_path_uses_the_configured_local_date(self) -> None:
        """23:30 UTC is already the next day in Berlin; the file follows local time."""
        path = build_daily_export_path(
            export_dir=Path("exports"),
            timezone_name="Europe/Berlin",
            started_at=STARTED_AT,
            file_prefix="jobs_ai",
        )

        assert path == Path("exports/jobs_ai_2026-03-16.csv")

    def test_the_same_instant_in_utc_belongs_to_the_previous_day(self) -> None:
        path = build_daily_export_path(
            export_dir=Path("exports"),
            timezone_name="UTC",
            started_at=STARTED_AT,
            file_prefix="jobs_ai",
        )

        assert path == Path("exports/jobs_ai_2026-03-15.csv")

    def test_a_prefix_with_spaces_does_not_produce_a_spaced_filename(self) -> None:
        path = build_daily_export_path(
            export_dir=Path("exports"),
            timezone_name="UTC",
            started_at=STARTED_AT,
            file_prefix="  core   c++  ",
        )

        assert path.name == "core_c++_2026-03-15.csv"

    def test_an_empty_prefix_falls_back_to_a_usable_name(self) -> None:
        path = build_daily_export_path(
            export_dir=Path("exports"),
            timezone_name="UTC",
            started_at=STARTED_AT,
            file_prefix="",
        )

        assert path.name == "jobs_2026-03-15.csv"

    def test_skipping_the_export_yields_no_destination(self) -> None:
        assert (
            resolve_export_destination(
                explicit_destination=None,
                skip_export=True,
                timezone_name="UTC",
                export_dir=Path("exports"),
                started_at=STARTED_AT,
                file_prefix="jobs",
            )
            is None
        )

    def test_an_explicit_destination_wins_over_the_dated_default(self) -> None:
        assert resolve_export_destination(
            explicit_destination="/tmp/custom.csv",
            skip_export=False,
            timezone_name="UTC",
            export_dir=Path("exports"),
            started_at=STARTED_AT,
            file_prefix="jobs",
        ) == Path("/tmp/custom.csv")

    def test_skip_export_beats_an_explicit_destination(self) -> None:
        """--skip-export is an instruction not to write, not a path question."""
        assert (
            resolve_export_destination(
                explicit_destination="/tmp/custom.csv",
                skip_export=True,
                timezone_name="UTC",
                export_dir=Path("exports"),
                started_at=STARTED_AT,
                file_prefix="jobs",
            )
            is None
        )


class TestPostAgeWindow:
    def test_the_configured_window_is_used_by_default(self) -> None:
        assert effective_post_age_hours(24) == 24

    def test_an_override_is_expressed_in_days(self) -> None:
        assert effective_post_age_hours(24, override_days=3) == 72

    def test_ignoring_post_age_disables_the_window(self) -> None:
        assert effective_post_age_hours(24, ignore_post_age=True) == 0

    def test_ignoring_post_age_beats_an_override(self) -> None:
        assert effective_post_age_hours(24, override_days=7, ignore_post_age=True) == 0


class TestProfileIdentity:
    def test_a_profile_uses_its_own_id(self) -> None:
        assert resolve_profile_id("ai", "AI") == "ai"

    def test_a_run_without_a_profile_falls_back_to_the_track_label(self) -> None:
        assert resolve_profile_id(None, "Core C++") == "Core C++"

    def test_an_empty_profile_id_is_treated_as_absent(self) -> None:
        assert resolve_profile_id("", "Core C++") == "Core C++"


class TestExitCode:
    @pytest.mark.parametrize(
        "collection_failed,publish_had_errors,expected",
        [
            (False, False, 0),
            (True, False, 1),
            (False, True, 1),
            (True, True, 1),
        ],
    )
    def test_any_failure_degrades_the_exit_code(
        self, collection_failed: bool, publish_had_errors: bool, expected: int
    ) -> None:
        assert (
            run_exit_code(
                collection_failed=collection_failed,
                publish_had_errors=publish_had_errors,
                accepted_count=5,
            )
            == expected
        )

    def test_a_clean_run_with_no_jobs_is_still_a_success(self) -> None:
        """An empty day is not an error; nothing failed."""
        assert (
            run_exit_code(collection_failed=False, publish_had_errors=False, accepted_count=0) == 0
        )

    def test_a_partial_source_failure_is_not_a_total_failure(self) -> None:
        """Other sources' jobs still reached the sinks, so the run degraded."""
        assert not acquisition_produced_nothing(collection_failed=True, accepted_count=12)

    def test_every_source_failing_with_nothing_accepted_is_a_total_failure(self) -> None:
        assert acquisition_produced_nothing(collection_failed=True, accepted_count=0)

    def test_no_jobs_without_a_failure_is_not_a_total_failure(self) -> None:
        assert not acquisition_produced_nothing(collection_failed=False, accepted_count=0)
