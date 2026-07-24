from __future__ import annotations

from pathlib import Path

import pytest

from job_scraper.application.search_planner import build_search_plan
from job_scraper.cli import main as cli
from job_scraper.cli.bootstrap import BootstrapRequest, initialize_profile
from job_scraper.collectors.linkedin import LinkedInCollector
from job_scraper.config import (
    HttpConfig,
    SourceConfig,
    environment_credential,
    load_source_config,
    validate_source_config,
)
from job_scraper.configuration import (
    available_profiles,
    get_config_root,
    load_profile_definition,
)
from job_scraper.configuration.models import ProfileDefinition
from job_scraper.jobs.run_all_tracks import parse_args, resolve_config_paths
from job_scraper.registry.builtins import create_builtin_registry


def test_public_registry_exposes_only_supported_production_inputs() -> None:
    registry = create_builtin_registry()

    assert registry.sources.available() == ("indeed_brightdata", "linkedin_direct")
    assert registry.channels.available() == ("email_imap",)


def test_linkedin_has_no_builtin_queries_or_locations() -> None:
    settings = SourceConfig(
        enabled=False,
        max_listing_pages=0,
        max_detail_fetches=0,
    )
    collector = LinkedInCollector(
        HttpConfig(
            user_agent="test-agent",
            timeout_seconds=1,
            base_delay_seconds=0,
            jitter_seconds=0,
            max_retries=0,
        ),
        settings,
    )

    assert collector.search_queries == []
    assert collector.locations == []


def test_enabled_source_requires_explicit_queries() -> None:
    settings = SourceConfig(
        enabled=True,
        max_listing_pages=1,
        max_detail_fetches=1,
        locations=["Region One"],
    )

    with pytest.raises(ValueError, match="search_queries"):
        validate_source_config("example", settings)


def test_source_specific_settings_do_not_require_core_model_changes() -> None:
    settings = load_source_config(
        {
            "example_source": {
                "enabled": True,
                "max_listing_pages": 1,
                "max_detail_fetches": 1,
                "search_queries": ["Role One"],
                "locations": ["Region One"],
                "vendor_dataset": "dataset-name",
            }
        },
        "example_source",
    )

    assert settings.options == {"vendor_dataset": "dataset-name"}


def test_search_plan_uses_only_runtime_profile_values(tmp_path: Path) -> None:
    profile = ProfileDefinition(
        profile_id="profile_one",
        label="Profile One",
        runtime_config=tmp_path / "runtime.toml",
        enabled=True,
        sources=("linkedin_direct",),
        channels=(),
        pipeline=("role",),
        sinks=("csv",),
        base_queries=("Role One", "Role Two"),
        locations=("Region One",),
        early_career_modifiers=(),
        watchlists=(),
    )

    assert build_search_plan([profile]).queries == ("Role One", "Role Two")


def test_config_root_can_live_outside_repository(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("JOB_SCRAPER_CONFIG_DIR", str(tmp_path))

    assert get_config_root() == tmp_path.resolve()


def test_profile_discovery_is_dynamic(tmp_path: Path) -> None:
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    runtime = tmp_path / "runtime.toml"
    runtime.touch()
    (profiles / "profile_one.toml").write_text(
        """
[profile]
id = "profile_one"
label = "Profile One"
runtime_config = "../runtime.toml"
enabled = true
sources = ["linkedin_direct"]
channels = []
pipeline = ["role"]
sinks = ["csv"]
base_queries = ["Role One"]
locations = ["Region One"]
early_career_modifiers = []
watchlists = []
""".strip(),
        encoding="utf-8",
    )

    assert available_profiles(tmp_path) == ("profile_one",)
    profile = load_profile_definition("profile_one", config_root=tmp_path)
    assert profile.runtime_config == runtime.resolve()

    args = parse_args(["--config-dir", str(tmp_path), "--skip-email"])
    assert resolve_config_paths(args) == [runtime.resolve()]


def test_placeholder_credentials_are_not_treated_as_secrets(monkeypatch) -> None:
    monkeypatch.setenv("EXAMPLE_TOKEN", "YOUR_ACTUAL_TOKEN")

    assert environment_credential("EXAMPLE_TOKEN") == ""


def test_agent_can_generate_validate_and_initialize_a_new_workspace(
    monkeypatch,
    tmp_path: Path,
) -> None:
    config_root = tmp_path / "private-workspace"
    result = initialize_profile(
        BootstrapRequest(
            config_root=config_root,
            profile_id="profile_one",
            label="Profile One",
            queries=("Role One",),
            locations=("Region One",),
            countries=("US",),
            keywords=("signal one",),
            sources=("linkedin_direct", "indeed_brightdata"),
            sinks=("csv",),
        )
    )

    assert result.profile_path.is_file()
    assert result.runtime_path.is_file()
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
    monkeypatch.setenv("JOB_SCRAPER_CONFIG_DIR", str(config_root))
    assert cli.main(["config", "validate", "--all"]) == 0
    assert cli.main(["doctor", "--all"]) == 0
    assert cli.main(["run", "--all", "--init-db"]) == 0
    assert (config_root / "data" / "profile_one.db").is_file()
    assert (config_root / "data" / "profile_two.db").is_file()


def test_agent_bootstrap_refuses_to_overwrite_private_configuration(tmp_path: Path) -> None:
    request = BootstrapRequest(
        config_root=tmp_path,
        profile_id="profile_one",
        label="Profile One",
        queries=("Role One",),
        locations=("Region One",),
        countries=("US",),
        keywords=("signal one",),
        sources=("linkedin_direct",),
    )
    initialize_profile(request)

    with pytest.raises(FileExistsError, match="Refusing to overwrite"):
        initialize_profile(request)
