from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from job_scraper.adapters.storage.sqlite_v2 import WorkspaceDatabase
from job_scraper.application.screening_results import load_screening_results
from job_scraper.cli import database as database_cli


def _write_result(path: Path, **record_overrides: object) -> None:
    record = {
        "job_id": "legacy-1",
        "profile_id": "core_track",
        "processing_mode": "core",
        "status": "evaluated",
        "selected": True,
        "score": 0.84,
        "core_fit": "direct",
        "variant": "cpp-core",
        "true_gap": ["one honest gap"],
        "rationale": "Fictional rationale.",
        "decision_source": "agent",
        "tailoring_status": "ready",
    }
    record.update(record_overrides)
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "contract_version": "screening:test;tailoring:test",
                "generated_at": "2026-08-28T12:00:00+00:00",
                "record_count": 1,
                "records": [record],
            }
        ),
        encoding="utf-8",
    )


def test_result_document_is_validated_before_import(tmp_path: Path) -> None:
    path = tmp_path / "results.json"
    _write_result(path)

    result = load_screening_results(path)[0]

    assert result.processing_mode == "core"
    assert result.true_gap == ("one honest gap",)

    _write_result(path, processing_mode="unsupported")
    with pytest.raises(ValueError, match="processing_mode"):
        load_screening_results(path)


def test_result_persistence_is_idempotent_and_requires_crosswalk(tmp_path: Path) -> None:
    path = tmp_path / "results.json"
    _write_result(path)
    result = load_screening_results(path)[0]
    workspace = WorkspaceDatabase(tmp_path / "workspace.db")
    workspace.initialize()

    with pytest.raises(ValueError, match="crosswalk"):
        workspace.record_screening_result(result)

    connection = sqlite3.connect(workspace.path)
    try:
        connection.execute(
            """INSERT INTO canonical_jobs VALUES (
            'canonical-1', 'identity-1', 'role', 'Example', 'DE', 'Berlin', 'Berlin',
            'Description', 'full-time', '', '', '',
            '2026-08-28T00:00:00+00:00', '2026-08-28T00:00:00+00:00')"""
        )
        connection.execute(
            "INSERT INTO legacy_job_links VALUES ('core_track', 'legacy-1', 'canonical-1', '')"
        )
        connection.commit()
    finally:
        connection.close()

    workspace.record_screening_result(result)
    workspace.record_screening_result(result)

    connection = sqlite3.connect(workspace.path)
    try:
        assert connection.execute("SELECT COUNT(*) FROM screening_results").fetchone()[0] == 1
    finally:
        connection.close()


def test_batch_persistence_rolls_back_when_any_crosswalk_is_missing(tmp_path: Path) -> None:
    path = tmp_path / "results.json"
    _write_result(path)
    valid = load_screening_results(path)[0]
    invalid = replace(valid, legacy_job_id="missing")
    workspace = WorkspaceDatabase(tmp_path / "workspace.db")
    workspace.initialize()
    connection = sqlite3.connect(workspace.path)
    try:
        connection.execute(
            """INSERT INTO canonical_jobs VALUES (
            'canonical-1', 'identity-1', 'role', 'Example', 'DE', 'Berlin', 'Berlin',
            'Description', 'full-time', '', '', '',
            '2026-08-28T00:00:00+00:00', '2026-08-28T00:00:00+00:00')"""
        )
        connection.execute(
            "INSERT INTO legacy_job_links VALUES ('core_track', 'legacy-1', 'canonical-1', '')"
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(ValueError, match="crosswalk"):
        workspace.record_screening_results((valid, invalid))

    connection = sqlite3.connect(workspace.path)
    try:
        assert connection.execute("SELECT COUNT(*) FROM screening_results").fetchone()[0] == 0
    finally:
        connection.close()


def test_cli_import_rejects_stale_profile_policy(monkeypatch, tmp_path: Path) -> None:
    path = tmp_path / "results.json"
    _write_result(path)
    workspace = WorkspaceDatabase(tmp_path / "workspace.db")
    workspace.initialize()
    monkeypatch.setattr(
        database_cli,
        "load_profile_definition",
        lambda _profile_id: SimpleNamespace(processing_mode="discovery"),
    )

    assert database_cli.import_screening_results([path], workspace.path) == 2
