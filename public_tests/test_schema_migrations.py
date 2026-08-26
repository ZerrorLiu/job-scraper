"""Schema evolution must reach databases that already exist."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from job_scraper.adapters.storage.sqlite_v2 import (
    BASE_SCHEMA_VERSION,
    LATEST_SCHEMA_VERSION,
    MIGRATIONS,
    WorkspaceDatabase,
)
from job_scraper.storage.db import Database


def _indexes(path: Path, table: str) -> set[str]:
    connection = sqlite3.connect(path)
    try:
        return {
            str(row[1])
            for row in connection.execute(f"PRAGMA index_list('{table}')")
            if not str(row[1]).startswith("sqlite_autoindex")
        }
    finally:
        connection.close()


def test_migrations_are_ordered_and_unique() -> None:
    versions = [migration.version for migration in MIGRATIONS]
    assert versions == sorted(set(versions))
    assert all(version > BASE_SCHEMA_VERSION for version in versions)


def test_a_fresh_workspace_records_every_migration(tmp_path: Path) -> None:
    workspace = WorkspaceDatabase(tmp_path / "workspace.db")
    workspace.initialize()

    assert workspace.pending_migrations() == ()
    connection = sqlite3.connect(tmp_path / "workspace.db")
    try:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == LATEST_SCHEMA_VERSION
    finally:
        connection.close()


def test_a_database_created_before_a_migration_still_receives_it(tmp_path: Path) -> None:
    """The regression this guards: `CREATE TABLE IF NOT EXISTS` alone skips
    existing databases, so schema changes never reached a live workspace."""
    path = tmp_path / "workspace.db"
    workspace = WorkspaceDatabase(path)
    workspace.initialize()

    # Rewind to a pre-migration state, as an older workspace file would be.
    connection = sqlite3.connect(path)
    try:
        for migration in MIGRATIONS:
            connection.execute(
                "DELETE FROM schema_migrations WHERE version = ?", (migration.version,)
            )
            for statement in migration.statements:
                name = statement.split("IF NOT EXISTS")[1].split()[0]
                connection.execute(f"DROP INDEX IF EXISTS {name}")
        connection.commit()
    finally:
        connection.close()

    assert [m.version for m in workspace.pending_migrations()] == [m.version for m in MIGRATIONS]

    workspace.initialize()

    assert workspace.pending_migrations() == ()
    assert "idx_postings_source_lookup" in _indexes(path, "source_postings")


def test_initialize_is_idempotent(tmp_path: Path) -> None:
    workspace = WorkspaceDatabase(tmp_path / "workspace.db")
    workspace.initialize()
    workspace.initialize()
    workspace.initialize()

    connection = sqlite3.connect(tmp_path / "workspace.db")
    try:
        recorded = connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0]
    finally:
        connection.close()
    assert recorded == 1 + len(MIGRATIONS)


def test_tables_this_schema_no_longer_defines_are_reported(tmp_path: Path) -> None:
    """A workspace that outlived a removed feature should say so, not hide it."""
    path = tmp_path / "workspace.db"
    workspace = WorkspaceDatabase(path)
    workspace.initialize()
    connection = sqlite3.connect(path)
    try:
        connection.execute("CREATE TABLE retired_feature (id TEXT PRIMARY KEY)")
        connection.commit()
    finally:
        connection.close()

    assert workspace.unknown_tables() == ("retired_feature",)
    assert "retired_feature (unknown)" in workspace.counts()


def test_the_v1_store_indexes_its_lookup_columns(tmp_path: Path) -> None:
    """Without these, every upsert and history probe scans the whole table."""
    path = tmp_path / "jobs.db"
    Database(path).initialize()

    assert {"idx_jobs_source_url", "idx_jobs_canonical_url"} <= _indexes(path, "jobs")
    assert "idx_observations_job" in _indexes(path, "job_observations")
    assert "idx_notion_state_page" in _indexes(path, "notion_sync_state")


def test_v1_initialize_is_safe_to_re_run(tmp_path: Path) -> None:
    database = Database(tmp_path / "jobs.db")
    database.initialize()
    database.initialize()

    assert "idx_jobs_company_lower" in _indexes(tmp_path / "jobs.db", "jobs")
