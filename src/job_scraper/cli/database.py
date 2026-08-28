from __future__ import annotations

import sqlite3
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

from job_scraper.adapters.sinks.notion_workflow import publish_daily
from job_scraper.adapters.storage.notion_bindings import NotionDatabaseBindingStore
from job_scraper.adapters.storage.sqlite_v2 import WorkspaceDatabase
from job_scraper.application.screening_results import load_screening_results
from job_scraper.config import AppConfig, load_config
from job_scraper.configuration import available_profiles, load_profile_definition
from job_scraper.domain.decisions import ScreeningResult
from job_scraper.integrations.notion import NotionClient
from job_scraper.storage.db import Database


def initialize_profiles(profile_id: str | None = None) -> int:
    """Initialize operational databases without running acquisition."""
    if profile_id:
        definitions = [load_profile_definition(profile_id)]
    else:
        definitions = [
            definition
            for candidate in available_profiles()
            if (definition := load_profile_definition(candidate)).enabled
        ]
    if not definitions:
        print("No enabled profiles found.")
        return 2

    configs = [load_config(definition.runtime_config) for definition in definitions]
    for definition, config in zip(definitions, configs, strict=True):
        Database(config.project.database_path).initialize()
        print(f"INITIALIZED {definition.profile_id}: {config.project.database_path}")
    workspace_path = _configured_workspace_path(configs)
    if workspace_path is not None:
        WorkspaceDatabase(workspace_path).initialize()
        print(f"INITIALIZED workspace: {workspace_path}")
    return 0


def migrate_profiles(
    profile_ids: list[str] | None,
    *,
    workspace_path: Path | None = None,
    dry_run: bool = False,
) -> int:
    selected = profile_ids or list(available_profiles())
    if not selected:
        print("No profiles found.")
        return 2

    definitions = [load_profile_definition(profile_id) for profile_id in selected]
    configs = [load_config(definition.runtime_config) for definition in definitions]
    destination = workspace_path or _configured_workspace_path(configs)
    if destination is None:
        print("No workspace database path is configured.")
        return 2

    resolved_destination = destination.resolve()
    overlapping = [
        definition.profile_id
        for definition, config in zip(definitions, configs, strict=True)
        if config.project.database_path.resolve() == resolved_destination
    ]
    if overlapping:
        print(
            "ERROR --workspace resolves to the same file as the source database for: "
            f"{', '.join(overlapping)} ({resolved_destination}). Migrations must never "
            "write to their own source database. Pass a different --workspace path "
            "(for example, one under the config's data/ directory that is not any "
            "profile's own database_path).",
        )
        return 2

    if dry_run:
        total = 0
        for definition, config in zip(definitions, configs, strict=True):
            source_path = config.project.database_path
            count = _v1_job_count(source_path)
            total += count
            print(f"DRY-RUN {definition.profile_id}: {count} jobs from {source_path}")
        print(f"DRY-RUN total: {total} profile rows -> {destination}")
        return 0

    workspace = WorkspaceDatabase(destination)
    workspace.initialize()
    for definition, config in zip(definitions, configs, strict=True):
        source_path = config.project.database_path
        report = workspace.migrate_v1(
            definition.profile_id,
            source_path,
        )
        print(
            f"MIGRATED {report.profile_id}: source={source_path.name}, "
            f"jobs={report.jobs_linked}, "
            f"applications={report.applications_migrated}, "
            f"publications={report.publications_migrated}"
        )
    counts = workspace.counts()
    print("WORKSPACE " + ", ".join(f"{name}={value}" for name, value in counts.items()))
    return 0


def resolve_status_workspace_path(
    profile_ids: list[str] | None,
    workspace_path: Path | None,
) -> Path:
    """Resolve the workspace path `db status` should inspect.

    Mirrors `db init`/`db migrate`: prefer the path configured by the
    selected (or, by default, all local) profiles' runtime_config over a
    CWD-relative literal default, so `status` reports on the same workspace
    `init`/`migrate` just acted on regardless of the current directory or a
    non-default JOB_SCRAPER_CONFIG_DIR.
    """
    if workspace_path is not None:
        return workspace_path.resolve()
    selected = profile_ids or list(available_profiles())
    if selected:
        definitions = [load_profile_definition(profile_id) for profile_id in selected]
        configs = [load_config(definition.runtime_config) for definition in definitions]
        configured = _configured_workspace_path(configs)
        if configured is not None:
            return configured.resolve()
    return Path("data/workspace.db").resolve()


