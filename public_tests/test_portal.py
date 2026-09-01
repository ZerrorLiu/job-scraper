from __future__ import annotations

import io
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from fastapi.testclient import TestClient

from job_scraper.adapters.server.browser_task_server import create_app
from job_scraper.adapters.storage.browser_task_store import BrowserTaskStore
from job_scraper.adapters.storage.portal_store import PortalStore
from job_scraper.domain.models import JobRecord
from job_scraper.storage.db import Database


def _docx(text: str) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr(
            "word/document.xml",
            f'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body></w:document>',
        )
    return output.getvalue()


def _setup(tmp_path: Path) -> tuple[TestClient, list[tuple[str, str]], PortalStore]:
    browser = BrowserTaskStore(tmp_path / "browser.db")
    browser.initialize()
    portal = PortalStore(tmp_path / "portal.db")
    portal.initialize()
    jobs = Database(tmp_path / "jobs.db")
    jobs.initialize()
    workspace = tmp_path / "workspace"
    links: list[tuple[str, str]] = []
    app = create_app(
        store=browser,
        portal_store=portal,
        upload_root=tmp_path / "private-uploads",
        send_login=lambda email, link: links.append((email, link)),
        secure_cookie=False,
        candidate_workspace=workspace,
        job_db=tmp_path / "jobs.db",
    )
    return TestClient(app), links, portal


def _insert_job(tmp_path: Path, *, job_id: str, title: str, source: str = "fictional") -> None:
    observed = datetime(2026, 1, 1, tzinfo=UTC)
    database = Database(tmp_path / "jobs.db")
    run = database.create_run(source, observed)
    database.upsert_job(
        JobRecord(
            source=source,
            source_job_id=job_id,
            source_url=f"https://example.test/{job_id}",
            canonical_url=f"https://example.test/{job_id}",
            title=title,
            company_name="Example GmbH",
            location_raw="Berlin",
            country="DE",
            city="Berlin",
            region="",
            remote_type="onsite",
            employment_type="full-time",
            seniority="unknown",
            posted_at=observed,
            first_seen_at=observed,
            scraped_at=observed,
            job_description="A fictional role for table filtering.",
            description_language="English",
            english_ratio=1.0,
            keyword_hits=[],
            tech_stack=[],
            salary_text="",
            salary_min=None,
            salary_max=None,
            salary_currency=None,
            dedupe_key=job_id,
        ),
        run.run_id,
    )


def _login(client: TestClient, links: list[tuple[str, str]], email: str) -> None:
    response = client.post("/login", data={"email": email})
    assert response.status_code == 200
    assert links[-1][0] == email
    assert client.get(links[-1][1], follow_redirects=True).status_code == 200


def _grant_dashboard(client: TestClient, portal: PortalStore) -> None:
    token = client.cookies.get("positions_session") or ""
    current = portal.session(token)
    assert current is not None
    with portal.connect() as connection:
        connection.execute(
            "UPDATE portal_onboarding SET state='integrations_configured' WHERE tenant_id=?",
            (current["tenant_id"],),
        )


def test_new_user_can_upload_analyze_and_approve_resume(tmp_path: Path) -> None:
    client, links, portal = _setup(tmp_path)
    _login(client, links, "person@example.test")
    onboarding = client.get("/onboarding")
    assert "multiple" in onboarding.text
    assert "up to five source resumes" in onboarding.text
    csrf = onboarding.text.split("name=csrf_token value='")[1].split("'")[0]
    uploaded = client.post(
        "/onboarding/resume",
        data={"csrf_token": csrf, "candidate_name": "Fictional Person"},
        files={
            "resume": (
                "resume.docx",
                _docx("C++ Qt developer building embedded systems"),
                "application/octet-stream",
            )
        },
        follow_redirects=True,
    )
    assert uploaded.status_code == 200
    assert "下一份工作最想做什么" in uploaded.text
    answers = {
        "desired_work": "I want hands-on C++ and Qt product work, not management.",
        "location": "Berlin or remote in Germany.",
        "languages": "English; German is optional.",
        "employment": "Full time only.",
    }
    response = uploaded
    for key, answer in answers.items():
        response = client.post(
            "/onboarding/answer",
            data={"csrf_token": csrf, "question_key": key, "answer": answer},
            follow_redirects=True,
        )
        assert response.status_code == 200
    assert "Agent 建议" in response.text
    assert "C++ / Qt" in response.text
    assert (
        client.post(
            "/onboarding/refine",
            data={"csrf_token": csrf, "answer": "Keep C++ first and exclude management."},
            follow_redirects=False,
        ).status_code
        == 303
    )
    finalized = client.post(
        "/onboarding/finalize", data={"csrf_token": csrf}, follow_redirects=False
    )
    assert finalized.status_code == 303
    assert finalized.headers["location"] == "/"
    notes = (tmp_path / "workspace/shared/profile-notes.md").read_text(encoding="utf-8")
    assert "# Profile notes" in notes
    assert "C++ Qt developer building embedded systems" in notes
    assert notes.count("BEGIN POSITIONS APPROVED EVIDENCE") == 1
    token = client.cookies.get("positions_session")
    session = portal.session(token or "")
    assert session is not None
    assert portal.state(session["tenant_id"]) == "integrations_configured"
    snapshot = portal.snapshot(session["tenant_id"])
    assert snapshot["state"] == "integrations_configured"
    preferences = snapshot["preferences"]
    assert isinstance(preferences, dict)
    assert preferences["locations_json"] == '["Berlin or remote in Germany."]'
    enrollment_page = client.post("/onboarding/enrollment", data={"csrf_token": csrf})
    assert enrollment_page.status_code == 200
    enrollment_token = enrollment_page.text.split("stdin:</p><pre>")[1].split("</pre>")[0]
    redeemed = client.post(
        "/v1/enrollments/redeem",
        headers={"Authorization": f"Enrollment {enrollment_token}"},
        json={"device_id": "fictional-device"},
    )
    assert redeemed.status_code == 200
    assert portal.state(session["tenant_id"]) == "connector_enrolled"


