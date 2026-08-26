"""The cumulative CSV series must stay bounded and must only touch its own files."""

from __future__ import annotations

from pathlib import Path

from job_scraper.adapters.sinks.csv import CsvSink
from job_scraper.domain.policies import FilterPolicy


class _Reader:
    def export_jobs(self, languages: list[str] | None = None) -> list[dict[str, object]]:
        del languages
        return [
            {
                "normalized_title": "Software Engineer",
                "company_name": "Fictional GmbH",
                "location_text": "Berlin, Germany",
                "country_code": "DE",
                "description_full": "",
                "description_language": "English",
                "english_ratio": 1.0,
                "employment_type": "full-time",
                "raw_payload_json": "{}",
            }
        ]


def _publish(destination: Path, retained: int) -> None:
    CsvSink(
        _Reader(),
        destination,
        FilterPolicy(countries=("DE",), require_english=False),
        retained_exports=retained,
    ).publish((), None)  # type: ignore[arg-type]


def test_old_exports_in_the_same_series_are_pruned(tmp_path: Path) -> None:
    for day in range(1, 21):
        (tmp_path / f"jobs_ai_2026-03-{day:02d}.csv").write_text("stale", encoding="utf-8")

    _publish(tmp_path / "jobs_ai_2026-04-01.csv", retained=5)

    remaining = sorted(path.name for path in tmp_path.glob("jobs_ai_*.csv"))
    assert remaining == [
        "jobs_ai_2026-03-17.csv",
        "jobs_ai_2026-03-18.csv",
        "jobs_ai_2026-03-19.csv",
        "jobs_ai_2026-03-20.csv",
        "jobs_ai_2026-04-01.csv",
    ]


def test_pruning_never_reaches_another_series_or_an_unrelated_file(tmp_path: Path) -> None:
    (tmp_path / "jobs_cpp_2026-03-01.csv").write_text("other track", encoding="utf-8")
    (tmp_path / "jobs_ai_notes.csv").write_text("not a dated export", encoding="utf-8")
    (tmp_path / "analysis.csv").write_text("hand written", encoding="utf-8")
    for day in range(1, 4):
        (tmp_path / f"jobs_ai_2026-03-{day:02d}.csv").write_text("stale", encoding="utf-8")

    _publish(tmp_path / "jobs_ai_2026-04-01.csv", retained=1)

    assert (tmp_path / "jobs_cpp_2026-03-01.csv").exists()
    assert (tmp_path / "jobs_ai_notes.csv").exists()
    assert (tmp_path / "analysis.csv").exists()
    assert sorted(p.name for p in tmp_path.glob("jobs_ai_2026-*.csv")) == ["jobs_ai_2026-04-01.csv"]


def test_retention_can_be_disabled(tmp_path: Path) -> None:
    for day in range(1, 6):
        (tmp_path / f"jobs_ai_2026-03-{day:02d}.csv").write_text("stale", encoding="utf-8")

    _publish(tmp_path / "jobs_ai_2026-04-01.csv", retained=0)

    assert len(list(tmp_path.glob("jobs_ai_*.csv"))) == 6


def test_retention_is_disabled_by_default(tmp_path: Path) -> None:
    """Enabling retention deletes files an operator already has, so it is opt-in."""
    from job_scraper.adapters.sinks.csv import DEFAULT_RETAINED_EXPORTS

    assert DEFAULT_RETAINED_EXPORTS == 0

    for day in range(1, 6):
        (tmp_path / f"jobs_ai_2026-03-{day:02d}.csv").write_text("stale", encoding="utf-8")
    CsvSink(
        _Reader(),
        tmp_path / "jobs_ai_2026-04-01.csv",
        FilterPolicy(countries=("DE",), require_english=False),
    ).publish((), None)  # type: ignore[arg-type]

    assert len(list(tmp_path.glob("jobs_ai_*.csv"))) == 6
