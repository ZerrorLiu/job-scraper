from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from job_scraper.adapters.storage.sqlite_v2 import WorkspaceDatabase, _job_from_v1_row
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
from job_scraper.domain.models import JobRecord, RawJobRecord
from job_scraper.integrations import email_recommendations
from job_scraper.jobs.run_all_tracks import (
    configured_email_lookback_days,
    parse_args,
    resolve_config_paths,
)
from job_scraper.registry.builtins import create_builtin_registry
from job_scraper.storage.db import Database


def test_public_registry_exposes_only_supported_production_inputs() -> None:
    registry = create_builtin_registry()

    assert registry.sources.available() == ("indeed_brightdata", "linkedin_direct")
    assert registry.channels.available() == ("email_imap",)


def test_cli_does_not_expose_automatic_application_delivery() -> None:
    parser = cli.build_parser()
    subparsers = next(
        action for action in parser._actions if isinstance(action, argparse._SubParsersAction)
    )

    assert "apply" not in subparsers.choices


def test_workspace_schema_omits_automatic_application_tables(tmp_path: Path) -> None:
    database = WorkspaceDatabase(tmp_path / "workspace.db")
    database.initialize()

    with database.connect() as connection:
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }

    assert {"source_postings", "profile_matches", "applications"} <= tables
    assert "application_attempts" not in tables
    assert "application_status_publications" not in tables


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


def test_linkedin_detail_keeps_platform_url_without_application_destination() -> None:
    collector = LinkedInCollector(
        HttpConfig("test-agent", 1, 0, 0, 0),
        SourceConfig(enabled=True, max_listing_pages=1, max_detail_fetches=1),
    )
    seed = RawJobRecord(
        source="linkedin",
        source_job_id="fictional-1",
        source_url="https://www.linkedin.com/jobs/view/fictional-1",
        canonical_url="https://www.linkedin.com/jobs/view/fictional-1",
        title="Fictional Engineer",
        company_name="Example GmbH",
        location_raw="Berlin",
        posted_at_text="",
        scraped_at=datetime.now(UTC),
    )
    html = """
    <script type="application/ld+json">
    {
      "@type": "JobPosting",
      "description": "A complete fictional description.",
      "directApply": true,
      "applicationContact": {"url": "https://careers.example.test/fictional-1"}
    }
    </script>
    """

    detail = collector.parse_detail(html, seed)

    assert detail.source_url == "https://www.linkedin.com/jobs/view/fictional-1"
    assert detail.job_description == "A complete fictional description."
    assert not hasattr(detail, "application_url")
    assert "applicationContact" not in detail.raw_payload


def test_email_detail_enrichment_never_replaces_the_source_platform_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = email_recommendations.EmailJobCandidate(
        url="https://de.indeed.com/viewjob?jk=fictional-2",
        title="Fictional Engineer",
        company_name="Example GmbH",
        location_raw="Berlin",
        context="Fictional Engineer Example GmbH Berlin",
        message_id="fictional@example.test",
        email_subject="Fictional jobs",
        email_from="jobs@example.test",
        email_date=datetime(2026, 1, 1, tzinfo=UTC),
    )
    monkeypatch.setattr(
        email_recommendations,
        "fetch_job_detail",
        lambda *_args: email_recommendations.JobDetail(
            description="A complete fictional job description. " * 10
        ),
    )

    raw = email_recommendations.enrich_email_candidate_to_raw_job(
        candidate,
        HttpConfig("test-agent", 1, 0, 0, 0),
    )

    assert raw.canonical_url == candidate.url
    assert "detail_final_url" not in raw.raw_payload


def test_legacy_company_link_columns_are_not_exported_to_csv_reader(tmp_path: Path) -> None:
    database = Database(tmp_path / "legacy.db")
    database.initialize()
    observed_at = datetime(2026, 1, 1, tzinfo=UTC)
    job = JobRecord(
        source="indeed",
        source_job_id="fictional-3",
        source_url="https://de.indeed.com/viewjob?jk=fictional-3",
        canonical_url="https://de.indeed.com/viewjob?jk=fictional-3",
        title="Fictional Engineer",
        company_name="Example GmbH",
        location_raw="Berlin",
        country="DE",
        city="Berlin",
        region="Berlin",
        remote_type="onsite",
        employment_type="full-time",
        seniority="unknown",
        posted_at=observed_at,
        first_seen_at=observed_at,
        scraped_at=observed_at,
        job_description="A complete fictional job description.",
        description_language="English",
        english_ratio=1.0,
        keyword_hits=[],
        tech_stack=[],
        salary_text="",
        salary_min=None,
        salary_max=None,
        salary_currency=None,
        dedupe_key="fictional-3",
    )
    job_id, _is_new = database.upsert_job(job, "fictional-run")
    with database.connect() as connection:
        connection.execute(
            """
            UPDATE jobs
            SET apply_url = ?, company_url = ?, raw_payload_json = ?
            WHERE id = ?
            """,
            (
                "https://ats.example.test/fictional-3",
                "https://example.test",
                json.dumps(
                    {
                        "description": "Preserved fictional detail",
                        "external_application_url": "https://ats.example.test/fictional-3",
                        "hiringOrganization": {
                            "name": "Example GmbH",
                            "sameAs": "https://example.test",
                        },
                    }
                ),
                job_id,
            ),
        )
        legacy_row = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        assert legacy_row is not None

    row = database.export_jobs()[0]
    migrated = _job_from_v1_row(legacy_row)
    exported_payload = json.loads(str(row["raw_payload_json"]))

    assert "apply_url" not in row
    assert "company_url" not in row
    assert "ats.example.test" not in str(dict(row))
    assert exported_payload == {
        "description": "Preserved fictional detail",
        "hiringOrganization": {"name": "Example GmbH"},
    }
    assert migrated.raw_payload == exported_payload


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
    assert cli.main(["db", "init"]) == 0
    assert cli.main(["db", "init", "--profile", "profile_one"]) == 0
    assert (config_root / "data" / "profile_one.db").is_file()
    assert (config_root / "data" / "profile_two.db").is_file()

    run_arguments: list[str] = []
    monkeypatch.setattr(
        cli.run_all_tracks,
        "main",
        lambda arguments: run_arguments.extend(arguments) or 0,
    )
    assert cli.main(["run"]) == 0
    assert "--configs" in run_arguments
    assert str(result.runtime_path) in run_arguments
    assert "--enable-indeed" not in run_arguments

    run_arguments.clear()
    assert cli.main(["run", "--profile", "profile_one", "--skip-email"]) == 0
    assert "--config" in run_arguments
    assert "--configs" not in run_arguments
    assert "--skip-email" in run_arguments


def test_email_lookback_uses_widest_configured_freshness(
    monkeypatch,
    tmp_path: Path,
) -> None:
    class Project:
        recent_post_age_hours = 25
        bootstrap_post_age_hours = 48

    class Config:
        project = Project()

    monkeypatch.setattr(
        "job_scraper.jobs.run_all_tracks.load_config",
        lambda _path: Config(),
    )

    assert configured_email_lookback_days([tmp_path / "runtime.toml"]) == 2
    assert configured_email_lookback_days([]) == 1


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
