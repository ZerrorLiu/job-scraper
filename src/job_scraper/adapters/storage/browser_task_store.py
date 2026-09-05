"""Durable single-tenant browser task queue and transactional outbox."""

from __future__ import annotations

import json
import secrets
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from zoneinfo import ZoneInfo

TASK_KINDS = frozenset({"search", "detail"})
TERMINAL_STATUSES = frozenset({"complete", "blocked", "unavailable"})
DEFAULT_LEASE_SECONDS = 600
ENROLLMENT_TOKEN_TTL_SECONDS = 3600
MAX_OUTBOX_ATTEMPTS = 5
BASE_SCHEMA_VERSION = 2
BERLIN = ZoneInfo("Europe/Berlin")

SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL, description TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS tenant_identity (singleton INTEGER PRIMARY KEY CHECK(singleton=1), instance_id TEXT NOT NULL UNIQUE);
CREATE TABLE IF NOT EXISTS browser_tasks (
 task_id TEXT PRIMARY KEY, kind TEXT NOT NULL CHECK(kind IN ('search','detail')), status TEXT NOT NULL,
 payload_json TEXT NOT NULL, lease_id TEXT, lease_expires_at TEXT, idempotency_key TEXT,
 result_json TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_browser_tasks_claimable ON browser_tasks(kind,status,created_at);
CREATE TABLE IF NOT EXISTS enrollment_tokens (
 token_hash TEXT PRIMARY KEY, created_at TEXT NOT NULL, expires_at TEXT NOT NULL, redeemed_at TEXT
);
CREATE TABLE IF NOT EXISTS worker_credentials (
 device_id TEXT PRIMARY KEY, token_hash TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL, revoked_at TEXT
);
CREATE TABLE IF NOT EXISTS browser_outbox (
 event_id TEXT PRIMARY KEY, task_id TEXT NOT NULL UNIQUE REFERENCES browser_tasks(task_id),
 kind TEXT NOT NULL CHECK(kind IN ('search','detail')),
 state TEXT NOT NULL CHECK(state IN ('pending','processing','applied','failed')),
 payload_json TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, next_attempt_at TEXT NOT NULL,
 last_error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, applied_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_browser_outbox_ready ON browser_outbox(state,next_attempt_at,created_at);
"""


class BrowserTaskStoreError(Exception):
    """Invalid queue request or state transition."""


class LostLeaseError(BrowserTaskStoreError):
    """The task is no longer held by the submitted lease."""


@dataclass(frozen=True, slots=True)
class ClaimedTask:
    task_id: str
    lease_id: str
    lease_expires_at: str
    kind: str
    payload: dict[str, object]


@dataclass(frozen=True, slots=True)
class CompletionOutcome:
    status: str
    result: dict[str, object]
    replayed: bool
    event_id: str


@dataclass(frozen=True, slots=True)
class OutboxEvent:
    event_id: str
    task_id: str
    kind: str
    payload: dict[str, object]
    attempts: int


def _now() -> datetime:
    return datetime.now(UTC)


def _hash_token(token: str) -> str:
    return sha256(token.encode()).hexdigest()


class BrowserTaskStore:
    """One instance is one client's storage boundary."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=30000")
        connection.execute("PRAGMA foreign_keys=ON")
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
                "INSERT OR IGNORE INTO tenant_identity VALUES (1,?)", (secrets.token_urlsafe(18),)
            )
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations VALUES (?,?,?)",
                (BASE_SCHEMA_VERSION, _now().isoformat(), "transactional browser queue/outbox"),
            )

    def instance_id(self) -> str:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT instance_id FROM tenant_identity WHERE singleton=1"
            ).fetchone()
        if row is None:
            raise BrowserTaskStoreError("Store is not initialized")
        return str(row["instance_id"])

    def enqueue(self, kind: str, task_id: str, payload: dict[str, object]) -> bool:
        if kind not in TASK_KINDS:
            raise BrowserTaskStoreError(f"Unknown task kind {kind!r}")
        now = _now().isoformat()
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            inserted = connection.execute(
                "INSERT OR IGNORE INTO browser_tasks(task_id,kind,status,payload_json,created_at,updated_at) "
                "VALUES (?,?,'pending',?,?,?)",
                (task_id, kind, json.dumps(payload, sort_keys=True), now, now),
            )
            return inserted.rowcount == 1

    def claim(
        self,
        kind: str,
        *,
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
        created_on: date | None = None,
    ) -> ClaimedTask | None:
        if kind not in TASK_KINDS:
            raise BrowserTaskStoreError(f"Unknown task kind {kind!r}")
        if lease_seconds <= 0:
            raise BrowserTaskStoreError("lease_seconds must be positive")
        now = _now()
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "UPDATE browser_tasks SET status='pending',lease_id=NULL,lease_expires_at=NULL,updated_at=? "
                "WHERE status='in_progress' AND lease_expires_at<=?",
                (now.isoformat(), now.isoformat()),
            )
            if created_on is None:
                row = connection.execute(
                    "SELECT task_id,kind,payload_json FROM browser_tasks WHERE kind=? AND status='pending' "
                    "ORDER BY created_at,task_id LIMIT 1",
                    (kind,),
                ).fetchone()
            else:
                start = datetime.combine(created_on, datetime.min.time(), tzinfo=BERLIN).astimezone(
                    UTC
                )
                end = start + timedelta(days=1)
                row = connection.execute(
                    "SELECT task_id,kind,payload_json FROM browser_tasks "
                    "WHERE kind=? AND status='pending' AND created_at>=? AND created_at<? "
                    "ORDER BY created_at,task_id LIMIT 1",
                    (kind, start.isoformat(), end.isoformat()),
                ).fetchone()
            if row is None:
                return None
            lease_id = secrets.token_urlsafe(24)
            expires = (now + timedelta(seconds=lease_seconds)).isoformat()
            changed = connection.execute(
                "UPDATE browser_tasks SET status='in_progress',lease_id=?,lease_expires_at=?,updated_at=? "
                "WHERE task_id=? AND status='pending'",
                (lease_id, expires, now.isoformat(), row["task_id"]),
            )
            if changed.rowcount != 1:
                return None
            payload = json.loads(row["payload_json"])
            payload["contract_version"] = "1.0"
            return ClaimedTask(str(row["task_id"]), lease_id, expires, str(row["kind"]), payload)

    def heartbeat(
        self, task_id: str, lease_id: str, *, lease_seconds: int = DEFAULT_LEASE_SECONDS
    ) -> str:
        now = _now()
        expires = (now + timedelta(seconds=lease_seconds)).isoformat()
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            changed = connection.execute(
                "UPDATE browser_tasks SET lease_expires_at=?,updated_at=? WHERE task_id=? AND lease_id=? "
                "AND status='in_progress' AND lease_expires_at>?",
                (expires, now.isoformat(), task_id, lease_id, now.isoformat()),
            )
            if changed.rowcount != 1:
                raise LostLeaseError("Lease is no longer held")
        return expires

    def complete(
        self,
        task_id: str,
        lease_id: str,
        *,
        idempotency_key: str,
        status: str,
        result: dict[str, object],
    ) -> CompletionOutcome:
        if not idempotency_key:
            raise BrowserTaskStoreError("idempotency_key is required")
        if status not in TERMINAL_STATUSES:
            raise BrowserTaskStoreError(f"Invalid terminal status {status!r}")
        now = _now().isoformat()
        encoded = json.dumps(result, sort_keys=True)
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT kind,status,lease_id,lease_expires_at,idempotency_key,result_json "
                "FROM browser_tasks WHERE task_id=?",
                (task_id,),
            ).fetchone()
            if row is None:
                raise BrowserTaskStoreError(f"Unknown task_id {task_id!r}")
            event_id = f"browser:{task_id}"
            if row["idempotency_key"] == idempotency_key and row["result_json"] is not None:
                return CompletionOutcome(
                    str(row["status"]), json.loads(row["result_json"]), True, event_id
                )
            if row["idempotency_key"] is not None:
                raise BrowserTaskStoreError("Task already completed with another idempotency key")
            if (
                row["status"] != "in_progress"
                or row["lease_id"] != lease_id
                or not row["lease_expires_at"]
                or row["lease_expires_at"] <= now
            ):
                raise LostLeaseError("Lease is no longer held")
            connection.execute(
                "UPDATE browser_tasks SET status=?,lease_id=NULL,lease_expires_at=NULL,"
                "idempotency_key=?,result_json=?,updated_at=? WHERE task_id=?",
                (status, idempotency_key, encoded, now, task_id),
            )
            connection.execute(
                "INSERT INTO browser_outbox(event_id,task_id,kind,state,payload_json,next_attempt_at,created_at,updated_at) "
                "VALUES (?,?,?,'pending',?,?,?,?)",
                (event_id, task_id, row["kind"], encoded, now, now, now),
            )
            return CompletionOutcome(status, result, False, event_id)

    def get(self, task_id: str) -> dict[str, object] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM browser_tasks WHERE task_id=?", (task_id,)
            ).fetchone()
        if row is None:
            return None
        return {
            "task_id": row["task_id"],
            "kind": row["kind"],
            "status": row["status"],
            "payload": json.loads(row["payload_json"]),
            "result": json.loads(row["result_json"]) if row["result_json"] else None,
        }

    def claim_outbox(self, *, event_id: str | None = None) -> OutboxEvent | None:
        clock = _now()
        now = clock.isoformat()
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "UPDATE browser_outbox SET state='pending',updated_at=? "
                "WHERE state='processing' AND updated_at<=?",
                (now, (clock - timedelta(minutes=10)).isoformat()),
            )
            query = (
                "SELECT event_id,task_id,kind,payload_json,attempts FROM browser_outbox "
                "WHERE state='pending' AND next_attempt_at<=?"
            )
            params: tuple[object, ...] = (now,)
            if event_id is not None:
                query += " AND event_id=?"
                params = (now, event_id)
            row = connection.execute(query + " ORDER BY created_at LIMIT 1", params).fetchone()
            if row is None:
                return None
            changed = connection.execute(
                "UPDATE browser_outbox SET state='processing',attempts=attempts+1,updated_at=? "
                "WHERE event_id=? AND state='pending'",
                (now, row["event_id"]),
            )
            if changed.rowcount != 1:
                return None
            return OutboxEvent(
                str(row["event_id"]),
                str(row["task_id"]),
                str(row["kind"]),
                json.loads(row["payload_json"]),
                int(row["attempts"]) + 1,
            )

    def finish_outbox(self, event_id: str) -> None:
        now = _now().isoformat()
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            changed = connection.execute(
                "UPDATE browser_outbox SET state='applied',applied_at=?,updated_at=?,last_error=NULL "
                "WHERE event_id=? AND state='processing'",
                (now, now, event_id),
            )
            if changed.rowcount != 1:
                raise BrowserTaskStoreError(f"Outbox event {event_id!r} is not processing")

    def fail_outbox(self, event_id: str, error: str) -> str:
        now = _now()
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT attempts FROM browser_outbox WHERE event_id=? AND state='processing'",
                (event_id,),
            ).fetchone()
            if row is None:
                raise BrowserTaskStoreError(f"Outbox event {event_id!r} is not processing")
            attempts = int(row["attempts"])
            state = "failed" if attempts >= MAX_OUTBOX_ATTEMPTS else "pending"
            delay = min(300, 2 ** max(0, attempts - 1))
            connection.execute(
                "UPDATE browser_outbox SET state=?,next_attempt_at=?,last_error=?,updated_at=? WHERE event_id=?",
                (
                    state,
                    (now + timedelta(seconds=delay)).isoformat(),
                    error[:2000],
                    now.isoformat(),
                    event_id,
                ),
            )
            return state

    def retry_outbox(self, event_id: str) -> bool:
        now = _now().isoformat()
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            changed = connection.execute(
                "UPDATE browser_outbox SET state='pending',next_attempt_at=?,last_error=NULL,updated_at=? "
                "WHERE event_id=? AND state='failed'",
                (now, now, event_id),
            )
            return changed.rowcount == 1

    def list_outbox(self, state: str | None = None) -> list[dict[str, object]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM browser_outbox"
                + (" WHERE state=?" if state else "")
                + " ORDER BY created_at",
                (state,) if state else (),
            ).fetchall()
        return [dict(row) for row in rows]

    def status(self) -> dict[str, object]:
        with self.connect() as connection:
            tasks = {
                str(r[0]): int(r[1])
                for r in connection.execute(
                    "SELECT status,COUNT(*) FROM browser_tasks GROUP BY status"
                )
            }
            outbox = {
                str(r[0]): int(r[1])
                for r in connection.execute(
                    "SELECT state,COUNT(*) FROM browser_outbox GROUP BY state"
                )
            }
            oldest = connection.execute(
                "SELECT MIN(created_at) FROM browser_tasks WHERE status='pending'"
            ).fetchone()[0]
            last = connection.execute(
                "SELECT event_id,applied_at FROM browser_outbox WHERE state='applied' ORDER BY applied_at DESC LIMIT 1"
            ).fetchone()
        return {
            "tasks": tasks,
            "outbox": outbox,
            "oldest_pending_at": oldest,
            "last_applied": dict(last) if last else None,
        }

    def create_enrollment_token(self, *, ttl_seconds: int = ENROLLMENT_TOKEN_TTL_SECONDS) -> str:
        if ttl_seconds <= 0:
            raise BrowserTaskStoreError("ttl_seconds must be positive")
        token = secrets.token_urlsafe(32)
        now = _now()
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO enrollment_tokens VALUES (?,?,?,NULL)",
                (
                    _hash_token(token),
                    now.isoformat(),
                    (now + timedelta(seconds=ttl_seconds)).isoformat(),
                ),
            )
        return token

    def redeem_enrollment_token(self, token: str, *, device_id: str) -> str:
        if not device_id.strip():
            raise BrowserTaskStoreError("device_id is required")
        now = _now()
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT expires_at,redeemed_at FROM enrollment_tokens WHERE token_hash=?",
                (_hash_token(token),),
            ).fetchone()
            if row is None:
                raise BrowserTaskStoreError("Unknown enrollment token")
            if row["redeemed_at"] is not None:
                raise BrowserTaskStoreError("Enrollment token already redeemed")
            if row["expires_at"] <= now.isoformat():
                raise BrowserTaskStoreError("Enrollment token expired")
            existing = connection.execute(
                "SELECT device_id FROM worker_credentials WHERE revoked_at IS NULL"
            ).fetchone()
            if existing is not None and existing["device_id"] != device_id:
                raise BrowserTaskStoreError("This client already has an active browser worker")
            connection.execute(
                "UPDATE enrollment_tokens SET redeemed_at=? WHERE token_hash=?",
                (now.isoformat(), _hash_token(token)),
            )
            device_token = secrets.token_urlsafe(32)
            connection.execute(
                "INSERT INTO worker_credentials VALUES (?,?,?,NULL) ON CONFLICT(device_id) DO UPDATE SET "
                "token_hash=excluded.token_hash,created_at=excluded.created_at,revoked_at=NULL",
                (device_id, _hash_token(device_token), now.isoformat()),
            )
        return device_token

    def verify_device_token(self, token: str) -> bool:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM worker_credentials WHERE token_hash=? AND revoked_at IS NULL",
                (_hash_token(token),),
            ).fetchone()
        return row is not None

    def revoke_device(self, device_id: str) -> bool:
        with self.connect() as connection:
            changed = connection.execute(
                "UPDATE worker_credentials SET revoked_at=? WHERE device_id=? AND revoked_at IS NULL",
                (_now().isoformat(), device_id),
            )
            return changed.rowcount == 1
