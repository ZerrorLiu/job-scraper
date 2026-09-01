"""Tenant-scoped account, onboarding, evidence, and track persistence."""

from __future__ import annotations

import json
import secrets
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path

ONBOARDING_STATES = (
    "account_verified",
    "resume_uploaded",
    "evidence_approved",
    "tracks_approved",
    "integrations_configured",
    "connector_enrolled",
    "browser_calibrated",
    "pipeline_calibrated",
    "active",
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS portal_users(id TEXT PRIMARY KEY,email TEXT NOT NULL UNIQUE,created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS portal_tenants(id TEXT PRIMARY KEY,created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS portal_memberships(user_id TEXT NOT NULL REFERENCES portal_users(id),tenant_id TEXT NOT NULL REFERENCES portal_tenants(id),role TEXT NOT NULL,PRIMARY KEY(user_id,tenant_id));
CREATE TABLE IF NOT EXISTS portal_login_tokens(token_hash TEXT PRIMARY KEY,email TEXT NOT NULL,expires_at TEXT NOT NULL,used_at TEXT);
CREATE TABLE IF NOT EXISTS portal_sessions(token_hash TEXT PRIMARY KEY,user_id TEXT NOT NULL REFERENCES portal_users(id),tenant_id TEXT NOT NULL REFERENCES portal_tenants(id),csrf_token TEXT NOT NULL,expires_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS portal_onboarding(tenant_id TEXT PRIMARY KEY REFERENCES portal_tenants(id),state TEXT NOT NULL,updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS portal_documents(id TEXT PRIMARY KEY,tenant_id TEXT NOT NULL REFERENCES portal_tenants(id),storage_key TEXT NOT NULL,sha256 TEXT NOT NULL,media_type TEXT NOT NULL,size INTEGER NOT NULL,extracted_text TEXT NOT NULL,created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS portal_evidence(id TEXT PRIMARY KEY,tenant_id TEXT NOT NULL REFERENCES portal_tenants(id),document_id TEXT NOT NULL REFERENCES portal_documents(id),claim_text TEXT NOT NULL,source_ref TEXT NOT NULL,status TEXT NOT NULL,created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS portal_tracks(id TEXT PRIMARY KEY,tenant_id TEXT NOT NULL REFERENCES portal_tenants(id),track_key TEXT NOT NULL,label TEXT NOT NULL,mode TEXT NOT NULL,keywords_json TEXT NOT NULL,status TEXT NOT NULL,UNIQUE(tenant_id,track_key));
CREATE TABLE IF NOT EXISTS portal_preferences(tenant_id TEXT PRIMARY KEY REFERENCES portal_tenants(id),locations_json TEXT NOT NULL,languages_json TEXT NOT NULL,countries_json TEXT NOT NULL,employment_type TEXT NOT NULL,sources_json TEXT NOT NULL,updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS portal_resume_analysis(tenant_id TEXT PRIMARY KEY REFERENCES portal_tenants(id),document_id TEXT NOT NULL REFERENCES portal_documents(id),status TEXT NOT NULL,error TEXT,updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS portal_onboarding_answers(tenant_id TEXT NOT NULL REFERENCES portal_tenants(id),question_key TEXT NOT NULL,answer TEXT NOT NULL,updated_at TEXT NOT NULL,PRIMARY KEY(tenant_id,question_key));
CREATE TABLE IF NOT EXISTS portal_agent_proposals(tenant_id TEXT PRIMARY KEY REFERENCES portal_tenants(id),summary TEXT NOT NULL,preferences_json TEXT NOT NULL,updated_at TEXT NOT NULL);
"""


def _now() -> datetime:
    return datetime.now(UTC)


def _hash(value: str) -> str:
    return sha256(value.encode()).hexdigest()


class PortalStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
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

    def issue_login(self, email: str, *, ttl_seconds: int = 900) -> str | None:
        normalized = email.strip().casefold()
        if "@" not in normalized or len(normalized) > 254:
            raise ValueError("A valid email is required")
        token = secrets.token_urlsafe(32)
        with self.connect() as connection:
            owner = connection.execute(
                "SELECT email FROM portal_users ORDER BY created_at LIMIT 1"
            ).fetchone()
            if owner is not None and str(owner["email"]) != normalized:
                return None
            recent = connection.execute(
                "SELECT 1 FROM portal_login_tokens WHERE email=? AND expires_at>? LIMIT 1",
                (normalized, (_now() + timedelta(seconds=840)).isoformat()),
            ).fetchone()
            if recent is not None:
                return None
            connection.execute(
                "INSERT INTO portal_login_tokens VALUES (?,?,?,NULL)",
                (_hash(token), normalized, (_now() + timedelta(seconds=ttl_seconds)).isoformat()),
            )
        return token

    def redeem_login(self, token: str) -> tuple[str, str]:
        now = _now()
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT email FROM portal_login_tokens WHERE token_hash=? AND used_at IS NULL AND expires_at>?",
                (_hash(token), now.isoformat()),
            ).fetchone()
            if row is None:
                raise ValueError("Login link is invalid or expired")
            email = str(row["email"])
            user = connection.execute(
                "SELECT id FROM portal_users WHERE email=?", (email,)
            ).fetchone()
            if user is None:
                user_id, tenant_id = secrets.token_urlsafe(18), secrets.token_urlsafe(18)
                connection.execute(
                    "INSERT INTO portal_users VALUES (?,?,?)", (user_id, email, now.isoformat())
                )
                connection.execute(
                    "INSERT INTO portal_tenants VALUES (?,?)", (tenant_id, now.isoformat())
                )
                connection.execute(
                    "INSERT INTO portal_memberships VALUES (?,?,?)", (user_id, tenant_id, "owner")
                )
                connection.execute(
                    "INSERT INTO portal_onboarding VALUES (?,?,?)",
                    (tenant_id, "account_verified", now.isoformat()),
                )
            else:
                user_id = str(user["id"])
                tenant_id = str(
                    connection.execute(
                        "SELECT tenant_id FROM portal_memberships WHERE user_id=?", (user_id,)
                    ).fetchone()[0]
                )
            connection.execute(
                "UPDATE portal_login_tokens SET used_at=? WHERE token_hash=?",
                (now.isoformat(), _hash(token)),
            )
        return user_id, tenant_id

    def create_session(self, user_id: str, tenant_id: str) -> tuple[str, str]:
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO portal_sessions VALUES (?,?,?,?,?)",
                (_hash(token), user_id, tenant_id, csrf, (_now() + timedelta(days=30)).isoformat()),
            )
        return token, csrf

    def session(self, token: str) -> dict[str, str] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT s.user_id,s.tenant_id,s.csrf_token,u.email FROM portal_sessions s JOIN portal_users u ON u.id=s.user_id WHERE s.token_hash=? AND s.expires_at>?",
                (_hash(token), _now().isoformat()),
            ).fetchone()
        return dict(row) if row else None

    def revoke_session(self, token: str) -> None:
        with self.connect() as connection:
            connection.execute("DELETE FROM portal_sessions WHERE token_hash=?", (_hash(token),))

    def state(self, tenant_id: str) -> str:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT state FROM portal_onboarding WHERE tenant_id=?", (tenant_id,)
            ).fetchone()
        if row is None:
            raise ValueError("Unknown tenant")
        return str(row[0])

    def dashboard_access_granted(self, tenant_id: str) -> bool:
        """Return whether the required profile onboarding transaction completed."""
        return ONBOARDING_STATES.index(self.state(tenant_id)) >= ONBOARDING_STATES.index(
            "integrations_configured"
        )

    def advance(self, tenant_id: str, state: str) -> None:
        if state not in ONBOARDING_STATES:
            raise ValueError("Unknown onboarding state")
        with self.connect() as connection:
            current = self.state(tenant_id)
            current_index = ONBOARDING_STATES.index(current)
            target_index = ONBOARDING_STATES.index(state)
            if target_index not in {current_index, current_index + 1}:
                raise ValueError("Onboarding steps must be completed in order")
            connection.execute(
                "UPDATE portal_onboarding SET state=?,updated_at=? WHERE tenant_id=?",
                (state, _now().isoformat(), tenant_id),
            )

    def add_resume(
        self,
        tenant_id: str,
        *,
        storage_key: str,
        digest: str,
        media_type: str,
        size: int,
        text: str,
    ) -> str:
        document_id = secrets.token_urlsafe(18)
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO portal_documents VALUES (?,?,?,?,?,?,?,?)",
                (
                    document_id,
                    tenant_id,
                    storage_key,
                    digest,
                    media_type,
                    size,
                    text,
                    _now().isoformat(),
                ),
            )
        with self.connect() as connection:
            connection.execute(
                "UPDATE portal_onboarding SET state='resume_uploaded',updated_at=? WHERE tenant_id=?",
                (_now().isoformat(), tenant_id),
            )
            connection.execute(
                """INSERT INTO portal_resume_analysis VALUES (?,?,'pending',NULL,?)
                ON CONFLICT(tenant_id) DO UPDATE SET document_id=excluded.document_id,
                status='pending',error=NULL,updated_at=excluded.updated_at""",
                (tenant_id, document_id, _now().isoformat()),
            )
        return document_id

    def replace_analysis(
        self,
        tenant_id: str,
        document_id: str,
        *,
        claims: list[dict[str, object]],
        tracks: list[dict[str, object]],
    ) -> None:
        with self.connect() as connection:
            connection.execute("DELETE FROM portal_evidence WHERE tenant_id=?", (tenant_id,))
            connection.execute("DELETE FROM portal_tracks WHERE tenant_id=?", (tenant_id,))
            for claim in claims:
                connection.execute(
                    "INSERT INTO portal_evidence VALUES (?,?,?,?,?,?,?)",
                    (
                        secrets.token_urlsafe(18),
                        tenant_id,
                        document_id,
                        str(claim["text"]),
                        str(claim["source_ref"]),
                        "proposed",
                        _now().isoformat(),
                    ),
                )
            for track in tracks:
                connection.execute(
                    "INSERT INTO portal_tracks VALUES (?,?,?,?,?,?,?)",
                    (
                        secrets.token_urlsafe(18),
                        tenant_id,
                        str(track["key"]),
                        str(track["label"]),
                        str(track.get("mode", "core")),
                        json.dumps(track.get("keywords", [])),
                        "proposed",
                    ),
                )
            connection.execute(
                "UPDATE portal_resume_analysis SET status='ready',error=NULL,updated_at=? WHERE tenant_id=? AND document_id=?",
                (_now().isoformat(), tenant_id, document_id),
            )

    def mark_analysis_failed(self, tenant_id: str, document_id: str, error: str) -> None:
        bounded_error = " ".join(error.split())[-1000:]
        with self.connect() as connection:
            connection.execute(
                "UPDATE portal_resume_analysis SET status='failed',error=?,updated_at=? WHERE tenant_id=? AND document_id=?",
                (
                    bounded_error or "analysis unavailable",
                    _now().isoformat(),
                    tenant_id,
                    document_id,
                ),
            )

    def latest_resume_text(self, tenant_id: str) -> str:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT extracted_text FROM portal_documents WHERE tenant_id=? ORDER BY created_at DESC LIMIT 1",
                (tenant_id,),
            ).fetchone()
        if row is None:
            raise ValueError("Upload a resume first")
        return str(row[0])

    def restart_analysis(self, tenant_id: str) -> tuple[str, str]:
        """Recreate analysis state from retained source documents."""
        with self.connect() as connection:
            rows = list(
                connection.execute(
                    "SELECT id,extracted_text FROM portal_documents WHERE tenant_id=? ORDER BY created_at",
                    (tenant_id,),
                )
            )
            if not rows:
                raise ValueError("Upload a resume first")
            document_id = str(rows[-1]["id"])
            connection.execute(
                """INSERT INTO portal_resume_analysis VALUES (?,?,'pending',NULL,?)
                ON CONFLICT(tenant_id) DO UPDATE SET document_id=excluded.document_id,
                status='pending',error=NULL,updated_at=excluded.updated_at""",
                (tenant_id, document_id, _now().isoformat()),
            )
        return document_id, "\n\n".join(str(row["extracted_text"]) for row in rows)

    def save_answer(self, tenant_id: str, question_key: str, answer: str) -> None:
        cleaned = answer.strip()
        if not cleaned or len(cleaned) > 2000:
            raise ValueError("Answer must be between 1 and 2000 characters")
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO portal_onboarding_answers VALUES (?,?,?,?)
                ON CONFLICT(tenant_id,question_key) DO UPDATE SET
                answer=excluded.answer,updated_at=excluded.updated_at""",
                (tenant_id, question_key, cleaned, _now().isoformat()),
            )

    def answers(self, tenant_id: str) -> dict[str, str]:
        with self.connect() as connection:
            rows = list(
                connection.execute(
                    "SELECT question_key,answer FROM portal_onboarding_answers WHERE tenant_id=?",
                    (tenant_id,),
                )
            )
        return {str(row["question_key"]): str(row["answer"]) for row in rows}

    def replace_agent_proposal(
        self,
        tenant_id: str,
        *,
        tracks: list[dict[str, object]],
        summary: str,
        preferences: dict[str, object],
    ) -> None:
        with self.connect() as connection:
            connection.execute("DELETE FROM portal_tracks WHERE tenant_id=?", (tenant_id,))
            for track in tracks:
                connection.execute(
                    "INSERT INTO portal_tracks VALUES (?,?,?,?,?,?,?)",
                    (
                        secrets.token_urlsafe(18),
                        tenant_id,
                        str(track["key"]),
                        str(track["label"]),
                        str(track.get("mode", "core")),
                        json.dumps(track.get("keywords", [])),
                        "proposed",
                    ),
                )
            connection.execute(
                """INSERT INTO portal_agent_proposals VALUES (?,?,?,?)
                ON CONFLICT(tenant_id) DO UPDATE SET summary=excluded.summary,
                preferences_json=excluded.preferences_json,updated_at=excluded.updated_at""",
                (tenant_id, summary.strip(), json.dumps(preferences), _now().isoformat()),
            )

    def snapshot(self, tenant_id: str) -> dict[str, object]:
        with self.connect() as connection:
            evidence = [
                dict(r)
                for r in connection.execute(
                    "SELECT id,claim_text,source_ref,status FROM portal_evidence WHERE tenant_id=? ORDER BY created_at",
                    (tenant_id,),
                )
            ]
            tracks = [
                dict(r)
                for r in connection.execute(
                    "SELECT id,track_key,label,mode,keywords_json,status FROM portal_tracks WHERE tenant_id=? ORDER BY label",
                    (tenant_id,),
                )
            ]
            documents = [
                dict(r)
                for r in connection.execute(
                    "SELECT id,media_type,size,created_at FROM portal_documents WHERE tenant_id=? ORDER BY created_at DESC",
                    (tenant_id,),
                )
            ]
            row = connection.execute(
                "SELECT locations_json,languages_json,countries_json,employment_type,sources_json FROM portal_preferences WHERE tenant_id=?",
                (tenant_id,),
            ).fetchone()
            analysis = connection.execute(
                "SELECT status,error FROM portal_resume_analysis WHERE tenant_id=?", (tenant_id,)
            ).fetchone()
            proposal = connection.execute(
                "SELECT summary,preferences_json FROM portal_agent_proposals WHERE tenant_id=?",
                (tenant_id,),
            ).fetchone()
        return {
            "state": self.state(tenant_id),
            "evidence": evidence,
            "tracks": tracks,
            "documents": documents,
            "preferences": dict(row) if row else None,
            "analysis_status": str(analysis[0]) if analysis else None,
            "analysis_error": str(analysis[1]) if analysis and analysis[1] else None,
            "answers": self.answers(tenant_id),
            "proposal": dict(proposal) if proposal else None,
        }

    def approve(self, tenant_id: str, kind: str) -> None:
        table = {"evidence": "portal_evidence", "tracks": "portal_tracks"}.get(kind)
        if table is None:
            raise ValueError("Unknown approval kind")
        expected = "resume_uploaded" if kind == "evidence" else "evidence_approved"
        if self.state(tenant_id) != expected:
            raise ValueError(f"Complete {expected} before approving {kind}")
        with self.connect() as connection:
            connection.execute(
                f"UPDATE {table} SET status='approved' WHERE tenant_id=?", (tenant_id,)
            )
        self.advance(tenant_id, "evidence_approved" if kind == "evidence" else "tracks_approved")

    def save_preferences(
        self,
        tenant_id: str,
        *,
        locations: list[str],
        languages: list[str],
        countries: list[str],
        employment_type: str,
        sources: list[str],
    ) -> None:
        if not locations or not languages or not countries or not sources:
            raise ValueError("Locations, languages, countries, and sources are required")
        if employment_type not in {"full_time", "part_time", "any"}:
            raise ValueError("Unknown employment type")
        if self.state(tenant_id) != "tracks_approved":
            raise ValueError("Approve tracks before saving search settings")
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO portal_preferences VALUES (?,?,?,?,?,?,?)
                ON CONFLICT(tenant_id) DO UPDATE SET
                locations_json=excluded.locations_json,languages_json=excluded.languages_json,
                countries_json=excluded.countries_json,employment_type=excluded.employment_type,
                sources_json=excluded.sources_json,updated_at=excluded.updated_at""",
                (
                    tenant_id,
                    json.dumps(locations),
                    json.dumps(languages),
                    json.dumps(countries),
                    employment_type,
                    json.dumps(sources),
                    _now().isoformat(),
                ),
            )
        self.advance(tenant_id, "integrations_configured")
