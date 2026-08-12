from __future__ import annotations

from pathlib import Path

import pytest

from job_scraper.cli import main as cli
from job_scraper.cli.bootstrap import BootstrapRequest, initialize_profile
from job_scraper.cli.database import migrate_profiles, resolve_status_workspace_path
from job_scraper.config import load_source_config
from job_scraper.configuration.loader import load_profile_definition


def _bootstrap_two_profiles(config_root: Path) -> None:
    initialize_profile(
        BootstrapRequest(
            config_root=config_root,
            profile_id="profile_one",
            label="Profile One",
            queries=("Role One",),
            locations=("Region One",),
            countries=("US",),
            keywords=("signal one",),
            sources=("linkedin_direct",),
            sinks=("csv",),
        )
    )
    initialize_profile(
        BootstrapRequest(
            config_root=config_root,
            profile_id="profile_two",
            label="Profile Two",
            queries=("Role Two",),
            locations=("Region Two",),
            countries=("CA",),
            keywords=("signal two",),
            sources=("linkedin_direct",),
            sinks=("csv",),
        )
    )


def test_doctor_with_no_flags_checks_every_local_profile(monkeypatch, tmp_path, capsys) -> None:
    config_root = tmp_path / "private-workspace"
    _bootstrap_two_profiles(config_root)
    monkeypatch.setenv("JOB_SCRAPER_CONFIG_DIR", str(config_root))

    assert cli.main(["doctor"]) == 0

    output = capsys.readouterr().out
    assert "[profile_one]" in output
    assert "[profile_two]" in output


def test_config_validate_with_no_flags_checks_every_local_profile(
    monkeypatch, tmp_path, capsys
) -> None:
    config_root = tmp_path / "private-workspace"
    _bootstrap_two_profiles(config_root)
    monkeypatch.setenv("JOB_SCRAPER_CONFIG_DIR", str(config_root))

    assert cli.main(["config", "validate"]) == 0

    output = capsys.readouterr().out
    assert "profile_one" in output
    assert "profile_two" in output


def test_load_profile_definition_rejects_path_traversal(tmp_path: Path) -> None:
    config_root = tmp_path / "private-workspace"
    _bootstrap_two_profiles(config_root)

    with pytest.raises(ValueError, match="letters, digits, or underscores"):
        load_profile_definition("../../pyproject", config_root=config_root)


def test_load_source_config_rejects_a_likely_typo_of_a_known_field() -> None:
    with pytest.raises(ValueError, match="did you mean 'max_detail_fetches'"):
        load_source_config(
            {"example_source": {"enabled": True, "max_detial_fetches": 80}},
            "example_source",
        )


def test_load_source_config_still_preserves_genuine_adapter_specific_keys() -> None:
    settings = load_source_config(
        {"example_source": {"enabled": True, "vendor_dataset": "dataset-name"}},
        "example_source",
    )

    assert settings.options == {"vendor_dataset": "dataset-name"}


def test_migrate_refuses_when_destination_matches_a_source_database(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    config_root = tmp_path / "private-workspace"
    _bootstrap_two_profiles(config_root)
    monkeypatch.setenv("JOB_SCRAPER_CONFIG_DIR", str(config_root))

    source_db = config_root / "data" / "profile_one.db"

    status = migrate_profiles(["profile_one"], workspace_path=source_db)

    assert status == 2
    assert "same file as the source database" in capsys.readouterr().out


def test_resolve_status_workspace_path_prefers_profile_configured_path(
    tmp_path: Path, monkeypatch
) -> None:
    # A freshly bootstrapped profile's runtime config already points
    # workspace_database_path at config_root/data/workspace.db (see
    # cli/bootstrap.py). `db status` with no --workspace and no CWD
    # dependency should resolve to that same path, matching what
    # `db init`/`db migrate` would use for this profile.
    config_root = tmp_path / "private-workspace"
    _bootstrap_two_profiles(config_root)
    monkeypatch.setenv("JOB_SCRAPER_CONFIG_DIR", str(config_root))

    resolved = resolve_status_workspace_path(["profile_one"], None)

    assert resolved == (config_root / "data" / "workspace.db").resolve()
