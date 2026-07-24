from __future__ import annotations

import sqlite3
from pathlib import Path

from job_scraper.adapters.storage.sqlite_v2 import WorkspaceDatabase
from job_scraper.config import AppConfig, load_config
from job_scraper.configuration import available_profiles, load_profile_definition
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

    for definition in definitions:
        config = load_config(definition.runtime_config)
        Database(config.project.database_path).initialize()
        print(f"INITIALIZED {definition.profile_id}: {config.project.database_path}")
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


def show_status(workspace_path: Path) -> int:
    if not workspace_path.is_file():
        print(f"Workspace database does not exist: {workspace_path}")
        return 1
    workspace = WorkspaceDatabase(workspace_path)
    workspace.initialize()
    for name, value in workspace.counts().items():
        print(f"{name:24} {value}")
    return 0


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
