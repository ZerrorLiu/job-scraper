from pathlib import Path

from job_scraper.application.application_runtime import ApplicationRuntime
from job_scraper.application.session_state import (
    ApplicationSessionState,
    load_session_state,
    save_session_state,
)
from job_scraper.browser.session import _approved_resume, _private_identity


def runtime(tmp_path: Path) -> ApplicationRuntime:
    return ApplicationRuntime(
        root=tmp_path,
        workspace_database=tmp_path / "workspace.db",
        facts_file=tmp_path / "facts.json",
        policies_file=tmp_path / "policies.toml",
        documents_dir=tmp_path / "documents",
        browser_profile_dir=tmp_path / "browser-profile",
        evidence_dir=tmp_path / "evidence",
        session_state_file=tmp_path / "session-state.json",
        browser_debug_port=9222,
        browser_channel="chromium",
    )


def test_session_state_round_trips_without_secret_fields(tmp_path: Path) -> None:
    current = ApplicationSessionState(
        status="waiting_for_human",
        canonical_job_id="fictional-job",
        platform="linkedin",
        requested_url="https://www.linkedin.com/jobs/view/fictional-job",
        current_url="https://www.linkedin.com/authwall",
        step="login",
        human_action="complete login in the visible browser",
    )

    save_session_state(runtime(tmp_path), current)
    restored = load_session_state(runtime(tmp_path))

    assert restored == current
    assert "password" not in runtime(tmp_path).session_state_file.read_text(encoding="utf-8")
    assert "cookie" not in runtime(tmp_path).session_state_file.read_text(encoding="utf-8")


def test_private_facts_array_maps_identity_and_approved_resume(tmp_path: Path) -> None:
    resume = tmp_path / "documents" / "resume.pdf"
    resume.parent.mkdir()
    resume.write_bytes(b"fictional resume")
    payload = {
        "facts": [
            {"id": "identity.full_name", "value": "Ada Example"},
            {"id": "identity.email", "value": "ada@example.test"},
        ],
        "approved_documents": [
            {
                "id": "resume.example",
                "path": "documents/resume.pdf",
                "purpose": "resume",
                "status": "approved",
            }
        ],
    }

    assert _private_identity(payload) == {
        "full_name": "Ada Example",
        "email": "ada@example.test",
    }
    assert _approved_resume(runtime(tmp_path), payload) == resume
