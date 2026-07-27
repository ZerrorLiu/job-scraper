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
    cdp_url: str

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
        cdp_url = os.environ.get("POSITIONS_APPLY_CDP_URL", "http://127.0.0.1:9222").strip()
        _validate_cdp_url(cdp_url)
        return cls(
            root=resolved_root,
            workspace_database=workspace_database.expanduser().resolve(),
            facts_file=resolved_root / "facts.json",
            policies_file=resolved_root / "policies.toml",
            documents_dir=resolved_root / "documents",
            browser_profile_dir=resolved_root / "browser-profile",
            evidence_dir=resolved_root / "evidence",
            cdp_url=cdp_url.rstrip("/"),
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


def _validate_cdp_url(value: str) -> None:
    from urllib.parse import urlparse

    parsed = urlparse(value)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise RuntimeConfigurationError(
            "POSITIONS_APPLY_CDP_URL must use local HTTP, for example http://127.0.0.1:9222"
        )
