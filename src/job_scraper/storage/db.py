from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from job_scraper.domain.locations import merge_locations
from job_scraper.domain.models import JobHistorySnapshot, JobRecord, RunStats
from job_scraper.domain.payload_sanitization import sanitize_job_payload
from job_scraper.domain.screening_feed import Publication, ScreeningFeedRecord
from job_scraper.pipeline.normalize import (
    normalize_title_for_dedupe,
    serialize_payload,
)

SCHEMA = """
PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    dedupe_key TEXT NOT NULL UNIQUE,
    source TEXT NOT NULL,
    source_job_id TEXT NOT NULL,
    source_url TEXT NOT NULL,
    canonical_url TEXT NOT NULL,
    normalized_title TEXT NOT NULL,
    company_name TEXT NOT NULL,
    location_text TEXT NOT NULL,
    country_code TEXT NOT NULL,
    city TEXT NOT NULL,
    region TEXT NOT NULL,
    remote_mode TEXT NOT NULL,
    employment_type TEXT NOT NULL,
    seniority TEXT NOT NULL,
    posted_at TEXT,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    description_full TEXT NOT NULL,
    description_language TEXT NOT NULL,
    english_ratio REAL NOT NULL,
    salary_text TEXT NOT NULL,
    salary_min REAL,
    salary_max REAL,
    currency TEXT,
    apply_url TEXT NOT NULL,
    company_url TEXT NOT NULL,
    keyword_hits_json TEXT NOT NULL,
    tech_stack_json TEXT NOT NULL,
    raw_payload_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS job_observations (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    source_platform TEXT NOT NULL,
    source_job_id TEXT NOT NULL,
    source_url TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    raw_title TEXT NOT NULL,
    raw_company TEXT NOT NULL,
    raw_location TEXT NOT NULL,
    raw_payload_json TEXT NOT NULL,
    fetch_status TEXT NOT NULL,
    FOREIGN KEY(job_id) REFERENCES jobs(id)
);

CREATE TABLE IF NOT EXISTS scrape_runs (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    jobs_seen INTEGER NOT NULL DEFAULT 0,
    jobs_new INTEGER NOT NULL DEFAULT 0,
    jobs_updated INTEGER NOT NULL DEFAULT 0,
    jobs_filtered INTEGER NOT NULL DEFAULT 0,
    jobs_failed INTEGER NOT NULL DEFAULT 0,
    errors_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notion_sync_state (
    job_id TEXT PRIMARY KEY,
    notion_page_id TEXT NOT NULL,
    notion_data_source_id TEXT NOT NULL,
    last_synced_at TEXT NOT NULL,
    last_payload_hash TEXT NOT NULL,
    sync_status TEXT NOT NULL,
    retry_count INTEGER NOT NULL DEFAULT 0,
    last_error TEXT NOT NULL DEFAULT '',
    FOREIGN KEY(job_id) REFERENCES jobs(id)
);

CREATE TABLE IF NOT EXISTS application_state (
    job_id TEXT PRIMARY KEY,
    application_status TEXT NOT NULL DEFAULT 'new',
    applied_at TEXT,
    priority TEXT NOT NULL DEFAULT 'normal',
    contact_name TEXT NOT NULL DEFAULT '',
    contact_email TEXT NOT NULL DEFAULT '',
    referral INTEGER NOT NULL DEFAULT 0,
    follow_up_at TEXT,
    notes TEXT NOT NULL DEFAULT '',
    last_user_edit_at TEXT,
    FOREIGN KEY(job_id) REFERENCES jobs(id)
);

CREATE TABLE IF NOT EXISTS external_snapshot_state (
    snapshot_id TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    dataset_id TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    request_payload_json TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    consumed_at TEXT,
    last_error TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_external_snapshot_resume
ON external_snapshot_state(provider, dataset_id, request_hash, consumed_at, updated_at);

-- Lookup indexes. Without these, every candidate upsert and every history
-- probe degrades to a full table scan of `jobs`, which grows with the run
-- history rather than with the size of one run.
CREATE INDEX IF NOT EXISTS idx_jobs_source_url ON jobs(source_url);
CREATE INDEX IF NOT EXISTS idx_jobs_canonical_url ON jobs(canonical_url);
CREATE INDEX IF NOT EXISTS idx_jobs_company_lower ON jobs(lower(company_name), first_seen_at);
CREATE INDEX IF NOT EXISTS idx_jobs_title_company_lower
ON jobs(lower(normalized_title), lower(company_name));
CREATE INDEX IF NOT EXISTS idx_observations_job ON job_observations(job_id);
CREATE INDEX IF NOT EXISTS idx_observations_platform ON job_observations(source_platform);
CREATE INDEX IF NOT EXISTS idx_notion_state_page ON notion_sync_state(notion_page_id);
CREATE INDEX IF NOT EXISTS idx_application_state_status
ON application_state(lower(application_status), last_user_edit_at);
"""


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 30000")
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(SCHEMA)

    def create_run(self, source: str, started_at: datetime) -> RunStats:
        run_id = str(uuid.uuid4())
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO scrape_runs (id, source, started_at, status, errors_json) VALUES (?, ?, ?, 'running', '[]')",
                (run_id, source, started_at.isoformat()),
            )
        return RunStats(run_id=run_id, source=source, started_at=started_at)

    def find_resumable_snapshot(
        self,
        provider: str,
        dataset_id: str,
        request_hash: str,
        *,
        max_age_seconds: float | None = None,
    ) -> sqlite3.Row | None:
        """Return the newest unconsumed snapshot for the exact same request."""
        age_clause = ""
        parameters: list[object] = [provider, dataset_id, request_hash]
        if max_age_seconds is not None:
            if max_age_seconds <= 0:
                raise ValueError("max_age_seconds must be greater than 0")
            cutoff = datetime.now(UTC) - timedelta(seconds=max_age_seconds)
            age_clause = """
                  AND (
                      status = 'ready'
                      OR (status IN ('starting', 'running') AND created_at >= ?)
                  )
            """
            parameters.append(cutoff.isoformat())
        with self.connect() as connection:
            return connection.execute(
                f"""
                SELECT snapshot_id, status, request_payload_json, updated_at, last_error
                FROM external_snapshot_state
                WHERE provider = ?
                  AND dataset_id = ?
                  AND request_hash = ?
                  AND consumed_at IS NULL
                  AND status IN ('starting', 'running', 'ready')
                  {age_clause}
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                parameters,
            ).fetchone()

    def register_snapshot(
        self,
        snapshot_id: str,
        provider: str,
        dataset_id: str,
        request_hash: str,
        request_payload: object,
        status: str = "starting",
    ) -> None:
        now = datetime.now(UTC).isoformat()
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO external_snapshot_state (
                    snapshot_id, provider, dataset_id, request_hash,
                    request_payload_json, status, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(snapshot_id) DO UPDATE SET
                    status = excluded.status,
                    updated_at = excluded.updated_at,
                    last_error = ''
                """,
                (
                    snapshot_id,
                    provider,
                    dataset_id,
                    request_hash,
                    json.dumps(request_payload, ensure_ascii=False, sort_keys=True),
                    status,
                    now,
                    now,
                ),
            )

    def update_snapshot_status(
        self,
        snapshot_id: str,
        status: str,
        *,
        last_error: str = "",
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE external_snapshot_state
                SET status = ?, updated_at = ?, last_error = ?
                WHERE snapshot_id = ?
                """,
                (
                    status,
                    datetime.now(UTC).isoformat(),
                    str(last_error),
                    snapshot_id,
                ),
            )

    def mark_snapshot_consumed(self, snapshot_id: str) -> None:
        now = datetime.now(UTC).isoformat()
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE external_snapshot_state
                SET status = 'consumed', consumed_at = ?, updated_at = ?, last_error = ''
                WHERE snapshot_id = ?
                """,
                (now, now, snapshot_id),
            )

    def finish_run(self, stats: RunStats, status: str) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE scrape_runs
                SET finished_at = ?, status = ?, jobs_seen = ?, jobs_new = ?, jobs_updated = ?, jobs_filtered = ?, jobs_failed = ?, errors_json = ?
                WHERE id = ?
                """,
                (
                    (stats.finished_at or datetime.now(UTC)).isoformat(),
                    status,
                    stats.jobs_seen,
                    stats.jobs_new,
                    stats.jobs_updated,
                    stats.jobs_filtered,
                    stats.jobs_failed,
                    json.dumps(stats.errors),
                    stats.run_id,
                ),
            )

    def upsert_job(self, job: JobRecord, run_id: str) -> tuple[str, bool]:
        # Read and write share one connection and one transaction: a second
        # connection would let a concurrent writer insert the same dedupe_key
        # between the lookup and the INSERT below.
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = self._lookup_job_row(
                connection,
                job.dedupe_key,
                job.source_url,
                job.canonical_url,
            )
            existing_id = str(existing["id"]) if existing else None
            is_new = existing_id is None
            if existing_id:
                assert existing is not None
                merged_location_text, merged_city, merged_options = merge_locations(
                    str(existing["location_text"] or ""),
                    job.location_raw,
                    str(existing["city"] or ""),
                    job.city,
                )
                existing_payload = json.loads(str(existing["raw_payload_json"] or "{}"))
                merged_payload = sanitize_job_payload(
                    existing_payload if isinstance(existing_payload, dict) else {}
                )
                merged_payload.update(sanitize_job_payload(job.raw_payload))
                if merged_options:
                    merged_payload["location_options"] = merged_options
                company_name = _prefer_job_metadata(
                    job.company_name,
                    str(existing["company_name"] or ""),
                )
                country_code = _prefer_job_metadata(
                    job.country,
                    str(existing["country_code"] or ""),
                )
                normalized_title = _prefer_job_metadata(
                    job.title,
                    str(existing["normalized_title"] or ""),
                )
                connection.execute(
                    """
                    UPDATE jobs
                    SET normalized_title = ?, company_name = ?, location_text = ?, country_code = ?, city = ?,
                        canonical_url = ?, last_seen_at = ?, description_full = ?, description_language = ?, english_ratio = ?, salary_text = ?,
                        salary_min = ?, salary_max = ?, currency = ?, apply_url = ?, company_url = ?,
                        keyword_hits_json = ?, tech_stack_json = ?, raw_payload_json = ?
                    WHERE id = ?
                    """,
                    (
                        normalized_title,
                        company_name,
                        merged_location_text or job.location_raw,
                        country_code,
                        merged_city or job.city,
                        job.canonical_url or str(existing["canonical_url"] or ""),
                        job.scraped_at.isoformat(),
                        job.job_description,
                        job.description_language,
                        job.english_ratio,
                        job.salary_text,
                        job.salary_min,
                        job.salary_max,
                        job.salary_currency,
                        "",
                        "",
                        json.dumps(job.keyword_hits),
                        json.dumps(job.tech_stack),
                        serialize_payload(merged_payload),
                        existing_id,
                    ),
                )
                job_id = existing_id
            else:
                job_id = str(uuid.uuid4())
                connection.execute(
                    """
                    INSERT INTO jobs (
                        id, dedupe_key, source, source_job_id, source_url, canonical_url, normalized_title,
                        company_name, location_text, country_code, city, region, remote_mode, employment_type,
                        seniority, posted_at, first_seen_at, last_seen_at, description_full, description_language,
                        english_ratio, salary_text, salary_min, salary_max, currency, apply_url, company_url,
                        keyword_hits_json, tech_stack_json, raw_payload_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        job_id,
                        job.dedupe_key,
                        job.source,
                        job.source_job_id,
                        job.source_url,
                        job.canonical_url,
                        job.title,
                        job.company_name,
                        job.location_raw,
                        job.country,
                        job.city,
                        job.region,
                        job.remote_type,
                        job.employment_type,
                        job.seniority,
                        job.posted_at.isoformat() if job.posted_at else None,
                        job.first_seen_at.isoformat(),
                        job.scraped_at.isoformat(),
                        job.job_description,
                        job.description_language,
                        job.english_ratio,
                        job.salary_text,
                        job.salary_min,
                        job.salary_max,
                        job.salary_currency,
                        "",
                        "",
                        json.dumps(job.keyword_hits),
                        json.dumps(job.tech_stack),
                        serialize_payload(sanitize_job_payload(job.raw_payload)),
                    ),
                )
                connection.execute(
                    "INSERT OR IGNORE INTO application_state (job_id) VALUES (?)", (job_id,)
                )

            connection.execute(
                """
                INSERT INTO job_observations (
                    id, job_id, run_id, source_platform, source_job_id, source_url, observed_at,
                    raw_title, raw_company, raw_location, raw_payload_json, fetch_status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid.uuid4()),
                    job_id,
                    run_id,
                    job.source,
                    job.source_job_id,
                    job.source_url,
                    job.scraped_at.isoformat(),
                    job.title,
                    job.company_name,
                    job.location_raw,
                    serialize_payload(sanitize_job_payload(job.raw_payload)),
                    "ok",
                ),
            )
        return job_id, is_new

    def export_jobs(self, languages: list[str] | None = None) -> list[dict[str, Any]]:
        with self.connect() as connection:
            query = """
                SELECT
                    j.id, j.dedupe_key, j.source, j.source_job_id,
                    j.source_url, j.canonical_url, j.normalized_title,
                    j.company_name, j.location_text, j.country_code,
                    j.city, j.region, j.remote_mode, j.employment_type,
                    j.seniority, j.posted_at, j.first_seen_at, j.last_seen_at,
                    j.description_full, j.description_language, j.english_ratio,
                    j.salary_text, j.salary_min, j.salary_max, j.currency,
                    j.keyword_hits_json, j.tech_stack_json, j.raw_payload_json,
                    a.application_status, a.applied_at, a.priority, a.notes
                FROM jobs j
                LEFT JOIN application_state a ON a.job_id = j.id
            """
            params: list[str] = []
            if languages:
                placeholders = ", ".join("?" for _ in languages)
                query += f" WHERE j.description_language IN ({placeholders})"
                params.extend(languages)
            query += " ORDER BY j.first_seen_at DESC"
            rows = connection.execute(query, params).fetchall()
            exported: list[dict[str, Any]] = []
            for row in rows:
                item = dict(row)
                payload = _json_object(item.get("raw_payload_json"))
                item["raw_payload_json"] = serialize_payload(sanitize_job_payload(payload))
                exported.append(item)
            return exported

    def read_screening_feed(
        self,
        *,
        profile_id: str,
        since: datetime,
        until_exclusive: datetime,
    ) -> list[ScreeningFeedRecord]:
        """Jobs first seen in a window, with publication and application state.

        Both joins are LEFT joins on purpose. A job with no publication row is
        real -- it was acquired but the sink has not run, or the sink is
        disabled -- and a caller that wants only published jobs says so through
        `select_screenable` rather than silently receiving a shorter list here.
        """
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    j.id, j.normalized_title, j.company_name, j.city,
                    j.description_language, j.canonical_url, j.source_url,
                    j.description_full, j.first_seen_at,
                    n.notion_page_id, n.notion_data_source_id,
                    a.application_status
                FROM jobs j
                LEFT JOIN notion_sync_state n ON n.job_id = j.id
                LEFT JOIN application_state a ON a.job_id = j.id
                WHERE j.first_seen_at >= ? AND j.first_seen_at < ?
                ORDER BY j.first_seen_at ASC, j.id ASC
                """,
                (since.isoformat(), until_exclusive.isoformat()),
            ).fetchall()

        return [
            ScreeningFeedRecord(
                job_id=str(row["id"]),
                profile_id=profile_id,
                title=row["normalized_title"] or "",
                company=row["company_name"] or "",
                location=row["city"] or "",
                language=row["description_language"] or "",
                url=row["canonical_url"] or row["source_url"] or "",
                description=row["description_full"] or "",
                first_seen_at=row["first_seen_at"] or "",
                application_status=row["application_status"] or "new",
                publication=Publication(
                    sink_id="notion_daily" if row["notion_page_id"] else "",
                    external_id=row["notion_page_id"] or "",
                    container_id=row["notion_data_source_id"] or "",
                ),
            )
            for row in rows
        ]

    def has_jobs(self) -> bool:
        with self.connect() as connection:
            row = connection.execute("SELECT 1 FROM jobs LIMIT 1").fetchone()
            return row is not None

    def has_source_observations(self, source: str) -> bool:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM job_observations WHERE source_platform = ? LIMIT 1",
                (source,),
            ).fetchone()
            return row is not None

    def upsert_notion_state(
        self, job_id: str, page_id: str, data_source_id: str, payload_hash: str, status: str
    ) -> None:
        now = datetime.now(UTC).isoformat()
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO notion_sync_state (job_id, notion_page_id, notion_data_source_id, last_synced_at, last_payload_hash, sync_status)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(job_id) DO UPDATE SET
                    notion_page_id = excluded.notion_page_id,
                    notion_data_source_id = excluded.notion_data_source_id,
                    last_synced_at = excluded.last_synced_at,
                    last_payload_hash = excluded.last_payload_hash,
                    sync_status = excluded.sync_status
                """,
                (job_id, page_id, data_source_id, now, payload_hash, status),
            )

    def find_job_id_by_notion_page_id(self, page_id: str) -> str:
        normalized_page_id = str(page_id).strip()
        if not normalized_page_id:
            return ""
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT sync.job_id
                FROM notion_sync_state AS sync
                JOIN jobs ON jobs.id = sync.job_id
                WHERE sync.notion_page_id = ?
                """,
                (normalized_page_id,),
            ).fetchone()
            return str(row["job_id"]) if row else ""

    def match_job_id_for_notion_page(self, title: str, company_name: str, job_url: str = "") -> str:
        normalized_title = " ".join(title.split()).strip().lower()
        normalized_company = " ".join(company_name.split()).strip().lower()
        normalized_url = str(job_url or "").strip()
        with self.connect() as connection:
            if normalized_url:
                rows = list(
                    connection.execute(
                        """
                        SELECT id, normalized_title, company_name, last_seen_at
                        FROM jobs
                        WHERE source_url = ? OR canonical_url = ?
                        ORDER BY last_seen_at DESC
                        """,
                        (normalized_url, normalized_url),
                    )
                )
                filtered = [
                    row
                    for row in rows
                    if (
                        not normalized_title
                        or " ".join(str(row["normalized_title"] or "").split()).strip().lower()
                        == normalized_title
                    )
                    and (
                        not normalized_company
                        or " ".join(str(row["company_name"] or "").split()).strip().lower()
                        == normalized_company
                    )
                ]
                candidates = filtered or rows
                if candidates:
                    return str(candidates[0]["id"])

            if not normalized_title or not normalized_company:
                return ""

            # Title+company alone is a weak signal (a repost or a distinct
            # opening can share both). Only trust it when it identifies
            # exactly one job; an ambiguous match is left unresolved rather
            # than guessing and writing a status onto the wrong job.
            candidate_rows = connection.execute(
                """
                SELECT id
                FROM jobs
                WHERE lower(normalized_title) = ? AND lower(company_name) = ?
                """,
                (normalized_title, normalized_company),
            ).fetchall()
            if len(candidate_rows) != 1:
                return ""
            row = candidate_rows[0]
            return str(row["id"]) if row else ""

    def get_application_status(self, job_id: str) -> str:
        normalized_job_id = str(job_id).strip()
        if not normalized_job_id:
            return ""
        with self.connect() as connection:
            row = connection.execute(
                "SELECT application_status FROM application_state WHERE job_id = ?",
                (normalized_job_id,),
            ).fetchone()
            return str(row["application_status"]) if row else ""

    def set_application_status(
        self, job_id: str, status: str, *, edited_at: datetime | None = None
    ) -> None:
        normalized_job_id = str(job_id).strip()
        normalized_status = " ".join(str(status or "").split()).strip().lower()
        if not normalized_job_id or not normalized_status:
            return
        recorded_at = (edited_at or datetime.now(UTC)).astimezone(UTC).isoformat()
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO application_state (job_id, application_status, last_user_edit_at)
                VALUES (?, ?, ?)
                ON CONFLICT(job_id) DO UPDATE SET
                    application_status = excluded.application_status,
                    last_user_edit_at = excluded.last_user_edit_at
                """,
                (normalized_job_id, normalized_status, recorded_at),
            )

    def has_recent_not_interested_match(
        self,
        job: JobRecord,
        started_at: datetime,
        *,
        lookback: timedelta = timedelta(days=30),
    ) -> bool:
        cutoff = started_at.astimezone(UTC) - lookback
        normalized_title = normalize_title_for_dedupe(job.title, job.location_raw)
        normalized_company = " ".join(job.company_name.split()).strip().casefold()
        current_location = _normalized_location(job.city or job.location_raw)
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT j.normalized_title, j.company_name, j.city, j.location_text
                FROM jobs j
                JOIN application_state a ON a.job_id = j.id
                WHERE lower(a.application_status) = 'not_interested'
                  AND a.last_user_edit_at > ?
                  AND a.last_user_edit_at <= ?
                  AND lower(j.company_name) = ?
                """,
                (cutoff.isoformat(), started_at.astimezone(UTC).isoformat(), normalized_company),
            ).fetchall()
        for row in rows:
            previous_title = normalize_title_for_dedupe(
                str(row["normalized_title"] or ""), str(row["location_text"] or "")
            )
            if previous_title != normalized_title:
                continue
            previous_location = _normalized_location(str(row["city"] or row["location_text"] or ""))
            if current_location and previous_location and current_location != previous_location:
                continue
            return True
        return False

    def clear_notion_state_for_page_ids(self, page_ids: list[str]) -> None:
        normalized_ids = [str(page_id).strip() for page_id in page_ids if str(page_id).strip()]
        if not normalized_ids:
            return
        placeholders = ", ".join("?" for _ in normalized_ids)
        with self.connect() as connection:
            connection.execute(
                f"DELETE FROM notion_sync_state WHERE notion_page_id IN ({placeholders})",
                normalized_ids,
            )

    def get_job_history(
        self, job_id: str, company_name: str, started_at: datetime
    ) -> JobHistorySnapshot:
        with self.connect() as connection:
            exact = connection.execute(
                """
                SELECT j.normalized_title, j.company_name, j.first_seen_at, n.notion_page_id
                FROM jobs j
                LEFT JOIN notion_sync_state n ON n.job_id = j.id
                WHERE j.id = ?
                """,
                (job_id,),
            ).fetchone()
            if exact:
                first_seen = datetime.fromisoformat(str(exact["first_seen_at"]))
                if first_seen < started_at:
                    return JobHistorySnapshot(
                        status_label="Seen job",
                        exact_seen_before=True,
                        company_seen_before=True,
                        previous_notion_page_id=str(exact["notion_page_id"] or ""),
                        previous_title=str(exact["normalized_title"] or ""),
                        previous_company_name=str(exact["company_name"] or ""),
                        previous_seen_at=first_seen,
                    )

            company = connection.execute(
                """
                SELECT j.normalized_title, j.company_name, j.first_seen_at, n.notion_page_id
                FROM jobs j
                LEFT JOIN notion_sync_state n ON n.job_id = j.id
                WHERE lower(j.company_name) = lower(?) AND j.id <> ? AND j.first_seen_at < ?
                ORDER BY j.first_seen_at DESC
                LIMIT 1
                """,
                (company_name, job_id, started_at.isoformat()),
            ).fetchone()
            if company:
                return JobHistorySnapshot(
                    status_label="Seen company",
                    company_seen_before=True,
                    previous_notion_page_id=str(company["notion_page_id"] or ""),
                    previous_title=str(company["normalized_title"] or ""),
                    previous_company_name=str(company["company_name"] or ""),
                    previous_seen_at=datetime.fromisoformat(str(company["first_seen_at"])),
                )

        return JobHistorySnapshot(status_label="New")

    @staticmethod
    def _lookup_job_row(
        connection: sqlite3.Connection,
        dedupe_key: str,
        source_url: str = "",
        canonical_url: str = "",
    ) -> sqlite3.Row | None:
        conditions = ["dedupe_key = ?"]
        parameters: list[str] = [dedupe_key]
        for url in dict.fromkeys(value for value in (source_url, canonical_url) if value):
            conditions.append("(source_url = ? OR canonical_url = ?)")
            parameters.extend([url, url])
        query = f"""
            SELECT id, normalized_title, company_name, country_code,
                   source_url, canonical_url, location_text, city, raw_payload_json
            FROM jobs
            WHERE {" OR ".join(conditions)}
            ORDER BY CASE WHEN dedupe_key = ? THEN 0 ELSE 1 END
            LIMIT 1
        """
        parameters.append(dedupe_key)
        return connection.execute(query, parameters).fetchone()


def _json_object(value: object) -> dict[str, Any]:
    try:
        parsed = json.loads(str(value or "{}"))
    except json.JSONDecodeError:
        return {}
    return dict(parsed) if isinstance(parsed, dict) else {}


def _normalized_location(value: str) -> str:
    normalized = " ".join(value.split()).strip().casefold()
    if normalized in {"", "unknown", "n/a", "multiple locations"}:
        return ""
    return normalized


def _prefer_job_metadata(new_value: str, existing_value: str) -> str:
    normalized_new = " ".join(str(new_value or "").split()).strip()
    if normalized_new.casefold() in {"", "unknown", "n/a"}:
        return existing_value
    return normalized_new
