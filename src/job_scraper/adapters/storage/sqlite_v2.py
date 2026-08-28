from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from job_scraper.domain.decisions import Decision, ScreeningResult
from job_scraper.domain.identity import (
    canonical_identity,
    canonical_job_id,
    source_posting_id,
    stable_id,
)
from job_scraper.domain.models import JobRecord
from job_scraper.domain.payload_sanitization import sanitize_job_payload

BASE_SCHEMA_VERSION = 5

SCHEMA = """
PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL,
    description TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS canonical_jobs (
    id TEXT PRIMARY KEY,
    identity_key TEXT NOT NULL UNIQUE,
    normalized_title TEXT NOT NULL,
    company_name TEXT NOT NULL,
    country_code TEXT NOT NULL,
    city TEXT NOT NULL,
    location_text TEXT NOT NULL,
    description_full TEXT NOT NULL,
    employment_type TEXT NOT NULL,
    remote_mode TEXT NOT NULL,
    seniority TEXT NOT NULL,
    salary_text TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS source_postings (
    id TEXT PRIMARY KEY,
    canonical_job_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    platform TEXT NOT NULL,
    acquisition_mode TEXT NOT NULL,
    source_job_id TEXT NOT NULL,
    source_url TEXT NOT NULL,
    canonical_url TEXT NOT NULL,
    application_url TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    raw_payload_json TEXT NOT NULL,
    UNIQUE(source_id, source_job_id, canonical_url),
    FOREIGN KEY(canonical_job_id) REFERENCES canonical_jobs(id)
);

CREATE TABLE IF NOT EXISTS source_observations (
    id TEXT PRIMARY KEY,
    posting_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    profile_id TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    raw_payload_json TEXT NOT NULL,
    FOREIGN KEY(posting_id) REFERENCES source_postings(id)
);

CREATE TABLE IF NOT EXISTS profile_matches (
    canonical_job_id TEXT NOT NULL,
    profile_id TEXT NOT NULL,
    accepted INTEGER NOT NULL,
    rejection_reason TEXT NOT NULL,
    decision_step TEXT NOT NULL,
    first_evaluated_at TEXT NOT NULL,
    last_evaluated_at TEXT NOT NULL,
    PRIMARY KEY(canonical_job_id, profile_id),
    FOREIGN KEY(canonical_job_id) REFERENCES canonical_jobs(id)
);

CREATE TABLE IF NOT EXISTS applications (
    canonical_job_id TEXT PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'new',
    applied_at TEXT,
    updated_at TEXT NOT NULL,
    notes TEXT NOT NULL DEFAULT '',
    FOREIGN KEY(canonical_job_id) REFERENCES canonical_jobs(id)
);


-- FROZEN. Only `migrate_v1` writes this table, so it holds publication state
-- as of the last migration and nothing since. Live publication state is the V1
-- store's `notion_sync_state`, which the Notion sink updates every run.
--
-- Reading this table as if it were current is a real trap: it looks exactly
-- like live data, and it silently under-reports any source that started
-- producing after the last migration. `WorkspaceDatabase.counts()` labels it,
-- and `db status` says so. Do not add a write path here until the V1/V2 split
-- is resolved -- a second live writer would make the two stores disagree.
CREATE TABLE IF NOT EXISTS external_publications (
    canonical_job_id TEXT NOT NULL,
    sink_id TEXT NOT NULL,
    external_id TEXT NOT NULL,
    external_container_id TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    status TEXT NOT NULL,
    last_synced_at TEXT NOT NULL,
    PRIMARY KEY(canonical_job_id, sink_id, external_id),
    FOREIGN KEY(canonical_job_id) REFERENCES canonical_jobs(id)
);


CREATE TABLE IF NOT EXISTS legacy_job_links (
    profile_id TEXT NOT NULL,
    legacy_job_id TEXT NOT NULL,
    canonical_job_id TEXT NOT NULL,
    legacy_database_path TEXT NOT NULL,
    PRIMARY KEY(profile_id, legacy_job_id),
    FOREIGN KEY(canonical_job_id) REFERENCES canonical_jobs(id)
);

CREATE INDEX IF NOT EXISTS idx_postings_canonical_job
ON source_postings(canonical_job_id);


CREATE INDEX IF NOT EXISTS idx_observations_run
ON source_observations(run_id, profile_id);

CREATE INDEX IF NOT EXISTS idx_matches_profile
ON profile_matches(profile_id, accepted, last_evaluated_at);
"""


