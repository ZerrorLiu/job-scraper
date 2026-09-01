from __future__ import annotations

from pathlib import Path

import pytest

from fine_screen.screen import load_variants
from fine_screen.workspace import load_workspace
from job_scraper.adapters.workspace_bootstrap import initialize_candidate_workspace


def test_workspace_bootstrap_creates_truthful_private_runtime(tmp_path: Path) -> None:
    root = tmp_path / "candidate"
    initialize_candidate_workspace(
        root,
        candidate_name="Fictional Person",
        email="person@example.test",
        resume_text="C++ & Qt engineer\nBuilt a device with 20% lower latency.",
        original_bytes=b"%PDF fictional",
        original_suffix=".pdf",
    )
    workspace = load_workspace(root)
    assert workspace.candidate.name == "Fictional Person"
    variant = (root / "resume/variants/imported.tex").read_text(encoding="utf-8")
    assert variant.count("% FINE_SCREEN_SKILLS_INSERTION_POINT") == 1
    assert r"C++ \& Qt engineer" in variant
    assert set(load_variants(root)) == {"imported"}
    assert (root / "resume/source/original.pdf").read_bytes() == b"%PDF fictional"


def test_workspace_bootstrap_refuses_unknown_existing_contents(tmp_path: Path) -> None:
    root = tmp_path / "candidate"
    root.mkdir()
    (root / "unrelated.txt").write_text("keep", encoding="utf-8")
    with pytest.raises(ValueError, match="non-empty"):
        initialize_candidate_workspace(
            root,
            candidate_name="Fictional Person",
            email="person@example.test",
            resume_text="Resume evidence",
            original_bytes=b"x",
            original_suffix=".pdf",
        )