def test_onboarding_accepts_multiple_resumes_and_back_edits_an_answer(tmp_path: Path) -> None:
    client, links, portal = _setup(tmp_path)
    _login(client, links, "person@example.test")
    onboarding = client.get("/onboarding")
    csrf = onboarding.text.split("name=csrf_token value='")[1].split("'")[0]
    uploaded = client.post(
        "/onboarding/resume",
        data={"csrf_token": csrf, "candidate_name": "Fictional Person"},
        files=[
            (
                "resume",
                ("primary.docx", _docx("C++ Qt product engineer"), "application/octet-stream"),
            ),
            (
                "resume",
                ("secondary.docx", _docx("Embedded systems evidence"), "application/octet-stream"),
            ),
        ],
        follow_redirects=True,
    )
    assert uploaded.status_code == 200
    assert "Back" in uploaded.text
    token = client.cookies.get("positions_session") or ""
    current = portal.session(token)
    assert current is not None
    assert (
        len(cast(list[dict[str, object]], portal.snapshot(current["tenant_id"])["documents"])) == 2
    )

    answered = client.post(
        "/onboarding/answer",
        data={"csrf_token": csrf, "question_key": "desired_work", "answer": "Original answer"},
        follow_redirects=False,
    )
    assert answered.headers["location"] == "/onboarding?question=location"
    previous = client.get("/onboarding?question=desired_work")
    assert "Original answer</textarea>" in previous.text
    assert "/onboarding?resumes=true" in previous.text
    edited = client.post(
        "/onboarding/answer",
        data={"csrf_token": csrf, "question_key": "desired_work", "answer": "Edited answer"},
        follow_redirects=False,
    )
    assert edited.status_code == 303
    assert portal.answers(current["tenant_id"])["desired_work"] == "Edited answer"


def test_missing_resume_analysis_is_recoverable_instead_of_rendering_blank(tmp_path: Path) -> None:
    client, links, portal = _setup(tmp_path)
    _login(client, links, "person@example.test")
    csrf = client.get("/onboarding").text.split("name=csrf_token value='")[1].split("'")[0]
    client.post(
        "/onboarding/resume",
        data={"csrf_token": csrf, "candidate_name": "Fictional Person"},
        files={
            "resume": (
                "resume.docx",
                _docx("C++ Qt developer building embedded systems"),
                "application/octet-stream",
            )
        },
    )
    token = client.cookies.get("positions_session") or ""
    current = portal.session(token)
    assert current is not None
    with portal.connect() as connection:
        connection.execute(
            "DELETE FROM portal_resume_analysis WHERE tenant_id=?", (current["tenant_id"],)
        )
    for key, answer in (
        ("desired_work", "Product engineering"),
        ("location", "Fictional City"),
        ("languages", "English"),
        ("employment", "Full time"),
    ):
        client.post(
            "/onboarding/answer",
            data={"csrf_token": csrf, "question_key": key, "answer": answer},
        )
    recovery = client.get("/onboarding")
    assert "Resume analysis needs to run" in recovery.text
    assert "action=/onboarding/reanalyze" in recovery.text
    retried = client.post("/onboarding/reanalyze", data={"csrf_token": csrf}, follow_redirects=True)
    assert retried.status_code == 200
    assert "Agent 建议" in retried.text