@dataclass(frozen=True, slots=True)
class Migration:
    """One ordered, idempotent schema step recorded in `schema_migrations`."""

    version: int
    description: str
    statements: tuple[str, ...]


# Versions <= BASE_SCHEMA_VERSION describe the shape that `SCHEMA` above already
# creates, so they are only ever *recorded* for a fresh database. Every change
# after that must be appended here as a new version -- `CREATE TABLE IF NOT
# EXISTS` alone silently skips existing databases, so column and index changes
# would never reach a workspace that was created before the change.
MIGRATIONS: tuple[Migration, ...] = (
    Migration(
        version=6,
        description="index source postings for detail lookup and canonical identity",
        statements=(
            "CREATE INDEX IF NOT EXISTS idx_postings_source_lookup "
            "ON source_postings(source_id, source_job_id, last_seen_at)",
            "CREATE INDEX IF NOT EXISTS idx_canonical_identity ON canonical_jobs(identity_key)",
            "CREATE INDEX IF NOT EXISTS idx_publications_external "
            "ON external_publications(sink_id, external_id)",
        ),
    ),
    Migration(
        version=7,
        description="persist validated semantic screening results",
        statements=(
            """CREATE TABLE IF NOT EXISTS screening_results (
                canonical_job_id TEXT NOT NULL,
                profile_id TEXT NOT NULL,
                processing_mode TEXT NOT NULL,
                status TEXT NOT NULL,
                selected INTEGER NOT NULL,
                score REAL,
                core_fit TEXT NOT NULL,
                variant TEXT NOT NULL,
                true_gap_json TEXT NOT NULL,
                rationale TEXT NOT NULL,
                decision_source TEXT NOT NULL,
                tailoring_status TEXT NOT NULL,
                contract_version TEXT NOT NULL,
                evaluated_at TEXT NOT NULL,
                payload_hash TEXT NOT NULL,
                PRIMARY KEY(canonical_job_id, profile_id, contract_version),
                FOREIGN KEY(canonical_job_id) REFERENCES canonical_jobs(id)
            )""",
            "CREATE INDEX IF NOT EXISTS idx_screening_results_profile "
            "ON screening_results(profile_id, processing_mode, status, evaluated_at)",
        ),
    ),
)

LATEST_SCHEMA_VERSION = max(
    (migration.version for migration in MIGRATIONS),
    default=BASE_SCHEMA_VERSION,
)

KNOWN_TABLES = (
    "schema_migrations",
    "canonical_jobs",
    "source_postings",
    "source_observations",
    "profile_matches",
    "applications",
    "external_publications",
    "legacy_job_links",
    "screening_results",
)

# Tables no run writes to. They are populated once by `migrate_v1` and then
# stand still, so any report built from them describes the moment of migration
# rather than the present.
FROZEN_TABLES = frozenset({"external_publications", "legacy_job_links"})


@dataclass(frozen=True, slots=True)
class MigrationReport:
    profile_id: str
    database_path: Path
    jobs_read: int = 0
    jobs_linked: int = 0
    applications_migrated: int = 0
    publications_migrated: int = 0


@dataclass(frozen=True, slots=True)
class StoredJobDetail:
    source_id: str
    source_job_id: str
    source_url: str
    title: str
    company_name: str
    location_text: str
    description: str
    employment_type: str


