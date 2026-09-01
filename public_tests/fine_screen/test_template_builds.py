"""The shipped workspace template must actually build a PDF.

Every other test reads the template as text. None of them would have caught a
LaTeX error in it -- the first run on a real machine did, because a stray
single backslash turned `\\\\[2pt]` into `\\[`, which opens display math and
took the whole document down.

Requires tectonic, so it skips where that is absent rather than failing.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from fine_screen.screen import build_pdf, find_tectonic

pytestmark = pytest.mark.skipif(
    shutil.which("tectonic") is None,
    reason="tectonic is not installed; the template build cannot be checked here",
)


def test_the_blank_variant_builds(workspace_root: Path, tmp_path: Path):
    source = workspace_root / "resume" / "variants" / "blank.tex"
    tectonic = find_tectonic(workspace_root)
    assert tectonic is not None

    built = build_pdf(tectonic, source, tmp_path / "build", tmp_path / "blank.pdf")

    if not built:
        log = tmp_path / "build" / "blank.log"
        pytest.fail(log.read_text(errors="replace")[-2000:] if log.exists() else "no log written")
    assert (tmp_path / "blank.pdf").stat().st_size > 0