def show_status(workspace_path: Path, profile_ids: list[str] | None = None) -> int:
    """Report on a workspace without modifying it.

    `status` used to call `initialize()`, which creates tables and applies
    migrations -- a write, from a command whose whole purpose is to look.
    """
    workspace_exists = workspace_path.is_file()
    if not workspace_exists:
        print(f"Workspace database does not exist: {workspace_path}")
    else:
        print(f"Workspace: {workspace_path}")
        workspace = WorkspaceDatabase(workspace_path)
        for name, value in workspace.counts().items():
            print(f"{name:24} {value}")
        pending = workspace.pending_migrations()
        if pending:
            versions = ", ".join(str(migration.version) for migration in pending)
            print(f"{'pending migrations':24} {versions} (run `job-scraper db init`)")
        unknown = workspace.unknown_tables()
        if unknown:
            print(f"{'unrecognized tables':24} {', '.join(unknown)}")
        for table, watermark in workspace.frozen_table_watermarks().items():
            print(f"{'frozen since':24} {table}: {watermark[:19]} (migration snapshot, not live)")
    selected = profile_ids or list(available_profiles())
    for profile_id in selected:
        definition = load_profile_definition(profile_id)
        config = load_config(definition.runtime_config)
        binding = NotionDatabaseBindingStore(
            config.project.database_path.parent / "notion_database_bindings.json"
        ).load(definition.profile_id)
        if binding is None:
            print(f"notion binding {definition.profile_id}: unbound")
        else:
            print(
                f"notion binding {definition.profile_id}: "
                f"database_id={binding.database_id}, data_source_id={binding.data_source_id}"
            )
    return 0 if workspace_exists else 1


def import_screening_results(result_paths: list[Path], workspace_path: Path) -> int:
    """Persist validated screener output; repeated imports are idempotent."""

    if not workspace_path.is_file():
        print(f"Workspace database does not exist: {workspace_path}")
        return 2
    workspace = WorkspaceDatabase(workspace_path)
    pending = workspace.pending_migrations()
    if pending:
        versions = ", ".join(str(migration.version) for migration in pending)
        print(f"Workspace has pending migrations: {versions}; run `job-scraper db init`")
        return 2
    try:
        results = _validated_screening_results(result_paths)
        workspace.record_screening_results(results)
    except (OSError, ValueError) as exc:
        print(f"ERROR screening result import failed: {exc}")
        return 2
    print(f"IMPORTED screening results: {len(results)} record(s) -> {workspace_path}")
    return 0


def publish_screening_results(result_paths: list[Path], *, expected_count: int | None) -> int:
    """Publish jobs only after their validated screening handoff exists."""

    try:
        results = _validated_screening_results(result_paths)
        if expected_count is not None and len(results) != expected_count:
            raise ValueError(f"screening result count is {len(results)}; expected {expected_count}")
        definitions = {
            profile_id: load_profile_definition(profile_id)
            for profile_id in sorted({result.profile_id for result in results})
        }
        configs = {
            profile_id: load_config(definition.runtime_config)
            for profile_id, definition in definitions.items()
        }
        workspace_path = _configured_workspace_path(list(configs.values()))
        if workspace_path is None or not workspace_path.is_file():
            raise ValueError("configured durable workspace database does not exist")
        workspace = WorkspaceDatabase(workspace_path)
        if workspace.pending_migrations():
            raise ValueError("durable workspace has pending migrations")
        workspace.verify_screening_results(results)
        by_profile: dict[str, list[ScreeningResult]] = defaultdict(list)
        for screening_result in results:
            by_profile[screening_result.profile_id].append(screening_result)
        published = 0
        for profile_id, profile_results in sorted(by_profile.items()):
            config = configs[profile_id]
            notion = NotionClient(config.notion)
            if not notion.enabled():
                raise ValueError(f"profile {profile_id} has no enabled Notion sink")
            database = Database(config.project.database_path)
            job_ids = {result.legacy_job_id for result in profile_results}
            jobs = database.read_accepted_jobs(job_ids)
            missing = sorted(job_ids - {accepted.job_id for accepted in jobs})
            if missing:
                raise ValueError(
                    f"profile {profile_id} is missing {len(missing)} finalized source jobs"
                )
            publish_result = publish_daily(
                database,
                jobs,
                notion,
                config.project.timezone,
                datetime.now(UTC),
                config.notion.daily_table_prefix,
                config.project.track_label,
                profile_id,
                NotionDatabaseBindingStore(
                    config.project.database_path.parent / "notion_database_bindings.json"
                ),
            )
            if publish_result.errors:
                raise RuntimeError("; ".join(publish_result.errors))
            published += publish_result.published
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"ERROR finalized screening publication failed: {exc}")
        return 2
    print(f"PUBLISHED finalized screening jobs: {published} -> Notion")
    return 0


def _validated_screening_results(result_paths: list[Path]) -> tuple[ScreeningResult, ...]:
    results = tuple(
        result for result_path in result_paths for result in load_screening_results(result_path)
    )
    profile_modes: dict[str, str] = {}
    for result in results:
        if result.profile_id not in profile_modes:
            profile_modes[result.profile_id] = load_profile_definition(
                result.profile_id
            ).processing_mode
        expected_mode = profile_modes[result.profile_id]
        if result.processing_mode != expected_mode:
            raise ValueError(
                "screening result processing_mode does not match current profile policy: "
                f"{result.profile_id} is {expected_mode}, result says {result.processing_mode}"
            )
    return results


def _configured_workspace_path(configs: list[AppConfig]) -> Path | None:
    paths = {
        config.project.workspace_database_path
        for config in configs
        if config.project.workspace_database_path is not None
    }
    if len(paths) > 1:
        rendered = ", ".join(str(path) for path in sorted(paths, key=str))
        raise ValueError(f"Profiles configure different workspace databases: {rendered}")
    return next(iter(paths), None)


def _v1_job_count(path: Path) -> int:
    if not path.is_file():
        return 0
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'jobs'"
        ).fetchone()
        if exists is None:
            return 0
        return int(connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0])
    finally:
        connection.close()
