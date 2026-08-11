from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


class RuntimeConfigurationError(ValueError):
    """Raised when the private application runtime is unsafe or incomplete."""


@dataclass(frozen=True, slots=True)
class ApplicationRuntime:
    root: Path
    workspace_database: Path
    facts_file: Path
    policies_file: Path
    documents_dir: Path
    browser_profile_dir: Path
    evidence_dir: Path
    session_state_file: Path
    browser_debug_port: int
    browser_channel: str

    @classmethod
    def from_environment(cls, *, root: Path | None = None) -> ApplicationRuntime:
        configured_root = root or _environment_path("POSITIONS_APPLY_WORKSPACE")
        if configured_root is None:
            raise RuntimeConfigurationError(
                "POSITIONS_APPLY_WORKSPACE is required and must point outside the Git worktree"
            )
        resolved_root = configured_root.expanduser().resolve()
        _assert_outside_git_worktree(resolved_root)
        workspace_database = _environment_path("JOB_WORKSPACE_DATABASE") or Path(
            "data/workspace.db"
        )
        browser_channel = os.environ.get("POSITIONS_APPLY_BROWSER_CHANNEL", "chrome").strip()
        if browser_channel not in {"chrome", "msedge", "chromium"}:
            raise RuntimeConfigurationError(
                "POSITIONS_APPLY_BROWSER_CHANNEL must be chrome, msedge, or chromium"
            )
        try:
            browser_debug_port = int(os.environ.get("POSITIONS_APPLY_BROWSER_DEBUG_PORT", "9222"))
        except ValueError as exc:
            raise RuntimeConfigurationError(
                "POSITIONS_APPLY_BROWSER_DEBUG_PORT must be an integer"
            ) from exc
        if not 1024 <= browser_debug_port <= 65535:
            raise RuntimeConfigurationError(
                "POSITIONS_APPLY_BROWSER_DEBUG_PORT must be between 1024 and 65535"
            )
        return cls(
            root=resolved_root,
            workspace_database=workspace_database.expanduser().resolve(),
            facts_file=resolved_root / "facts.json",
            policies_file=resolved_root / "policies.toml",
            documents_dir=resolved_root / "documents",
            browser_profile_dir=resolved_root / "browser-profile",
            evidence_dir=resolved_root / "evidence",
            session_state_file=resolved_root / "session-state.json",
            browser_debug_port=browser_debug_port,
            browser_channel=browser_channel,
        )


def _environment_path(name: str) -> Path | None:
    value = os.environ.get(name, "").strip()
    return Path(value) if value else None


def _assert_outside_git_worktree(path: Path) -> None:
    for ancestor in (path, *path.parents):
        if (ancestor / ".git").exists():
            raise RuntimeConfigurationError(
                "Application runtime must be outside every Git worktree: " + str(path)
            )
