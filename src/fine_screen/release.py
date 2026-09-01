"""Version and input-integrity gates for private fine-screen workspaces.

The public ``fine-screen`` package and a candidate's private workspace are
separate Git repositories. A release manifest binds one clean revision of each
repository to exact hashes of every resume input that can change a generated
application. VPS runners verify the manifest before an ``--apply`` run.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fine_screen.workspace import Workspace, WorkspaceError, load_workspace

MANIFEST_SCHEMA_VERSION = 1


class ReleaseError(ValueError):
    """A release manifest cannot safely be written or verified."""


@dataclass(frozen=True)
class ReleaseIdentity:
    code_revision: str
    workspace_revision: str
    workspace_manifest_sha256: str


def package_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _git_output(root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *args],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if completed.returncode != 0:
        raise ReleaseError(
            f"git {' '.join(args)} failed in {root}: {completed.stderr.strip() or '(no stderr)'}"
        )
    return completed.stdout.strip()


def clean_git_revision(root: Path, label: str) -> str:
    revision = _git_output(root, "rev-parse", "HEAD")
    if not revision:
        raise ReleaseError(f"{label} has no Git revision")
    if _git_output(root, "status", "--porcelain"):
        raise ReleaseError(f"{label} working tree is not clean")
    return revision


def _relative_files(workspace: Workspace) -> list[Path]:
    files = [
        workspace.root / "workspace.toml",
        workspace.skills_whitelist_path,
        workspace.profile_notes_path,
        workspace.evidence_library_path,
    ]
    files.extend(sorted(workspace.resume_variants_dir.glob("*.tex")))
    for directory in (
        workspace.resume_shared_dir,
        workspace.shared_dir / "assets",
        workspace.root / "assets",
    ):
        if directory.is_dir():
            files.extend(sorted(path for path in directory.rglob("*") if path.is_file()))
    unique: dict[Path, Path] = {}
    for path in files:
        resolved = path.resolve()
        if not resolved.is_file():
            raise ReleaseError(f"workspace release input is missing: {path}")
        try:
            relative = resolved.relative_to(workspace.root.resolve())
        except ValueError as exc:
            raise ReleaseError(f"workspace release input escapes workspace: {path}") from exc
        unique[relative] = resolved
    return [unique[key] for key in sorted(unique)]


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def _workspace_files(workspace: Workspace) -> dict[str, str]:
    root = workspace.root.resolve()
    return {
        path.relative_to(root).as_posix(): _sha256_file(path) for path in _relative_files(workspace)
    }


def build_manifest(workspace: Workspace, code_root: Path | None = None) -> dict[str, Any]:
    code_root = (code_root or package_root()).resolve()
    workspace_root = workspace.root.resolve()
    files = _workspace_files(workspace)
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "code_revision": clean_git_revision(code_root, "fine-screen code repository"),
        "workspace_revision": clean_git_revision(workspace_root, "private workspace repository"),
        "workspace_files": files,
        "workspace_manifest_sha256": hashlib.sha256(_canonical_json(files)).hexdigest(),
    }


def write_manifest(
    workspace: Workspace, path: Path, code_root: Path | None = None
) -> ReleaseIdentity:
    manifest = build_manifest(workspace, code_root)
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return identity_from_manifest(manifest)


def identity_from_manifest(manifest: dict[str, Any]) -> ReleaseIdentity:
    required = {
        "schema_version",
        "code_revision",
        "workspace_revision",
        "workspace_files",
        "workspace_manifest_sha256",
    }
    if set(manifest) != required:
        raise ReleaseError("release manifest has unsupported or missing fields")
    if manifest["schema_version"] != MANIFEST_SCHEMA_VERSION:
        raise ReleaseError(f"unsupported release manifest schema {manifest['schema_version']!r}")
    string_fields = {"code_revision", "workspace_revision", "workspace_manifest_sha256"}
    if not all(isinstance(manifest[key], str) and manifest[key] for key in string_fields):
        raise ReleaseError("release manifest revisions and digest must be non-empty strings")
    if not isinstance(manifest["workspace_files"], dict) or not manifest["workspace_files"]:
        raise ReleaseError("release manifest must contain workspace file hashes")
    return ReleaseIdentity(
        code_revision=manifest["code_revision"],
        workspace_revision=manifest["workspace_revision"],
        workspace_manifest_sha256=manifest["workspace_manifest_sha256"],
    )


def verify_manifest(
    workspace: Workspace, path: Path, code_root: Path | None = None
) -> ReleaseIdentity:
    try:
        manifest = json.loads(path.expanduser().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReleaseError(f"cannot read release manifest {path}: {exc}") from exc
    if not isinstance(manifest, dict):
        raise ReleaseError("release manifest must be a JSON object")
    identity = identity_from_manifest(manifest)
    expected = build_manifest(workspace, code_root)
    if manifest != expected:
        raise ReleaseError(
            "release manifest does not match the checked-out code or workspace inputs"
        )
    return identity


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("command", choices=("write", "verify"))
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        workspace = load_workspace(args.workspace)
        workspace.require_inputs()
        if args.command == "write":
            identity = write_manifest(workspace, args.manifest)
        else:
            identity = verify_manifest(workspace, args.manifest)
    except (WorkspaceError, ReleaseError) as exc:
        print(f"ERROR {exc}")
        return 2
    print(
        "Release manifest verified: "
        f"code={identity.code_revision} workspace={identity.workspace_revision} "
        f"inputs={identity.workspace_manifest_sha256}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