def test_pending_analysis_has_running_indicator_and_auto_refresh(tmp_path: Path) -> None:
    client, links, portal = _setup(tmp_path)
    _login(client, links, "person@example.test")
    csrf = client.get("/onboarding").text.split("name=csrf_token value='")[1].split("'")[0]
    client.post(
        "/onboarding/resume",
        data={"csrf_token": csrf, "candidate_name": "Fictional Person"},
        files={
            "resume": (
                "resume.docx",
                _docx("C++ Qt developer building embedded systems"),
                "application/octet-stream",
            )
        },
    )
    token = client.cookies.get("positions_session") or ""
    current = portal.session(token)
    assert current is not None
    with portal.connect() as connection:
        connection.execute(
            "UPDATE portal_resume_analysis SET status='pending' WHERE tenant_id=?",
            (current["tenant_id"],),
        )
    for key, answer in (
        ("desired_work", "Product engineering"),
        ("location", "Fictional City"),
        ("languages", "English"),
        ("employment", "Full time"),
    ):
        portal.save_answer(current["tenant_id"], key, answer)
    waiting = client.get("/onboarding")
    assert "Analysis is running" in waiting.text
    assert "class='spinner'" in waiting.text
    assert "<meta http-equiv=refresh content=4>" in waiting.text
    assert "prefers-reduced-motion" in waiting.text


def test_portal_instance_refuses_a_second_account_without_disclosing_it(tmp_path: Path) -> None:
    first, links, portal = _setup(tmp_path)
    _login(first, links, "first@example.test")
    original_links = list(links)
    second = TestClient(first.app)
    response = second.post("/login", data={"email": "second@example.test"})
    assert response.status_code == 200
    assert "one-time sign-in link has been sent" in response.text
    assert links == original_links
    with portal.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM portal_users").fetchone()[0] == 1


def test_csrf_is_bound_to_the_signed_in_session(tmp_path: Path) -> None:
    client, links, _ = _setup(tmp_path)
    _login(client, links, "person@example.test")
    denied = client.post("/onboarding/approve", data={"csrf_token": "wrong", "kind": "evidence"})
    assert denied.status_code == 403


def test_logout_revokes_server_session(tmp_path: Path) -> None:
    client, links, portal = _setup(tmp_path)
    _login(client, links, "person@example.test")
    token = client.cookies.get("positions_session") or ""
    assert portal.session(token) is not None
    current = portal.session(token)
    assert current is not None
    response = client.post(
        "/logout", data={"csrf_token": current["csrf_token"]}, follow_redirects=False
    )
    assert response.status_code == 303
    assert portal.session(token) is None


def test_logout_rejects_cross_site_request_without_csrf(tmp_path: Path) -> None:
    client, links, portal = _setup(tmp_path)
    _login(client, links, "person@example.test")
    token = client.cookies.get("positions_session") or ""
    assert client.post("/logout").status_code == 422
    assert portal.session(token) is not None


def test_dashboard_uses_shared_app_shell_and_real_screening_rows(tmp_path: Path) -> None:
    client, links, portal = _setup(tmp_path)
    reports = tmp_path / "workspace/analysis/fine-screen"
    reports.mkdir(parents=True)
    (reports / "latest-agent.csv").write_text(
        "title,company,score,decision\nFictional Engineer,Example GmbH,0.9,selected\n"
        "Review Engineer,Sample AG,0.5,rejected\n",
        encoding="utf-8",
    )
    _login(client, links, "person@example.test")
    _grant_dashboard(client, portal)
    response = client.get("/")
    assert response.status_code == 200
    assert 'class="sidebar"' in response.text
    assert 'class="nav-item active" href="/"' in response.text
    assert "<span>Seen</span><strong>2</strong>" in response.text
    assert "<span>Accept</span><strong>1</strong>" in response.text
    assert "<span>Drop</span><strong>1</strong>" in response.text
    assert "Fictional Engineer" in response.text
    assert "#287fbd" in response.text
    assert "#245e52" not in response.text.split("body{background:#f3f6f8", 1)[-1]
    assert response.text.count('class="metric-icon" aria-hidden="true"') == 3
    assert response.headers["content-security-policy"].startswith("default-src 'self'")


