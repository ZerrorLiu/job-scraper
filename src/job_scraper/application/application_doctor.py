from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from job_scraper.application.application_runtime import ApplicationRuntime


@dataclass(frozen=True, slots=True)
class DoctorCheck:
    name: str
    ok: bool
    detail: str


def inspect_application_runtime(runtime: ApplicationRuntime) -> list[DoctorCheck]:
    checks = [
        DoctorCheck("runtime outside Git", _is_directory_safe(runtime.root), str(runtime.root)),
        DoctorCheck("runtime directory", runtime.root.is_dir(), _presence(runtime.root)),
        DoctorCheck(
            "workspace database",
            runtime.workspace_database.is_file(),
            str(runtime.workspace_database),
        ),
        DoctorCheck(
            "facts file", _valid_json_object(runtime.facts_file), _presence(runtime.facts_file)
        ),
        DoctorCheck(
            "policies file", runtime.policies_file.is_file(), _presence(runtime.policies_file)
        ),
        DoctorCheck(
            "documents directory", runtime.documents_dir.is_dir(), _presence(runtime.documents_dir)
        ),
        DoctorCheck(
            "browser profile directory",
            runtime.browser_profile_dir.is_dir(),
            _presence(runtime.browser_profile_dir),
        ),
        DoctorCheck(
            "evidence directory", _ensure_directory(runtime.evidence_dir), str(runtime.evidence_dir)
        ),
        DoctorCheck("browser channel", True, runtime.browser_channel),
    ]
    return checks


def _is_directory_safe(path: Path) -> bool:
    return not any((ancestor / ".git").exists() for ancestor in (path, *path.parents))


def _valid_json_object(path: Path) -> bool:
    if not path.is_file():
        return False
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    return isinstance(value, dict)


def _ensure_directory(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        return False
    return path.is_dir()


def _presence(path: Path) -> str:
    return "present" if path.exists() else "missing: " + str(path)