class WorkspaceDatabase:
    """V2 workspace store shared by all profiles and source channels."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(SCHEMA)
            connection.execute(
                """
                INSERT OR IGNORE INTO schema_migrations(version, applied_at, description)
                VALUES (?, ?, ?)
                """,
                (
                    BASE_SCHEMA_VERSION,
                    datetime.now(UTC).isoformat(),
                    "workspace canonical job schema",
                ),
            )
            self._apply_pending_migrations(connection)
            connection.execute(f"PRAGMA user_version = {LATEST_SCHEMA_VERSION}")

    def pending_migrations(self) -> tuple[Migration, ...]:
        """Migrations this database has not recorded yet (for dry-run reporting)."""
        with self.connect() as connection:
            if not _table_exists(connection, "schema_migrations"):
                return MIGRATIONS
            return self._pending(connection)

    def unknown_tables(self) -> tuple[str, ...]:
        """Tables present in the file that this schema no longer defines.

        Surfacing drift beats silently ignoring it: a workspace that outlived a
        removed feature keeps those tables and their rows, and an operator
        should be able to see that from `db status` rather than by opening the
        file by hand.
        """
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        return tuple(sorted(str(row["name"]) for row in rows if row["name"] not in KNOWN_TABLES))

    @staticmethod
    def _pending(connection: sqlite3.Connection) -> tuple[Migration, ...]:
        applied = {
            int(row["version"])
            for row in connection.execute("SELECT version FROM schema_migrations").fetchall()
        }
        return tuple(migration for migration in MIGRATIONS if migration.version not in applied)

    def _apply_pending_migrations(self, connection: sqlite3.Connection) -> None:
        for migration in self._pending(connection):
            for statement in migration.statements:
                connection.execute(statement)
            connection.execute(
                """
                INSERT OR IGNORE INTO schema_migrations(version, applied_at, description)
                VALUES (?, ?, ?)
                """,
                (migration.version, datetime.now(UTC).isoformat(), migration.description),
            )

    def record_candidate(
        self,
        job: JobRecord,
        decision: Decision,
        *,
        profile_id: str,
        run_id: str,
        evaluated_at: datetime,
        legacy_job_id: str = "",
    ) -> str:
        canonical_id = canonical_job_id(job)
        posting_id = source_posting_id(job)
        observed_at = job.scraped_at.astimezone(UTC).isoformat()
        evaluated = evaluated_at.astimezone(UTC).isoformat()
        platform, acquisition_mode = _source_provenance(job)
        payload_json = _json(sanitize_job_payload(job.raw_payload))

        with self.connect() as connection:
            self._upsert_canonical_job(connection, canonical_id, job)
            connection.execute(
                """
                INSERT INTO source_postings(
                    id, canonical_job_id, source_id, platform, acquisition_mode,
                    source_job_id, source_url, canonical_url, application_url,
                    first_seen_at, last_seen_at, raw_payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    canonical_job_id = excluded.canonical_job_id,
                    platform = excluded.platform,
                    acquisition_mode = excluded.acquisition_mode,
                    source_url = excluded.source_url,
                    canonical_url = excluded.canonical_url,
                    application_url = excluded.application_url,
                    last_seen_at = excluded.last_seen_at,
                    raw_payload_json = excluded.raw_payload_json
                """,
                (
                    posting_id,
                    canonical_id,
                    job.source,
                    platform,
                    acquisition_mode,
                    job.source_job_id,
                    job.source_url,
                    job.canonical_url,
                    "",
                    job.first_seen_at.astimezone(UTC).isoformat(),
                    observed_at,
                    payload_json,
                ),
            )
            observation_id = stable_id(
                "observation",
                posting_id,
                run_id,
                profile_id,
                observed_at,
            )
            # An observation is an audit trail entry, so it only needs to carry
            # a payload when that payload differs from what the posting already
            # records. Storing a byte-identical copy of every payload on both
            # tables roughly doubled the workspace file for no added
            # information; "" means "identical to the posting at this time".
            connection.execute(
                """
                INSERT OR IGNORE INTO source_observations(
                    id, posting_id, run_id, profile_id, observed_at, raw_payload_json
                ) VALUES (?, ?, ?, ?, ?, '')
                """,
                (observation_id, posting_id, run_id, profile_id, observed_at),
            )
            connection.execute(
                """
                INSERT INTO profile_matches(
                    canonical_job_id, profile_id, accepted, rejection_reason,
                    decision_step, first_evaluated_at, last_evaluated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(canonical_job_id, profile_id) DO UPDATE SET
                    accepted = excluded.accepted,
                    rejection_reason = excluded.rejection_reason,
                    decision_step = excluded.decision_step,
                    last_evaluated_at = excluded.last_evaluated_at
                """,
                (
                    canonical_id,
                    profile_id,
                    int(decision.accepted),
                    "" if decision.reason is None else decision.reason.value,
                    decision.step,
                    evaluated,
                    evaluated,
                ),
            )
            if legacy_job_id:
                connection.execute(
                    """
                    INSERT INTO legacy_job_links(
                        profile_id, legacy_job_id, canonical_job_id,
                        legacy_database_path
                    ) VALUES (?, ?, ?, '')
                    ON CONFLICT(profile_id, legacy_job_id) DO UPDATE SET
                        canonical_job_id = excluded.canonical_job_id
                    """,
                    (profile_id, legacy_job_id, canonical_id),
                )
        return canonical_id

    def record_screening_result(self, result: ScreeningResult) -> None:
        """Upsert one validated result through its stable legacy-job crosswalk."""

        self.record_screening_results((result,))

    def record_screening_results(self, results: tuple[ScreeningResult, ...]) -> None:
        """Persist a complete handoff atomically, or leave the workspace unchanged."""

        with self.connect() as connection:
            resolved: list[tuple[ScreeningResult, str]] = []
            for result in results:
                row = connection.execute(
                    """SELECT canonical_job_id FROM legacy_job_links
                    WHERE profile_id = ? AND legacy_job_id = ?""",
                    (result.profile_id, result.legacy_job_id),
                ).fetchone()
                if row is None:
                    raise ValueError(
                        "screening result has no canonical legacy-job crosswalk: "
                        f"{result.profile_id}/{result.legacy_job_id}"
                    )
                resolved.append((result, str(row["canonical_job_id"])))
            for result, canonical_job_id in resolved:
                self._upsert_screening_result(connection, result, canonical_job_id)

    @staticmethod
    def _upsert_screening_result(
        connection: sqlite3.Connection,
        result: ScreeningResult,
        canonical_job_id: str,
    ) -> None:

        payload = {
            "processing_mode": result.processing_mode,
            "status": result.status,
            "selected": result.selected,
            "score": result.score,
            "core_fit": result.core_fit,
            "variant": result.variant,
            "true_gap": result.true_gap,
            "rationale": result.rationale,
            "decision_source": result.decision_source,
            "tailoring_status": result.tailoring_status,
        }
        payload_json = _json(payload)
        payload_hash = stable_id("screening-result", payload_json)
        connection.execute(
            """INSERT INTO screening_results(
                    canonical_job_id, profile_id, processing_mode, status, selected,
                    score, core_fit, variant, true_gap_json, rationale,
                    decision_source, tailoring_status, contract_version,
                    evaluated_at, payload_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(canonical_job_id, profile_id, contract_version) DO UPDATE SET
                    processing_mode = excluded.processing_mode,
                    status = excluded.status,
                    selected = excluded.selected,
                    score = excluded.score,
                    core_fit = excluded.core_fit,
                    variant = excluded.variant,
                    true_gap_json = excluded.true_gap_json,
                    rationale = excluded.rationale,
                    decision_source = excluded.decision_source,
                    tailoring_status = excluded.tailoring_status,
                    evaluated_at = excluded.evaluated_at,
                    payload_hash = excluded.payload_hash
                """,
            (
                canonical_job_id,
                result.profile_id,
                result.processing_mode,
                result.status,
                int(result.selected),
                result.score,
                result.core_fit,
                result.variant,
                _json(result.true_gap),
                result.rationale,
                result.decision_source,
                result.tailoring_status,
                result.contract_version,
                result.evaluated_at.astimezone(UTC).isoformat(),
                payload_hash,
            ),
        )

    def migrate_v1(self, profile_id: str, database_path: Path) -> MigrationReport:
        if not database_path.is_file():
            return MigrationReport(profile_id=profile_id, database_path=database_path)

        # Read-only: this migration must never modify or delete its source
        # database (see AGENTS.md data-safety rules).
        source = sqlite3.connect(f"file:{database_path}?mode=ro", uri=True)
        source.row_factory = sqlite3.Row
        try:
            if not _table_exists(source, "jobs"):
                return MigrationReport(
                    profile_id=profile_id,
                    database_path=database_path,
                )
            rows = source.execute("SELECT * FROM jobs ORDER BY first_seen_at").fetchall()
            applications = _rows_by_key(
                source,
                "application_state",
                "job_id",
            )
            publications = _rows_by_key(
                source,
                "notion_sync_state",
                "job_id",
            )
        finally:
            source.close()

        applications_migrated = 0
        publications_migrated = 0
        for row in rows:
            job = _job_from_v1_row(row)
            canonical_id = self.record_candidate(
                job,
                Decision.accept(),
                profile_id=profile_id,
                run_id=f"migration:{profile_id}",
                evaluated_at=job.first_seen_at,
                legacy_job_id=str(row["id"]),
            )
            self._set_legacy_database_path(
                profile_id,
                str(row["id"]),
                database_path,
            )
            application = applications.get(str(row["id"]))
            if application is not None:
                self._migrate_application(canonical_id, application)
                applications_migrated += 1
            publication = publications.get(str(row["id"]))
            if publication is not None:
                self._migrate_publication(canonical_id, publication)
                publications_migrated += 1

        return MigrationReport(
            profile_id=profile_id,
            database_path=database_path,
            jobs_read=len(rows),
            jobs_linked=len(rows),
            applications_migrated=applications_migrated,
            publications_migrated=publications_migrated,
        )

    def counts(self) -> dict[str, int]:
        """Row counts, labelled so a frozen table cannot be read as current."""
        tables = tuple(table for table in KNOWN_TABLES if table != "schema_migrations")
        with self.connect() as connection:
            counted = {
                _count_label(table): int(
                    connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
                )
                for table in tables
                if _table_exists(connection, table)
            }
            for table in self.unknown_tables():
                counted[f"{table} (unknown)"] = int(
                    connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
                )
        return counted

    def frozen_table_watermarks(self) -> dict[str, str]:
        """Newest timestamp in each frozen table, to show how stale it is."""
        columns = {"external_publications": "last_synced_at"}
        watermarks: dict[str, str] = {}
        with self.connect() as connection:
            for table, column in columns.items():
                if table not in FROZEN_TABLES:
                    continue
                row = connection.execute(f'SELECT MAX("{column}") FROM "{table}"').fetchone()
                if row and row[0]:
                    watermarks[table] = str(row[0])
        return watermarks

    def find_source_job_detail(
        self,
        source_id: str,
        source_job_id: str,
    ) -> StoredJobDetail | None:
        if not source_id or not source_job_id:
            return None
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT
                    sp.source_id, sp.source_job_id, sp.source_url,
                    sp.raw_payload_json,
                    cj.normalized_title, cj.company_name, cj.location_text,
                    cj.description_full, cj.employment_type
                FROM source_postings AS sp
                JOIN canonical_jobs AS cj ON cj.id = sp.canonical_job_id
                WHERE sp.source_id = ? AND sp.source_job_id = ?
                ORDER BY sp.last_seen_at DESC
                LIMIT 1
                """,
                (source_id, source_job_id),
            ).fetchone()
        if row is None or not str(row["description_full"] or "").strip():
            return None
        try:
            payload = json.loads(str(row["raw_payload_json"] or "{}"))
        except json.JSONDecodeError:
            payload = {}
        source_description = _first_payload_text(
            payload,
            "description_text",
            "description",
            "job_description",
            "job_description_formatted",
        )
        return StoredJobDetail(
            source_id=str(row["source_id"]),
            source_job_id=str(row["source_job_id"]),
            source_url=str(row["source_url"]),
            title=str(row["normalized_title"]),
            company_name=str(row["company_name"]),
            location_text=str(row["location_text"]),
            description=source_description or str(row["description_full"]),
            employment_type=str(row["employment_type"]),
        )

    def _upsert_canonical_job(
        self,
        connection: sqlite3.Connection,
        canonical_id: str,
        job: JobRecord,
    ) -> None:
        first_seen = job.first_seen_at.astimezone(UTC).isoformat()
        last_seen = job.scraped_at.astimezone(UTC).isoformat()
        connection.execute(
            """
            INSERT INTO canonical_jobs(
                id, identity_key, normalized_title, company_name, country_code,
                city, location_text, description_full, employment_type,
                remote_mode, seniority, salary_text, first_seen_at, last_seen_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                normalized_title = excluded.normalized_title,
                company_name = excluded.company_name,
                country_code = excluded.country_code,
                city = excluded.city,
                location_text = excluded.location_text,
                description_full = CASE
                    WHEN length(excluded.description_full) > length(canonical_jobs.description_full)
                    THEN excluded.description_full
                    ELSE canonical_jobs.description_full
                END,
                employment_type = excluded.employment_type,
                remote_mode = excluded.remote_mode,
                seniority = excluded.seniority,
                salary_text = excluded.salary_text,
                first_seen_at = min(canonical_jobs.first_seen_at, excluded.first_seen_at),
                last_seen_at = max(canonical_jobs.last_seen_at, excluded.last_seen_at)
            """,
            (
                canonical_id,
                canonical_identity(job),
                job.title,
                job.company_name,
                job.country,
                job.city,
                job.location_raw,
                job.job_description,
                job.employment_type,
                job.remote_type,
                job.seniority,
                job.salary_text,
                first_seen,
                last_seen,
            ),
        )

    def _set_legacy_database_path(
        self,
        profile_id: str,
        legacy_job_id: str,
        database_path: Path,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE legacy_job_links
                SET legacy_database_path = ?
                WHERE profile_id = ? AND legacy_job_id = ?
                """,
                (str(database_path.resolve()), profile_id, legacy_job_id),
            )

    def _migrate_application(
        self,
        canonical_id: str,
        row: sqlite3.Row,
    ) -> None:
        updated_at = (
            str(row["last_user_edit_at"] or "")
            or str(row["applied_at"] or "")
            or datetime.now(UTC).isoformat()
        )
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO applications(
                    canonical_job_id, status, applied_at, updated_at, notes
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(canonical_job_id) DO UPDATE SET
                    status = CASE
                        WHEN applications.status = 'applied' THEN applications.status
                        ELSE excluded.status
                    END,
                    applied_at = COALESCE(excluded.applied_at, applications.applied_at),
                    updated_at = excluded.updated_at,
                    notes = CASE
                        WHEN excluded.notes <> '' THEN excluded.notes
                        ELSE applications.notes
                    END
                """,
                (
                    canonical_id,
                    str(row["application_status"] or "new"),
                    row["applied_at"],
                    updated_at,
                    str(row["notes"] or ""),
                ),
            )

    def _migrate_publication(
        self,
        canonical_id: str,
        row: sqlite3.Row,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO external_publications(
                    canonical_job_id, sink_id, external_id, external_container_id,
                    payload_hash, status, last_synced_at
                ) VALUES (?, 'notion', ?, ?, ?, ?, ?)
                ON CONFLICT(canonical_job_id, sink_id, external_id) DO UPDATE SET
                    external_container_id = excluded.external_container_id,
                    payload_hash = excluded.payload_hash,
                    status = excluded.status,
                    last_synced_at = excluded.last_synced_at
                """,
                (
                    canonical_id,
                    str(row["notion_page_id"]),
                    str(row["notion_data_source_id"]),
                    str(row["last_payload_hash"]),
                    str(row["sync_status"]),
                    str(row["last_synced_at"]),
                ),
            )


def _source_provenance(job: JobRecord) -> tuple[str, str]:
    source = job.source.strip().lower()
    platforms = job.raw_payload.get("source_platforms")
    if isinstance(platforms, list) and platforms:
        platform = str(platforms[0]).strip().lower() or source
    else:
        platform = source
    if source == "email":
        return platform, "email"
    if source == "indeed":
        return "indeed", "managed_dataset"
    return platform, "direct"


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    return row is not None


def _rows_by_key(
    connection: sqlite3.Connection,
    table: str,
    key: str,
) -> dict[str, sqlite3.Row]:
    if not _table_exists(connection, table):
        return {}
    return {str(row[key]): row for row in connection.execute(f"SELECT * FROM {table}").fetchall()}


def _job_from_v1_row(row: sqlite3.Row) -> JobRecord:
    return JobRecord(
        source=str(row["source"]),
        source_job_id=str(row["source_job_id"]),
        source_url=str(row["source_url"]),
        canonical_url=str(row["source_url"]),
        title=str(row["normalized_title"]),
        company_name=str(row["company_name"]),
        location_raw=str(row["location_text"]),
        country=str(row["country_code"]),
        city=str(row["city"]),
        region=str(row["region"]),
        remote_type=str(row["remote_mode"]),
        employment_type=str(row["employment_type"]),
        seniority=str(row["seniority"]),
        posted_at=_optional_datetime(row["posted_at"]),
        first_seen_at=_required_datetime(row["first_seen_at"]),
        scraped_at=_required_datetime(row["last_seen_at"]),
        job_description=str(row["description_full"]),
        description_language=str(row["description_language"]),
        english_ratio=float(row["english_ratio"]),
        keyword_hits=_json_list(row["keyword_hits_json"]),
        tech_stack=_json_list(row["tech_stack_json"]),
        salary_text=str(row["salary_text"]),
        salary_min=_optional_float(row["salary_min"]),
        salary_max=_optional_float(row["salary_max"]),
        salary_currency=None if row["currency"] is None else str(row["currency"]),
        dedupe_key=str(row["dedupe_key"]),
        raw_payload=sanitize_job_payload(_json_object(row["raw_payload_json"])),
    )


def _required_datetime(value: object) -> datetime:
    parsed = _optional_datetime(value)
    if parsed is None:
        raise ValueError(f"Expected datetime, got {value!r}")
    return parsed


def _optional_datetime(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _optional_float(value: object) -> float | None:
    if value is None or value == "":
        return None
    return float(str(value))


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _first_payload_text(payload: object, *keys: str) -> str:
    if not isinstance(payload, dict):
        return ""
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _json_list(value: object) -> list[str]:
    try:
        parsed = json.loads(str(value or "[]"))
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed]


def _json_object(value: object) -> dict[str, object]:
    try:
        parsed = json.loads(str(value or "{}"))
    except json.JSONDecodeError:
        return {}
    return dict(parsed) if isinstance(parsed, dict) else {}


def _count_label(table: str) -> str:
    return f"{table} (frozen)" if table in FROZEN_TABLES else table
