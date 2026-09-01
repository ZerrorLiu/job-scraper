"""Shared fixtures.

The candidate here carries a legacy slug on purpose. Cleanup and replacement
have to recognize files written under an earlier naming scheme, and a candidate
with no history would let that path rot untested.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fine_screen.workspace import Candidate, load_workspace

TEMPLATE_ROOT = Path(__file__).resolve().parents[2] / "src" / "fine_screen" / "templates"
WORKSPACE_TEMPLATE = TEMPLATE_ROOT / "workspace"


@pytest.fixture
def candidate() -> Candidate:
    return Candidate(
        name="Alex Fictional",
        file_slug="AlexFictional",
        legacy_file_slugs=("Alex_Fictional",),
    )


@pytest.fixture
def workspace_root(tmp_path: Path, candidate: Candidate) -> Path:
    """A workspace built from the shipped template, with a test identity."""
    root = tmp_path / "workspace"
    root.mkdir()
    for source in WORKSPACE_TEMPLATE.rglob("*"):
        if source.is_dir():
            continue
        destination = root / source.relative_to(WORKSPACE_TEMPLATE)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(source.read_bytes())

    (root / "workspace.toml").write_text(
        "[candidate]\n"
        f'name = "{candidate.name}"\n'
        f'file_slug = "{candidate.file_slug}"\n'
        f"legacy_file_slugs = {json.dumps(list(candidate.legacy_file_slugs))}\n",
        encoding="utf-8",
    )
    return root


@pytest.fixture
def workspace(workspace_root: Path):
    return load_workspace(workspace_root)
