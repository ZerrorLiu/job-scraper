from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from job_scraper.domain.decisions import ScreeningResult

RESULT_SCHEMA_VERSION = 1
PROCESSING_MODES = frozenset({"core", "review", "discovery"})
RESULT_STATUSES = frozenset({"error", "retained", "no_match", "metadata_blocked", "evaluated"})
TAILORING_STATUSES = frozenset({"not_applicable", "ready", "error", "not_selected"})


def load_screening_results(path: Path) -> tuple[ScreeningResult, ...]:
    """Validate the private screener's result document before persistence."""

    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict) or document.get("schema_version") != RESULT_SCHEMA_VERSION:
        raise ValueError(f"unsupported screening result schema in {path}")
    contract_version = _text(document.get("contract_version"), "contract_version")
    generated_at = _datetime(document.get("generated_at"), "generated_at")
    records = document.get("records")
    if not isinstance(records, list):
        raise ValueError("screening result records must be an array")
    parsed = tuple(
        _parse_record(record, contract_version=contract_version, generated_at=generated_at)
        for record in records
    )
    if int(document.get("record_count", -1)) != len(parsed):
        raise ValueError("screening result record_count does not match records")
    identities = [(result.profile_id, result.legacy_job_id) for result in parsed]
    if len(identities) != len(set(identities)):
        raise ValueError("screening result document contains duplicate profile/job records")
    return parsed


def _parse_record(
    value: object,
    *,
    contract_version: str,
    generated_at: datetime,
) -> ScreeningResult:
    if not isinstance(value, dict):
        raise ValueError("each screening result must be an object")
    processing_mode = _text(value.get("processing_mode"), "processing_mode")
    if processing_mode not in PROCESSING_MODES:
        raise ValueError(f"unsupported processing_mode: {processing_mode}")
    score_value = value.get("score")
    score = None if score_value in (None, "") else float(score_value)
    if score is not None and not 0 <= score <= 1:
        raise ValueError("screening score must be between 0 and 1")
    true_gap = value.get("true_gap", [])
    if not isinstance(true_gap, list) or any(not isinstance(item, str) for item in true_gap):
        raise ValueError("true_gap must be an array of strings")
    status = _choice(value.get("status"), "status", RESULT_STATUSES)
    tailoring_status = _choice(
        value.get("tailoring_status"), "tailoring_status", TAILORING_STATUSES
    )
    if processing_mode != "core" and tailoring_status != "not_applicable":
        raise ValueError("non-core screening results cannot carry a tailoring status")
    return ScreeningResult(
        legacy_job_id=_text(value.get("job_id"), "job_id"),
        profile_id=_text(value.get("profile_id"), "profile_id"),
        processing_mode=processing_mode,
        status=status,
        selected=value.get("selected") is True,
        score=score,
        core_fit=str(value.get("core_fit", "")).strip(),
        variant=str(value.get("variant", "")).strip(),
        true_gap=tuple(item.strip() for item in true_gap if item.strip()),
        rationale=str(value.get("rationale", "")).strip(),
        decision_source=str(value.get("decision_source", "")).strip(),
        tailoring_status=tailoring_status,
        contract_version=contract_version,
        evaluated_at=generated_at,
    )


def _text(value: object, name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{name} must be a non-empty string")
    return text


def _datetime(value: object, name: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(_text(value, name))
    except ValueError as exc:
        raise ValueError(f"{name} must be an ISO datetime") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{name} must include a timezone")
    return parsed


def _choice(value: object, name: str, allowed: frozenset[str]) -> str:
    selected = _text(value, name)
    if selected not in allowed:
        raise ValueError(f"unsupported {name}: {selected}")
    return selected
