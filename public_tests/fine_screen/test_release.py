from __future__ import annotations

import json

import pytest

from fine_screen import release


def _stable_revisions(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_revision(root, label: str) -> str:  # type: ignore[no-untyped-def]
        return "c" * 40 if "code" in label else "w" * 40

    monkeypatch.setattr(release, "clean_git_revision", fake_revision)


def test_manifest_binds_code_workspace_and_resume_inputs(workspace, tmp_path, monkeypatch):  # type: ignore[no-untyped-def]
    _stable_revisions(monkeypatch)

    manifest = release.build_manifest(workspace, code_root=tmp_path)

    assert manifest["code_revision"] == "c" * 40
    assert manifest["workspace_revision"] == "w" * 40
    assert "workspace.toml" in manifest["workspace_files"]
    assert "resume/variants/blank.tex" in manifest["workspace_files"]
    assert "shared/evidence-library.json" in manifest["workspace_files"]


def test_verification_rejects_a_changed_workspace_input(workspace, tmp_path, monkeypatch):  # type: ignore[no-untyped-def]
    _stable_revisions(monkeypatch)
    manifest_path = tmp_path / "release.json"
    manifest_path.write_text(
        json.dumps(release.build_manifest(workspace, code_root=tmp_path)), encoding="utf-8"
    )

    (workspace.resume_variants_dir / "blank.tex").write_text("changed", encoding="utf-8")

    with pytest.raises(release.ReleaseError, match="does not match"):
        release.verify_manifest(workspace, manifest_path, code_root=tmp_path)


def test_manifest_schema_rejects_unknown_fields(workspace, tmp_path, monkeypatch):  # type: ignore[no-untyped-def]
    _stable_revisions(monkeypatch)
    manifest = release.build_manifest(workspace, code_root=tmp_path)
    manifest["unexpected"] = "no"
    path = tmp_path / "release.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(release.ReleaseError, match="unsupported or missing"):
        release.verify_manifest(workspace, path, code_root=tmp_path)
