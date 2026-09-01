"""The workspace contract.

Everything personal is supposed to live outside this package. These tests pin
the boundary: what a workspace must supply, and what happens when it does not.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from fine_screen.workspace import Candidate, WorkspaceError, load_workspace


def _write(root: Path, body: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "workspace.toml").write_text(body, encoding="utf-8")
    return root


def test_a_workspace_reports_its_candidate(workspace):
    assert workspace.candidate.name == "Alex Fictional"
    assert workspace.candidate.file_slug == "AlexFictional"
    assert workspace.candidate.legacy_file_slugs == ("Alex_Fictional",)


def test_paths_hang_off_the_root(workspace):
    root = workspace.root
    assert workspace.resume_variants_dir == root / "resume" / "variants"
    assert workspace.skills_whitelist_path == root / "shared" / "quick-learn-skills.toml"
    assert workspace.profile_notes_path == root / "shared" / "profile-notes.md"
    assert workspace.output_pdf_dir == root / "CV" / "Fine-Screened"
    assert workspace.generated_variants_dir == root / "resume" / "variants" / "applications"


def test_the_shipped_template_is_a_valid_workspace(workspace):
    """Whatever we hand a new user must satisfy our own checks."""
    workspace.require_inputs()


def test_a_missing_config_names_the_template(tmp_path):
    with pytest.raises(WorkspaceError, match="templates/workspace"):
        load_workspace(tmp_path)


def test_malformed_toml_is_reported_as_such(tmp_path):
    root = _write(tmp_path / "ws", "[candidate\nname =")

    with pytest.raises(WorkspaceError, match="not valid TOML"):
        load_workspace(root)


def test_a_config_without_a_candidate_table_is_refused(tmp_path):
    root = _write(tmp_path / "ws", '[other]\nname = "x"\n')

    with pytest.raises(WorkspaceError, match=r"\[candidate\]"):
        load_workspace(root)


def test_a_missing_required_field_names_it(tmp_path):
    root = _write(tmp_path / "ws", '[candidate]\nname = "Alex"\n')

    with pytest.raises(WorkspaceError, match="file_slug"):
        load_workspace(root)


def test_an_unknown_candidate_field_is_refused(tmp_path):
    """A typo that silently ran on defaults would name real files wrongly."""
    root = _write(
        tmp_path / "ws",
        '[candidate]\nname = "Alex"\nfile_slug = "Alex"\nfile_slugs = ["oops"]\n',
    )

    with pytest.raises(WorkspaceError, match="file_slugs"):
        load_workspace(root)


@pytest.mark.parametrize("slug", ["has space", "has/slash", "-leading", "dots.dots", ""])
def test_a_slug_that_would_break_a_filename_is_refused(slug):
    with pytest.raises(WorkspaceError, match="filename"):
        Candidate(name="Alex", file_slug=slug)


def test_an_empty_name_is_refused():
    with pytest.raises(WorkspaceError, match="name"):
        Candidate(name="   ", file_slug="Alex")


def test_a_legacy_slug_is_validated_too():
    """Legacy slugs are matched against real filenames, so they must be valid."""
    with pytest.raises(WorkspaceError, match="filename"):
        Candidate(name="Alex", file_slug="Alex", legacy_file_slugs=("bad name",))


def test_missing_inputs_are_listed_before_any_work(tmp_path):
    root = _write(tmp_path / "ws", '[candidate]\nname = "Alex"\nfile_slug = "Alex"\n')
    workspace = load_workspace(root)

    with pytest.raises(WorkspaceError) as caught:
        workspace.require_inputs()

    message = str(caught.value)
    assert "resume" in message and "quick-learn-skills.toml" in message


def test_every_input_the_run_needs_is_checked_up_front(tmp_path, workspace_root):
    """A file needed only by tailoring must still fail before the agent runs.

    The evidence library is read late, so leaving it out of this check meant a
    real deployment got through screening and died at the tailoring step with a
    bare FileNotFoundError -- after the paid agent calls.
    """
    (workspace_root / "shared" / "evidence-library.json").unlink()
    workspace = load_workspace(workspace_root)

    with pytest.raises(WorkspaceError, match=r"evidence-library\.json"):
        workspace.require_inputs()
