from __future__ import annotations

from pathlib import Path

from job_scraper.cli.bootstrap import BootstrapRequest, initialize_profile
from job_scraper.cli.doctor import run_doctor
from job_scraper.config import (
    AppConfig,
    FiltersConfig,
    HttpConfig,
    NotionConfig,
    ProjectConfig,
    SourceConfig,
)
from job_scraper.configuration.brightdata import brightdata_direct_collection_enabled
from job_scraper.jobs import run_brightdata_notion_e2e, run_daily


def test_direct_collection_defaults_to_suspended(monkeypatch) -> None:
    monkeypatch.delenv("BRIGHTDATA_DIRECT_COLLECTION_ENABLED", raising=False)

    assert not brightdata_direct_collection_enabled()

    monkeypatch.setenv("BRIGHTDATA_DIRECT_COLLECTION_ENABLED", "true")
    assert brightdata_direct_collection_enabled()


def test_selected_direct_source_is_disabled_without_the_explicit_opt_in(
    monkeypatch, tmp_path
) -> None:
    config = AppConfig(
        project=ProjectConfig("UTC", tmp_path / "jobs.db", 24, tmp_path, "Fictional"),
        filters=FiltersConfig("US", [], [], [], "title", 0.0),
        http=HttpConfig("fictional", 1, 0, 0, 0),
        sources={"indeed_brightdata": SourceConfig(enabled=True)},
        notion=NotionConfig(enabled=False),
    )
    monkeypatch.delenv("BRIGHTDATA_DIRECT_COLLECTION_ENABLED", raising=False)

    run_daily._disable_brightdata_when_suspended(config)

    assert not config.sources["indeed_brightdata"].enabled


def test_live_e2e_exits_before_loading_config_while_suspended(
    monkeypatch, tmp_path, capsys
) -> None:
    monkeypatch.delenv("BRIGHTDATA_DIRECT_COLLECTION_ENABLED", raising=False)

    assert (
        run_brightdata_notion_e2e.main(
            [
                "--term",
                "Fictional role",
                "--location",
                "Fictional location",
                "--config",
                str(tmp_path / "unused.toml"),
            ]
        )
        == 2
    )

    assert "direct collection is suspended" in capsys.readouterr().err


def test_doctor_reports_profile_source_list_suspension(monkeypatch, tmp_path: Path) -> None:
    config_root = tmp_path / "config"
    initialize_profile(
        BootstrapRequest(
            config_root=config_root,
            profile_id="fictional",
            label="Fictional",
            queries=("Fictional",),
            locations=("Fictional",),
            countries=("US",),
            keywords=("Fictional",),
            sources=("linkedin_direct",),
            sinks=("csv",),
        )
    )

    results = run_doctor("fictional", project_root=tmp_path)

    assert ("brightdata direct", "ok", "disabled by the effective profile source list") in [
        (result.name, result.status, result.message) for result in results
    ]
