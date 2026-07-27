from __future__ import annotations

import json
from pathlib import Path

import pytest

from job_scraper.application.application_doctor import inspect_application_runtime
from job_scraper.application.application_runtime import (
    ApplicationRuntime,
    RuntimeConfigurationError,
)


def test_runtime_rejects_a_git_worktree(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    with pytest.raises(RuntimeConfigurationError, match="outside every Git worktree"):
        ApplicationRuntime.from_environment(root=tmp_path)


def test_doctor_checks_private_runtime_and_reports_browser_channel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "facts.json").write_text(json.dumps({"fictional": True}), encoding="utf-8")
    (tmp_path / "policies.toml").write_text("[application]\n", encoding="utf-8")
    (tmp_path / "documents").mkdir()
    (tmp_path / "browser-profile").mkdir()
    runtime = ApplicationRuntime.from_environment(root=tmp_path)

    checks = inspect_application_runtime(runtime)

    assert {check.name for check in checks} == {
        "runtime outside Git",
        "runtime directory",
        "workspace database",
        "facts file",
        "policies file",
        "documents directory",
        "browser profile directory",
        "evidence directory",
        "browser channel",
    }
    assert next(check for check in checks if check.name == "browser channel").ok