def test_settings_navigation_has_a_real_page(tmp_path: Path) -> None:
    client, links, portal = _setup(tmp_path)
    _login(client, links, "person@example.test")
    _grant_dashboard(client, portal)
    response = client.get("/settings")
    assert response.status_code == 200
    assert "Connections & settings" in response.text
    assert "No approved settings yet" in response.text
    assert 'href="/settings" aria-current="page"' in response.text
    assert '<details class="mobile-nav">' in response.text
    assert response.text.count('action="/logout"') == 2


def test_empty_dashboard_does_not_claim_a_completed_zero_result_run(tmp_path: Path) -> None:
    client, links, portal = _setup(tmp_path)
    _login(client, links, "person@example.test")
    _grant_dashboard(client, portal)
    response = client.get("/")
    assert response.text.count("<strong>—</strong>") == 3
    assert "No reviewed jobs yet" in response.text


def test_incomplete_onboarding_blocks_every_dashboard_page(tmp_path: Path) -> None:
    client, links, _ = _setup(tmp_path)
    _login(client, links, "person@example.test")
    for path in ("/", "/jobs", "/profile", "/resumes", "/runs", "/settings"):
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == "/onboarding"
    onboarding = client.get("/onboarding")
    assert "Step 1 of 6" in onboarding.text
    assert "Start with your resume" in onboarding.text


def test_jobs_table_filters_sorts_and_pages_canonical_jobs(tmp_path: Path) -> None:
    client, links, portal = _setup(tmp_path)
    _insert_job(tmp_path, job_id="role-b", title="Beta Engineer", source="board-b")
    _insert_job(tmp_path, job_id="role-a", title="Alpha Engineer", source="board-a")
    _login(client, links, "person@example.test")
    _grant_dashboard(client, portal)
    response = client.get("/jobs?q=alpha&source=board-a&sort=title&order=asc&page_size=25")
    assert response.status_code == 200
    assert "2 jobs" not in response.text
    assert "1 jobs" in response.text
    assert "Alpha Engineer" in response.text
    assert "Beta Engineer" not in response.text
    assert "Location" in response.text
    assert "First seen" in response.text
    assert client.get("/jobs?sort=DROP+TABLE+jobs", follow_redirects=False).status_code == 422


def test_resume_signature_is_checked_not_just_extension(tmp_path: Path) -> None:
    client, links, _ = _setup(tmp_path)
    _login(client, links, "person@example.test")
    csrf = client.get("/onboarding").text.split("name=csrf_token value='")[1].split("'")[0]
    response = client.post(
        "/onboarding/resume",
        data={"csrf_token": csrf, "candidate_name": "Fictional Person"},
        files={"resume": ("resume.pdf", b"not really a PDF", "application/pdf")},
    )
    assert response.status_code == 422


def test_onboarding_steps_cannot_be_skipped(tmp_path: Path) -> None:
    client, links, portal = _setup(tmp_path)
    _login(client, links, "person@example.test")
    csrf = client.get("/onboarding").text.split("name=csrf_token value='")[1].split("'")[0]
    assert (
        client.post("/onboarding/approve", data={"csrf_token": csrf, "kind": "tracks"}).status_code
        == 409
    )
    settings = client.post(
        "/settings",
        data={
            "csrf_token": csrf,
            "locations": "Fictional City",
            "countries": "DE",
            "languages": "English",
            "employment_type": "full_time",
            "sources": "linkedin_direct",
        },
    )
    assert settings.status_code == 409
    token = client.cookies.get("positions_session") or ""
    session = portal.session(token)
    assert session is not None
    assert portal.snapshot(session["tenant_id"])["preferences"] is None
    assert client.post("/onboarding/enrollment", data={"csrf_token": csrf}).status_code == 409


def test_results_and_resume_download_require_session_and_stay_inside_workspace(
    tmp_path: Path,
) -> None:
    client, links, portal = _setup(tmp_path)
    workspace = tmp_path / "workspace"
    (workspace / "analysis/fine-screen").mkdir(parents=True)
    (workspace / "CV/Fine-Screened").mkdir(parents=True)
    _insert_job(tmp_path, job_id="role-1", title="Fictional Engineer")
    (workspace / "CV/Fine-Screened/fictional.pdf").write_bytes(b"%PDF-1.4 fictional")
    assert client.get("/resumes/fictional.pdf", follow_redirects=False).status_code == 303
    _login(client, links, "person@example.test")
    _grant_dashboard(client, portal)
    assert "Fictional Engineer" in client.get("/jobs").text
    assert "fictional.pdf" in client.get("/resumes").text
    download = client.get("/resumes/fictional.pdf")
    assert download.status_code == 200
    assert download.headers["content-type"] == "application/pdf"
    assert client.get("/resumes/%2e%2e%2foutside.pdf").status_code == 404
