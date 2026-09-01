"""Minimal server-rendered Web portal for one-pass tenant onboarding."""

from __future__ import annotations

import csv
import html
import io
import json
import os
import sqlite3
import zipfile
from collections.abc import Callable
from hashlib import sha256
from pathlib import Path
from typing import Annotated, cast
from urllib.parse import urlencode
from xml.etree import ElementTree

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Cookie,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from pypdf import PdfReader

from job_scraper.adapters.storage.browser_task_store import BrowserTaskStore
from job_scraper.adapters.storage.portal_store import ONBOARDING_STATES, PortalStore
from job_scraper.adapters.workspace_bootstrap import initialize_candidate_workspace
from job_scraper.ports.resume_analysis import ResumeAnalyzer

MAX_RESUME_BYTES = 10 * 1024 * 1024
SESSION_COOKIE = "positions_session"
EVIDENCE_BEGIN = "<!-- BEGIN POSITIONS APPROVED EVIDENCE -->"
EVIDENCE_END = "<!-- END POSITIONS APPROVED EVIDENCE -->"
ONBOARDING_QUESTIONS = (
    ("desired_work", "你下一份工作最想做什么? 可以写职位、方向, 或你想解决的问题。"),
    ("location", "你希望在哪里工作? 是否接受 remote、hybrid 或搬家?"),
    ("languages", "你能用哪些语言工作? 有没有最低语言要求?"),
    ("employment", "你希望全职、兼职还是都可以? 还有哪些必须满足或一定不要的条件?"),
)


class BasicResumeAnalyzer:
    """Conservative bootstrap analyzer; every suggestion remains user-approved evidence."""

    def analyze(self, text: str) -> dict[str, list[dict[str, object]]]:
        lines = [line.strip() for line in text.splitlines() if len(line.strip()) >= 12][:40]
        lowered = text.casefold()
        tracks: list[dict[str, object]] = []
        if any(term in lowered for term in ("c++", "qt", "embedded", "firmware")):
            tracks.append(
                {"key": "cpp", "label": "C++ / Qt", "mode": "core", "keywords": ["C++", "Qt"]}
            )
        if any(
            term in lowered
            for term in ("machine learning", "artificial intelligence", " llm", " ai ")
        ):
            tracks.append({"key": "ai", "label": "AI", "mode": "core", "keywords": ["AI", "LLM"]})
        if not tracks:
            tracks.append({"key": "general", "label": "General", "mode": "review", "keywords": []})
        claims: list[dict[str, object]] = [
            {"text": line, "source_ref": f"resume:line:{index + 1}"}
            for index, line in enumerate(lines)
        ]
        return {"claims": claims, "tracks": tracks}

    def refine(
        self,
        text: str,
        *,
        answers: dict[str, str],
        current_tracks: list[dict[str, object]],
    ) -> dict[str, object]:
        del text
        location = answers.get("location", "Remote")
        languages = answers.get("languages", "English")
        employment = answers.get("employment", "").casefold()
        return {
            "summary": f"目标: {answers.get('desired_work', '按简历匹配')}; 地点: {location}",
            "tracks": current_tracks,
            "preferences": {
                "locations": [location],
                "countries": ["DE"],
                "languages": [languages],
                "employment_type": "part_time" if "part" in employment else "full_time",
                "sources": [
                    "linkedin_direct",
                    "arbeitsagentur_direct",
                    "ats_direct",
                    "workable_direct",
                ],
            },
        }


NAV_ITEMS = (
    ("/", "Overview", "⌂"),
    ("/jobs", "Jobs", "▤"),
    ("/profile", "Search directions", "◎"),
    ("/resumes", "CV & evidence", "▱"),
    ("/runs", "Activity", "↻"),
)


def _page(
    title: str,
    body: str,
    *,
    email: str | None = None,
    csrf_token: str | None = None,
    active_path: str = "",
    eyebrow: str | None = None,
    show_setup: bool = True,
    refresh_seconds: int | None = None,
) -> HTMLResponse:
    escaped_title = html.escape(title)
    if email is None:
        shell = f'<main class="auth"><section class="auth-card"><a class="brand" href="/">Positions<span>.</span></a><h1>{escaped_title}</h1>{body}</section></main>'
    else:

        def nav_link(path: str, label: str, icon: str) -> str:
            active_class = " active" if path == active_path else ""
            current = ' aria-current="page"' if path == active_path else ""
            return f'<a class="nav-item{active_class}" href="{path}"{current}><span class="nav-icon" aria-hidden="true">{icon}</span><span>{label}</span></a>'

        links = "".join(nav_link(path, label, icon) for path, label, icon in NAV_ITEMS)
        logout = f'<form method="post" action="/logout"><input type="hidden" name="csrf_token" value="{html.escape(csrf_token or "")}"><button class="account" type="submit"><span class="avatar">{html.escape(email[:1].upper())}</span><span class="account-copy"><strong>{html.escape(email)}</strong><small>Sign out</small></span></button></form>'
        settings_class = "nav-item active" if active_path == "/settings" else "nav-item"
        settings_current = ' aria-current="page"' if active_path == "/settings" else ""
        settings_link = f'<a class="{settings_class}" href="/settings"{settings_current}><span class="nav-icon">⚙</span><span>Connections & settings</span></a>'
        setup_cta = '<a class="top-cta" href="/onboarding">Setup</a>' if show_setup else ""
        shell = f"""<div class="app-shell">
<aside class="sidebar"><a class="brand" href="/">Positions<span>.</span></a><nav aria-label="Primary">{links}</nav><div class="sidebar-bottom">{settings_link}{logout}</div></aside>
<main class="main"><header class="topbar"><details class="mobile-nav"><summary aria-label="Open navigation">☰</summary><div>{links}{settings_link}{logout}</div></details><div><p class="eyebrow">{html.escape(eyebrow or "Workspace")}</p><h1>{escaped_title}</h1></div>{setup_cta}</header><div class="content">{body}</div></main></div>"""
    return HTMLResponse(
        "<!doctype html><html lang='en'><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
        f"<title>{escaped_title} · Positions</title>{f'<meta http-equiv=refresh content={refresh_seconds}>' if refresh_seconds else ''}<style>"
        "*{box-sizing:border-box}html{color-scheme:light}body{margin:0;background:#f5f7f5;color:#18201d;font-family:Inter,ui-sans-serif,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;font-size:14px;line-height:1.5}a{color:inherit;text-decoration:none}button,input,textarea,select{font:inherit}button{cursor:pointer}.app-shell{min-height:100vh}.sidebar{position:fixed;inset:0 auto 0 0;width:224px;background:#fff;border-right:1px solid #dfe5e1;padding:26px 16px 18px;display:flex;flex-direction:column;z-index:2}.brand{display:inline-flex;align-items:center;padding:0 12px;color:#18201d;font-size:20px;font-weight:760;letter-spacing:-.04em}.brand span{color:#245e52}.sidebar nav{display:grid;gap:4px;margin-top:34px}.nav-item{min-height:42px;padding:10px 12px;display:flex;align-items:center;gap:11px;border-radius:8px;color:#65706a;font-weight:570}.nav-item:hover{background:#f5f7f5;color:#18201d}.nav-item.active{background:#e7f1ed;color:#194a40}.nav-icon{width:19px;text-align:center;font-size:16px}.sidebar-bottom{margin-top:auto;display:grid;gap:12px}.account{width:100%;border:0;border-top:1px solid #dfe5e1;background:transparent;padding:16px 8px 0;display:flex;gap:10px;text-align:left;color:#18201d}.avatar{width:32px;height:32px;border-radius:50%;display:grid;place-items:center;background:#245e52;color:white;font-weight:700}.account-copy{min-width:0;display:grid}.account-copy strong{overflow:hidden;text-overflow:ellipsis;font-size:12px}.account-copy small{color:#65706a}.main{margin-left:224px;min-height:100vh}.topbar{height:80px;padding:18px 32px;border-bottom:1px solid #dfe5e1;background:#fff;display:flex;align-items:center;justify-content:space-between;position:sticky;top:0;z-index:1}.topbar h1{margin:1px 0 0;font-size:20px;line-height:1.2;letter-spacing:-.02em}.eyebrow{margin:0;color:#7b857f;font-size:11px;font-weight:700;letter-spacing:.09em;text-transform:uppercase}.top-cta,.button,button:not(.account){border:1px solid #245e52;border-radius:7px;background:#245e52;color:#fff;padding:9px 14px;font-weight:650}.top-cta:hover,.button:hover,button:not(.account):hover{background:#194a40}.button.secondary{background:#fff;color:#245e52}.content{max-width:1440px;margin:0 auto;padding:32px}.hero{display:flex;justify-content:space-between;gap:24px;align-items:flex-start;margin-bottom:24px}.hero h2{font-size:28px;line-height:36px;letter-spacing:-.035em;margin:0 0 6px}.hero p{margin:0;color:#65706a;max-width:680px}.status-pill,.pill{display:inline-flex;align-items:center;gap:7px;border-radius:999px;padding:6px 10px;background:#e7f1ed;color:#245e52;font-size:12px;font-weight:700}.status-dot{width:7px;height:7px;border-radius:50%;background:#2f7d68}.metrics{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin-bottom:24px}.metric,.card{background:#fff;border:1px solid #dfe5e1;border-radius:10px}.metric{padding:18px 20px}.metric span{color:#65706a;font-size:12px;font-weight:650}.metric strong{display:block;font-size:26px;letter-spacing:-.035em;margin-top:5px}.dashboard-grid{display:grid;grid-template-columns:minmax(520px,3fr) minmax(360px,2fr);gap:24px}.stack{display:grid;gap:16px;align-content:start}.card{padding:20px}.card-head{display:flex;align-items:center;justify-content:space-between;gap:16px;margin-bottom:16px}.card h2,.card h3{margin:0;font-size:16px;line-height:24px}.card p{color:#65706a}.card-link{color:#245e52;font-weight:650;font-size:13px}.empty{padding:30px 16px;text-align:center;border:1px dashed #cbd4ce;border-radius:8px;background:#fafbfa}.empty strong{display:block;margin-bottom:4px}.empty p{margin:0}.list{list-style:none;padding:0;margin:0}.list li{display:flex;justify-content:space-between;gap:16px;padding:13px 0;border-top:1px solid #edf0ee}.list li:first-child{border-top:0}.list small,.muted{color:#65706a}.progress{height:7px;background:#eef2ef;border-radius:99px;overflow:hidden}.progress span{display:block;height:100%;background:#2f7d68}.steps{list-style:none;padding:0;margin:16px 0}.steps li{padding:9px 0;color:#65706a}.steps .done{color:#245e52}.notice{padding:14px 16px;background:#f8faf8;border-left:3px solid #d3a33d;color:#4c554f}.table-wrap{overflow:auto}table{width:100%;border-collapse:collapse;text-align:left}th{padding:10px 12px;color:#65706a;font-size:11px;text-transform:uppercase;letter-spacing:.06em;border-bottom:1px solid #dfe5e1}td{padding:13px 12px;border-bottom:1px solid #edf0ee}pre{overflow:auto;background:#eef2ef;padding:14px;border-radius:8px}input,textarea,select{max-width:100%;border:1px solid #cbd4ce;border-radius:7px;background:#fff;padding:9px 11px;margin:5px 0 12px}textarea{width:100%}form+form{margin-top:10px}.auth{min-height:100vh;display:grid;place-items:center;padding:24px}.auth-card{width:min(420px,100%);background:#fff;border:1px solid #dfe5e1;border-radius:10px;padding:32px;box-shadow:0 18px 45px rgba(24,32,29,.06)}.auth-card h1{margin:28px 0 16px;font-size:28px}.mobile-nav{display:none}"
        "@media(max-width:1279px){.sidebar{width:76px;padding-inline:10px}.sidebar .brand{padding:0;font-size:0;justify-content:center}.sidebar .brand:first-letter{font-size:20px}.nav-item span:last-child,.account-copy{display:none}.nav-item{justify-content:center}.main{margin-left:76px}}"
        "@media(max-width:959px){.dashboard-grid{grid-template-columns:1fr}.content{padding:24px}.topbar{padding-inline:24px}}"
        "@media(max-width:719px){.sidebar{display:none}.main{margin-left:0}.topbar{height:72px;padding:14px 18px}.content{padding:20px 16px}.mobile-nav{display:block;margin-right:12px}.mobile-nav summary{list-style:none;font-size:20px}.mobile-nav>div{position:absolute;top:64px;left:12px;width:240px;padding:10px;background:#fff;border:1px solid #dfe5e1;border-radius:9px;box-shadow:0 12px 30px rgba(24,32,29,.12)}.metrics{grid-template-columns:1fr}.hero{display:block}.hero .status-pill{margin-top:14px}.top-cta{padding:8px 11px}.dashboard-grid{display:block}.stack{margin-bottom:16px}}"
        "body{background:#f3f6f8;color:#102531}.sidebar,.topbar,.metric,.card,.auth-card{background:#fff;border-color:#dce4e8}.brand{color:#102531}.brand span{color:#287fbd}.nav-item{color:#6f7d85}.nav-item:hover{background:#f3f6f8;color:#102531}.nav-item.active{background:#e8f2f9;color:#1e669a;box-shadow:inset 3px 0 #287fbd}.top-cta,.button,button:not(.account){background:#062536;border-color:#062536;border-radius:4px}.top-cta:hover,.button:hover,button:not(.account):hover{background:#1e669a;border-color:#1e669a}.button.secondary{background:#fff;color:#1e669a;border-color:#9fb4c1}.card-link,.steps .done{color:#1e669a}.avatar{background:#287fbd}.eyebrow,.metric span,.card p,.muted,.list small{color:#6f7d85}.status-pill,.pill{background:#e8f2f9;color:#1e669a}.status-dot{background:#24a8a3}.progress{background:#e8edf0}.progress span{background:#287fbd}.metric{position:relative;padding-right:64px}.metric-icon{position:absolute;right:18px;top:50%;width:38px;height:38px;transform:translateY(-50%);display:grid;place-items:center;border-radius:50%;background:#eef3f6;color:#102531}.metric-icon svg{width:20px;height:20px}.job-filters{display:grid;grid-template-columns:minmax(220px,2fr) repeat(4,minmax(120px,1fr)) auto auto;gap:12px;align-items:end;background:#fff;border:1px solid #dce4e8;padding:16px;margin-bottom:16px}.job-filters label{display:grid;color:#52636d;font-size:12px;font-weight:650}.job-filters input,.job-filters select{width:100%;margin:5px 0 0}.table-meta,.pagination{display:flex;align-items:center;justify-content:space-between;gap:12px;margin:12px 0}.table-wrap{padding:0;max-height:calc(100vh - 310px)}thead{position:sticky;top:0;z-index:1;background:#fff}.onboarding-progress{margin:28px 0 16px}.onboarding-progress>div:first-child{display:flex;justify-content:space-between;gap:16px;margin-bottom:10px;color:#6f7d85;font-size:12px}.onboarding-progress strong{color:#102531}.onboarding-card{border-top:1px solid #dce4e8;padding-top:24px}.onboarding-card h2{font-size:24px;line-height:32px;margin:4px 0 8px}.step-kicker{margin:0;color:#287fbd;font-size:12px;font-weight:700;text-transform:uppercase;letter-spacing:.08em}.onboarding-card label{display:grid;font-weight:600}.onboarding-card small{display:block;margin:-6px 0 14px;color:#6f7d85}.form-actions{display:flex;justify-content:flex-end;gap:10px}.running-status{display:flex;align-items:center;gap:16px;padding:18px;border:1px solid #cfe0eb;background:#f7fbfd}.running-status p{margin:2px 0 0}.spinner{width:28px;height:28px;flex:0 0 auto;border:3px solid #cfe0eb;border-top-color:#287fbd;border-radius:50%;animation:spin .8s linear infinite}@keyframes spin{to{transform:rotate(360deg)}}.auth-card{width:min(680px,100%);box-shadow:0 14px 36px rgba(16,37,49,.07)}:focus-visible{outline:2px solid #287fbd;outline-offset:2px}@media(prefers-reduced-motion:reduce){.spinner{animation:none;border-color:#287fbd}}@media(max-width:1100px){.job-filters{grid-template-columns:repeat(2,minmax(0,1fr))}}@media(max-width:719px){.job-filters{grid-template-columns:1fr}.table-wrap{max-height:none}}"
        f"</style></head><body>{shell}</body></html>"
    )


def _metric_icon(kind: str) -> str:
    paths = {
        "seen": '<path d="M2.5 12s3.5-6 9.5-6 9.5 6 9.5 6-3.5 6-9.5 6-9.5-6-9.5-6Z"/><circle cx="12" cy="12" r="2.5"/>',
        "accept": '<path d="m5 12 4 4L19 6"/>',
        "drop": '<path d="M6 6l12 12M18 6 6 18"/>',
    }
    return f'<span class="metric-icon" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">{paths[kind]}</svg></span>'


def _screening_rows(candidate_workspace: Path | None) -> list[dict[str, str]]:
    if candidate_workspace is None:
        return []
    reports = candidate_workspace / "analysis/fine-screen"
    files = sorted(reports.glob("*-agent.csv"), key=lambda path: path.stat().st_mtime, reverse=True)
    if not files:
        return []
    with files[0].open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))[:200]


def _onboarding_page(
    title: str,
    body: str,
    *,
    step: int,
    total: int = 6,
    refresh_seconds: int | None = None,
) -> HTMLResponse:
    progress = max(0, min(100, round(step / total * 100)))
    content = f'''<div class="onboarding-progress"><div><span>Step {step} of {total}</span><strong>{html.escape(title)}</strong></div><div class="progress" role="progressbar" aria-label="Onboarding progress" aria-valuemin="0" aria-valuemax="{total}" aria-valuenow="{step}"><span style="width:{progress}%"></span></div></div><div class="onboarding-card">{body}</div>'''
    return _page("Set up your workspace", content, refresh_seconds=refresh_seconds)


def _extract_resume(data: bytes, filename: str) -> tuple[str, str]:
    suffix = Path(filename).suffix.casefold()
    if suffix == ".pdf" and data.startswith(b"%PDF"):
        text = "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(data)).pages)
        return text, "application/pdf"
    if suffix == ".docx" and data.startswith(b"PK"):
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            info = archive.getinfo("word/document.xml")
            if info.file_size > 20 * 1024 * 1024:
                raise ValueError("DOCX document.xml is too large")
            xml = archive.read("word/document.xml")
        root = ElementTree.fromstring(xml)
        text = "\n".join(node.text or "" for node in root.iter() if node.tag.endswith("}t"))
        return text, "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    raise ValueError("Only genuine PDF and DOCX resumes are accepted")


def _sync_approved_evidence(workspace: Path, evidence: list[dict[str, object]]) -> None:
    path = workspace / "shared/profile-notes.md"
    if not path.is_file():
        raise ValueError("Candidate workspace is missing shared/profile-notes.md")
    original = path.read_text(encoding="utf-8")
    if original.count(EVIDENCE_BEGIN) != original.count(EVIDENCE_END):
        raise ValueError("Candidate profile notes contain an incomplete managed evidence block")
    lines = [EVIDENCE_BEGIN, "", "## Resume evidence approved in Positions", ""]
    lines.extend(
        f"- {str(item['claim_text']).strip()} ({str(item['source_ref']).strip()})"
        for item in evidence
        if str(item["claim_text"]).strip()
    )
    lines.extend(["", EVIDENCE_END])
    block = "\n".join(lines)
    if EVIDENCE_BEGIN in original:
        prefix, tail = original.split(EVIDENCE_BEGIN, 1)
        _, suffix = tail.split(EVIDENCE_END, 1)
        updated = prefix.rstrip() + "\n\n" + block + suffix
    else:
        updated = original.rstrip() + "\n\n" + block + "\n"
    temporary = path.with_suffix(".md.positions.tmp")
    temporary.write_text(updated, encoding="utf-8")
    os.replace(temporary, path)


def create_portal_router(
    *,
    portal_store: PortalStore,
    browser_store: BrowserTaskStore,
    upload_root: Path,
    send_login: Callable[[str, str], None],
    analyzer: ResumeAnalyzer | None = None,
    candidate_workspace: Path | None = None,
    job_db: Path | None = None,
    secure_cookie: bool = True,
) -> APIRouter:
    router = APIRouter()
    analyzer = analyzer or BasicResumeAnalyzer()
    upload_root.mkdir(parents=True, exist_ok=True)

    def session(token: str | None) -> dict[str, str]:
        current = portal_store.session(token or "")
        if current is None:
            raise HTTPException(status.HTTP_303_SEE_OTHER, headers={"Location": "/login"})
        return current

    def csrf(current: dict[str, str], supplied: str) -> None:
        if not supplied or supplied != current["csrf_token"]:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Invalid CSRF token")

    def dashboard_session(token: str | None) -> dict[str, str]:
        current = session(token)
        if not portal_store.dashboard_access_granted(current["tenant_id"]):
            raise HTTPException(status.HTTP_303_SEE_OTHER, headers={"Location": "/onboarding"})
        return current

    def refresh_proposal(tenant_id: str) -> None:
        snapshot = portal_store.snapshot(tenant_id)
        tracks = cast(list[dict[str, object]], snapshot["tracks"])
        normalized_tracks = [
            {
                "key": item["track_key"],
                "label": item["label"],
                "mode": item["mode"],
                "keywords": json.loads(str(item["keywords_json"])),
            }
            for item in tracks
        ]
        result = analyzer.refine(
            portal_store.latest_resume_text(tenant_id),
            answers=cast(dict[str, str], snapshot["answers"]),
            current_tracks=normalized_tracks,
        )
        portal_store.replace_agent_proposal(
            tenant_id,
            tracks=cast(list[dict[str, object]], result["tracks"]),
            summary=str(result["summary"]),
            preferences=cast(dict[str, object], result["preferences"]),
        )

    @router.get("/login", response_class=HTMLResponse)
    def login_page() -> HTMLResponse:
        return _page(
            "Sign in",
            "<form method=post><label>Email<br><input name=email type=email required></label><br><button>Send sign-in link</button></form>",
        )

    @router.post("/login", response_class=HTMLResponse)
    def login(email: Annotated[str, Form()]) -> HTMLResponse:
        token = portal_store.issue_login(email)
        if token is not None:
            send_login(email.strip().casefold(), f"/auth/verify?token={token}")
        return _page("Check your email", "<p>A one-time sign-in link has been sent.</p>")

    @router.get("/auth/verify")
    def verify(token: str) -> RedirectResponse:
        user_id, tenant_id = portal_store.redeem_login(token)
        session_token, _ = portal_store.create_session(user_id, tenant_id)
        response = RedirectResponse("/onboarding", status_code=status.HTTP_303_SEE_OTHER)
        response.set_cookie(
            SESSION_COOKIE,
            session_token,
            httponly=True,
            secure=secure_cookie,
            samesite="lax",
            max_age=30 * 86400,
        )
        return response

    @router.post("/logout")
    def logout(
        csrf_token: Annotated[str, Form()],
        positions_session: Annotated[str | None, Cookie()] = None,
    ) -> RedirectResponse:
        current = session(positions_session)
        csrf(current, csrf_token)
        portal_store.revoke_session(positions_session or "")
        response = RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
        response.delete_cookie(SESSION_COOKIE)
        return response

    @router.get("/", response_class=HTMLResponse)
    def dashboard(positions_session: Annotated[str | None, Cookie()] = None) -> HTMLResponse:
        current = dashboard_session(positions_session)
        snapshot = portal_store.snapshot(current["tenant_id"])
        queue = browser_store.status()
        rows = _screening_rows(candidate_workspace)
        accepted = sum(
            1
            for row in rows
            if str(row.get("selected", row.get("status", row.get("decision", "")))).casefold()
            in {"1", "true", "selected", "accepted", "accept"}
        )
        dropped = max(0, len(rows) - accepted)
        state = str(snapshot["state"])
        state_index = ONBOARDING_STATES.index(state)
        completed_milestones = sum(state_index >= threshold for threshold in (0, 1, 4, 5, 8))
        progress = completed_milestones * 20
        tracks = cast(list[dict[str, object]], snapshot["tracks"])
        recent_jobs = rows[:5]
        job_list = "".join(
            f"<li><span><strong>{html.escape(str(row.get('title', 'Untitled role')))}</strong><br><small>{html.escape(str(row.get('company', 'Unknown company')))}</small></span><span class='pill'>{html.escape(str(row.get('score', row.get('status', 'Reviewed'))))}</span></li>"
            for row in recent_jobs
        )
        if not job_list:
            job_list = "<div class='empty'><strong>No reviewed jobs yet</strong><p>Results appear here after the first screening run.</p></div>"
        track_list = (
            "".join(
                f"<li><span><strong>{html.escape(str(item['label']))}</strong><br><small>{html.escape(str(item['mode']).capitalize())} direction</small></span><span class='pill'>{html.escape(str(item['status']).capitalize())}</span></li>"
                for item in tracks[:4]
            )
            or "<div class='empty'><strong>No search directions yet</strong><p>Complete the Agent conversation to create them.</p></div>"
        )
        queued = queue.get("tasks", {})
        task_count = sum(int(value) for value in queued.values()) if isinstance(queued, dict) else 0
        body = f'''<section class="hero"><div><h2>Your job search, at a glance</h2><p>Review matches, search directions and generated CVs from one focused workspace.</p></div><span class="status-pill"><span class="status-dot"></span>{html.escape(state.replace("_", " ").title())}</span></section>
<section class="metrics" aria-label="Latest screening totals"><div class="metric">{_metric_icon("seen")}<span>Seen</span><strong>{len(rows) if rows else "—"}</strong></div><div class="metric">{_metric_icon("accept")}<span>Accept</span><strong>{accepted if rows else "—"}</strong></div><div class="metric">{_metric_icon("drop")}<span>Drop</span><strong>{dropped if rows else "—"}</strong></div></section>
<section class="dashboard-grid"><div class="stack"><article class="card"><div class="card-head"><h2>Recent matches</h2><a class="card-link" href="/jobs">View all</a></div><ul class="list">{job_list}</ul></article><article class="card"><div class="card-head"><h2>Search directions</h2><a class="card-link" href="/profile">Review</a></div><ul class="list">{track_list}</ul></article></div>
<div class="stack"><article class="card"><div class="card-head"><h2>Setup progress</h2><strong>{progress}%</strong></div><div class="progress"><span style="width:{progress}%"></span></div><ul class="steps"><li class="done">✓ Account verified</li><li class="{"done" if state_index >= 1 else ""}">{"✓" if state_index >= 1 else "○"} CV and goals</li><li class="{"done" if state_index >= 4 else ""}">{"✓" if state_index >= 4 else "○"} Search plan approved</li><li class="{"done" if state_index >= 5 else ""}">{"✓" if state_index >= 5 else "○"} Browser connected</li><li class="{"done" if state_index >= 8 else ""}">{"✓" if state_index >= 8 else "○"} Calibration complete</li></ul><a class="button" href="/onboarding">Continue setup</a></article><article class="card"><div class="card-head"><h2>Browser worker</h2><span class="pill">{html.escape(str(task_count))} queued</span></div><p>The Web dashboard reports server state. Browser execution stays on your connected computer.</p></article><aside class="notice"><strong>Applications remain manual.</strong><br>Positions can discover, screen and prepare evidence, but never submits an application for you.</aside></div></section>'''
        return _page(
            "Overview",
            body,
            email=current["email"],
            csrf_token=current["csrf_token"],
            active_path="/",
            eyebrow="Dashboard",
        )

    @router.get("/onboarding", response_class=HTMLResponse)
    def onboarding(
        question: str | None = None,
        resumes: bool = False,
        positions_session: Annotated[str | None, Cookie()] = None,
    ) -> Response:
        current = session(positions_session)
        snapshot = portal_store.snapshot(current["tenant_id"])
        state = str(snapshot["state"])
        if portal_store.dashboard_access_granted(current["tenant_id"]):
            return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
        documents = cast(list[dict[str, object]], snapshot["documents"])
        if not documents or resumes:
            count_copy = (
                f"{len(documents)} of 5 source resumes retained."
                if documents
                else "You can retain up to five source resumes."
            )
            back = (
                '<a class="button secondary" href="/onboarding">Back to questions</a>'
                if documents
                else ""
            )
            body = f"<p class='step-kicker'>CV & evidence</p><h2>{'Add another resume' if documents else 'Start with your resumes'}</h2><p>We will analyze them privately while you answer four short questions. {count_copy}</p><form method=post action=/onboarding/resume enctype=multipart/form-data><input type=hidden name=csrf_token value='{html.escape(current['csrf_token'])}'><label>Your name<input name=candidate_name required maxlength=120 autocomplete=name></label><label>Resume files<input type=file name=resume accept='.pdf,.docx' required multiple aria-describedby=resume-help></label><small id=resume-help>PDF or DOCX, up to 10 MiB each; five retained files maximum.</small><div class='form-actions'>{back}<button>Upload and continue</button></div></form>"
            return _onboarding_page("Upload resume", body, step=1)
        tracks = cast(list[dict[str, object]], snapshot["tracks"])
        answers = cast(dict[str, str], snapshot["answers"])
        next_question = next(
            (item for item in ONBOARDING_QUESTIONS if item[0] not in answers), None
        )
        requested_question = next(
            (
                item
                for index, item in enumerate(ONBOARDING_QUESTIONS)
                if item[0] == question
                and all(previous[0] in answers for previous in ONBOARDING_QUESTIONS[:index])
            ),
            None,
        )
        active_question = requested_question or next_question
        if active_question:
            key, prompt = active_question
            question_number = next(
                index for index, item in enumerate(ONBOARDING_QUESTIONS, start=2) if item[0] == key
            )
            current_index = next(
                index for index, item in enumerate(ONBOARDING_QUESTIONS) if item[0] == key
            )
            back_url = (
                "/onboarding?resumes=true"
                if current_index == 0
                else f"/onboarding?question={ONBOARDING_QUESTIONS[current_index - 1][0]}"
            )
            value = html.escape(answers.get(key, ""))
            body = f"<p class='step-kicker'>Your goals</p><h2>{html.escape(prompt)}</h2><p>Use your own words. You can refine the complete proposal before approval.</p><form method=post action=/onboarding/answer><input type=hidden name=csrf_token value='{html.escape(current['csrf_token'])}'><input type=hidden name=question_key value='{key}'><textarea name=answer required maxlength=2000 rows=5 autofocus>{value}</textarea><div class='form-actions'><a class='button secondary' href='{back_url}'>Back</a><button>Continue</button></div></form>"
            return _onboarding_page("Tell us what fits", body, step=question_number)
        elif snapshot["analysis_status"] == "pending":
            body = "<p class='step-kicker'>Resume analysis</p><h2>Analyzing your resumes</h2><div class='running-status' role='status' aria-live='polite'><span class='spinner' aria-hidden='true'></span><div><strong>Analysis is running</strong><p>Extracting evidence and preparing your search directions. This page refreshes automatically.</p></div></div><p class='muted'>You can safely leave this page and return later.</p>"
            return _onboarding_page("Analyzing resumes", body, step=6, refresh_seconds=4)
        elif snapshot["analysis_status"] == "failed":
            error = str(snapshot.get("analysis_error") or "")
            reason = (
                "AI analysis capacity is temporarily exhausted. Wait until the usage window resets, then retry."
                if "usage limit" in error.casefold()
                else "The analysis service was unavailable. Retry without uploading your resumes again."
            )
            body = f"<p class='step-kicker'>Resume analysis</p><h2>Analysis could not finish</h2><p>{reason}</p><p class='muted'>Your resumes and answers are safe.</p><form method=post action=/onboarding/reanalyze><input type=hidden name=csrf_token value='{html.escape(current['csrf_token'])}'><button>Retry analysis</button></form>"
            return _onboarding_page("Retry analysis", body, step=6)
        elif snapshot["analysis_status"] is None:
            body = f"<p class='step-kicker'>Resume analysis</p><h2>Resume analysis needs to run</h2><p>Your retained resumes and answers are safe. Start analysis to build the review proposal.</p><form method=post action=/onboarding/reanalyze><input type=hidden name=csrf_token value='{html.escape(current['csrf_token'])}'><button>Analyze resumes</button></form>"
            return _onboarding_page("Analyze resumes", body, step=6)
        body = ""
        proposal = snapshot["proposal"]
        if isinstance(proposal, dict):
            preferences = json.loads(str(proposal["preferences_json"]))
            mode_labels = {
                "core": "重点匹配并生成简历",
                "review": "人工复核",
                "discovery": "只做探索",
            }
            body += f"<h2>Agent 建议</h2><p>{html.escape(str(proposal['summary']))}</p><h3>搜索方向和关键词</h3><ul>"
            body += "".join(
                f"<li><strong>{html.escape(str(item['label']))}</strong> ({html.escape(mode_labels.get(str(item['mode']), str(item['mode'])))})<br>关键词: {html.escape(', '.join(json.loads(str(item['keywords_json']))) or '由 Agent 按职位语义判断')}</li>"
                for item in tracks
            )
            body += "</ul><h3>搜索范围</h3><ul>"
            for label, key in (
                ("地点", "locations"),
                ("国家", "countries"),
                ("工作语言", "languages"),
                ("数据来源", "sources"),
            ):
                body += f"<li>{label}: {html.escape(', '.join(preferences[key]))}</li>"
            body += f"<li>工作类型: {html.escape(str(preferences['employment_type']))}</li></ul><h3>简历依据</h3><ul>"
            body += "".join(
                f"<li>{html.escape(str(item['claim_text']))}</li>"
                for item in cast(list[dict[str, object]], snapshot["evidence"])[:40]
            )
            body += "</ul>"
            body += f"<a class='button secondary' href='/onboarding?question={ONBOARDING_QUESTIONS[-1][0]}'>Back</a><form method=post action=/onboarding/refine><input type=hidden name=csrf_token value='{html.escape(current['csrf_token'])}'><label>想怎么调整? 直接告诉我<br><textarea name=answer required maxlength=2000 rows=4 style='width:100%'></textarea></label><br><button>让 Agent 调整</button></form><form method=post action=/onboarding/finalize><input type=hidden name=csrf_token value='{html.escape(current['csrf_token'])}'><button>以上信息正确, 确认并进入 Dashboard</button></form>"
        if state == "integrations_configured":
            body += f"<h2>连接这台电脑</h2><form method=post action=/onboarding/enrollment><input type=hidden name=csrf_token value='{html.escape(current['csrf_token'])}'><button>生成一次性连接码</button></form>"
        return _onboarding_page("Review and confirm", body, step=6)

    @router.post("/onboarding/reanalyze")
    def reanalyze_resumes(
        background_tasks: BackgroundTasks,
        csrf_token: Annotated[str, Form()],
        positions_session: Annotated[str | None, Cookie()] = None,
    ) -> RedirectResponse:
        current = session(positions_session)
        csrf(current, csrf_token)
        try:
            document_id, retained_text = portal_store.restart_analysis(current["tenant_id"])
        except ValueError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc

        def analyze_retained_resumes() -> None:
            try:
                analysis = analyzer.analyze(retained_text)
                portal_store.replace_analysis(
                    current["tenant_id"],
                    document_id,
                    claims=analysis["claims"],
                    tracks=analysis["tracks"],
                )
                if len(portal_store.answers(current["tenant_id"])) >= len(ONBOARDING_QUESTIONS):
                    refresh_proposal(current["tenant_id"])
            except RuntimeError as exc:
                portal_store.mark_analysis_failed(current["tenant_id"], document_id, str(exc))

        background_tasks.add_task(analyze_retained_resumes)
        return RedirectResponse("/onboarding", status_code=status.HTTP_303_SEE_OTHER)

    @router.post("/onboarding/resume")
    async def upload_resume(
        background_tasks: BackgroundTasks,
        resume: Annotated[list[UploadFile], File()],
        candidate_name: Annotated[str, Form()],
        csrf_token: Annotated[str, Form()],
        positions_session: Annotated[str | None, Cookie()] = None,
    ) -> RedirectResponse:
        current = session(positions_session)
        csrf(current, csrf_token)
        existing_count = len(
            cast(list[dict[str, object]], portal_store.snapshot(current["tenant_id"])["documents"])
        )
        if not resume or existing_count + len(resume) > 5:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, "Retain between one and five resumes"
            )
        extracted: list[tuple[UploadFile, bytes, str, str]] = []
        for uploaded in resume:
            data = await uploaded.read(MAX_RESUME_BYTES + 1)
            if not data or len(data) > MAX_RESUME_BYTES:
                raise HTTPException(
                    status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    "Each resume must be 1 byte to 10 MiB",
                )
            try:
                text, media_type = _extract_resume(data, uploaded.filename or "")
            except (ValueError, KeyError, zipfile.BadZipFile) as exc:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
            if not text.strip():
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_CONTENT,
                    "Every resume must contain extractable text",
                )
            extracted.append((uploaded, data, text, media_type))
        primary_upload, primary_data, _, _ = extracted[0]
        if candidate_workspace is not None:
            try:
                initialize_candidate_workspace(
                    candidate_workspace,
                    candidate_name=candidate_name,
                    email=current["email"],
                    resume_text="\n\n".join(item[2] for item in extracted),
                    original_bytes=primary_data,
                    original_suffix=Path(primary_upload.filename or "").suffix.casefold(),
                )
            except (OSError, ValueError) as exc:
                raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
        document_ids: list[str] = []
        for _, data, text, media_type in extracted:
            digest = sha256(data).hexdigest()
            storage_key = f"{current['tenant_id']}/{digest}"
            target = upload_root / current["tenant_id"] / digest
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            document_ids.append(
                portal_store.add_resume(
                    current["tenant_id"],
                    storage_key=storage_key,
                    digest=digest,
                    media_type=media_type,
                    size=len(data),
                    text=text,
                )
            )
        combined_text = "\n\n".join(item[2] for item in extracted)
        document_id = document_ids[-1]

        def analyze_resume() -> None:
            try:
                analysis = analyzer.analyze(combined_text)
                portal_store.replace_analysis(
                    current["tenant_id"],
                    document_id,
                    claims=analysis["claims"],
                    tracks=analysis["tracks"],
                )
                if len(portal_store.answers(current["tenant_id"])) >= len(ONBOARDING_QUESTIONS):
                    refresh_proposal(current["tenant_id"])
            except RuntimeError as exc:
                portal_store.mark_analysis_failed(current["tenant_id"], document_id, str(exc))

        background_tasks.add_task(analyze_resume)
        return RedirectResponse("/onboarding", status_code=status.HTTP_303_SEE_OTHER)

    @router.post("/onboarding/answer")
    def answer_question(
        question_key: Annotated[str, Form()],
        answer: Annotated[str, Form()],
        csrf_token: Annotated[str, Form()],
        positions_session: Annotated[str | None, Cookie()] = None,
    ) -> RedirectResponse:
        current = session(positions_session)
        csrf(current, csrf_token)
        allowed = {key for key, _ in ONBOARDING_QUESTIONS}
        answers = portal_store.answers(current["tenant_id"])
        expected = next((key for key, _ in ONBOARDING_QUESTIONS if key not in answers), None)
        if question_key not in allowed or (
            question_key != expected and question_key not in answers
        ):
            raise HTTPException(status.HTTP_409_CONFLICT, "Answer the current question first")
        portal_store.save_answer(current["tenant_id"], question_key, answer)
        snapshot = portal_store.snapshot(current["tenant_id"])
        if (
            len(cast(dict[str, str], snapshot["answers"])) == len(ONBOARDING_QUESTIONS)
            and snapshot["analysis_status"] == "ready"
        ):
            try:
                refresh_proposal(current["tenant_id"])
            except RuntimeError as exc:
                raise HTTPException(
                    status.HTTP_503_SERVICE_UNAVAILABLE, "Agent is temporarily unavailable"
                ) from exc
        updated_answers = portal_store.answers(current["tenant_id"])
        next_key = next(
            (key for key, _ in ONBOARDING_QUESTIONS if key not in updated_answers), None
        )
        destination = f"/onboarding?question={next_key}" if next_key else "/onboarding?review=true"
        return RedirectResponse(destination, status_code=status.HTTP_303_SEE_OTHER)

    @router.post("/onboarding/refine")
    def refine_proposal(
        answer: Annotated[str, Form()],
        csrf_token: Annotated[str, Form()],
        positions_session: Annotated[str | None, Cookie()] = None,
    ) -> RedirectResponse:
        current = session(positions_session)
        csrf(current, csrf_token)
        if portal_store.state(current["tenant_id"]) != "resume_uploaded":
            raise HTTPException(status.HTTP_409_CONFLICT, "The approved plan is locked")
        portal_store.save_answer(current["tenant_id"], "refinement", answer)
        try:
            refresh_proposal(current["tenant_id"])
        except RuntimeError as exc:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE, "Agent is temporarily unavailable"
            ) from exc
        return RedirectResponse("/onboarding", status_code=status.HTTP_303_SEE_OTHER)

    @router.post("/onboarding/finalize")
    def finalize_onboarding(
        csrf_token: Annotated[str, Form()],
        positions_session: Annotated[str | None, Cookie()] = None,
    ) -> RedirectResponse:
        current = session(positions_session)
        csrf(current, csrf_token)
        snapshot = portal_store.snapshot(current["tenant_id"])
        proposal = snapshot["proposal"]
        evidence = cast(list[dict[str, object]], snapshot["evidence"])
        if not isinstance(proposal, dict) or not evidence:
            raise HTTPException(status.HTTP_409_CONFLICT, "Complete the Agent conversation first")
        try:
            if candidate_workspace is not None:
                _sync_approved_evidence(candidate_workspace, evidence)
            portal_store.approve(current["tenant_id"], "evidence")
            portal_store.approve(current["tenant_id"], "tracks")
            preferences = json.loads(str(proposal["preferences_json"]))
            portal_store.save_preferences(current["tenant_id"], **preferences)
        except (OSError, TypeError, ValueError) as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
        return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)

    @router.post("/onboarding/approve")
    def approve(
        kind: Annotated[str, Form()],
        csrf_token: Annotated[str, Form()],
        positions_session: Annotated[str | None, Cookie()] = None,
    ) -> RedirectResponse:
        current = session(positions_session)
        csrf(current, csrf_token)
        if kind == "evidence" and candidate_workspace is not None:
            snapshot = portal_store.snapshot(current["tenant_id"])
            evidence = cast(list[dict[str, object]], snapshot["evidence"])
            try:
                _sync_approved_evidence(candidate_workspace, evidence)
            except (OSError, ValueError) as exc:
                raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
        try:
            portal_store.approve(current["tenant_id"], kind)
        except ValueError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
        return RedirectResponse("/onboarding", status_code=status.HTTP_303_SEE_OTHER)

    @router.post("/onboarding/enrollment", response_class=HTMLResponse)
    def enrollment(
        request: Request,
        csrf_token: Annotated[str, Form()],
        positions_session: Annotated[str | None, Cookie()] = None,
    ) -> HTMLResponse:
        current = session(positions_session)
        csrf(current, csrf_token)
        if portal_store.state(current["tenant_id"]) != "integrations_configured":
            raise HTTPException(
                status.HTTP_409_CONFLICT, "Save search settings before connecting a computer"
            )
        token = browser_store.create_enrollment_token()
        server = str(request.base_url).rstrip("/")
        command = f"positions-client enroll --server {server} --token-stdin --device-id my-computer"
        return _page(
            "Connect this computer",
            f"<p>Run:</p><pre>{html.escape(command)}</pre><p>Paste this one-time token through stdin:</p><pre>{html.escape(token)}</pre><p>Then run <code>positions-client agent</code>.</p>",
            email=current["email"],
            csrf_token=current["csrf_token"],
        )

    @router.post("/settings")
    def save_settings(
        csrf_token: Annotated[str, Form()],
        locations: Annotated[str, Form()],
        countries: Annotated[str, Form()],
        languages: Annotated[str, Form()],
        employment_type: Annotated[str, Form()],
        sources: Annotated[str, Form()],
        positions_session: Annotated[str | None, Cookie()] = None,
    ) -> RedirectResponse:
        current = session(positions_session)
        csrf(current, csrf_token)

        def values(raw: str) -> list[str]:
            return list(dict.fromkeys(item.strip() for item in raw.split(",") if item.strip()))

        try:
            portal_store.save_preferences(
                current["tenant_id"],
                locations=values(locations),
                countries=values(countries),
                languages=values(languages),
                employment_type=employment_type,
                sources=values(sources),
            )
        except ValueError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
        return RedirectResponse("/onboarding", status_code=status.HTTP_303_SEE_OTHER)

    @router.get("/settings", response_class=HTMLResponse)
    def settings(positions_session: Annotated[str | None, Cookie()] = None) -> HTMLResponse:
        current = dashboard_session(positions_session)
        snapshot = portal_store.snapshot(current["tenant_id"])
        preferences = snapshot["preferences"]
        if isinstance(preferences, dict):
            rows = (
                ("Locations", ", ".join(json.loads(str(preferences["locations_json"])))),
                ("Countries", ", ".join(json.loads(str(preferences["countries_json"])))),
                ("Languages", ", ".join(json.loads(str(preferences["languages_json"])))),
                ("Employment", str(preferences["employment_type"]).replace("_", " ").title()),
                ("Sources", ", ".join(json.loads(str(preferences["sources_json"])))),
            )
            content = (
                "<ul class='list'>"
                + "".join(
                    f"<li><span class='muted'>{html.escape(label)}</span><strong>{html.escape(value or 'Not set')}</strong></li>"
                    for label, value in rows
                )
                + "</ul>"
            )
        else:
            content = "<div class='empty'><strong>No approved settings yet</strong><p>Complete setup to confirm search scope and sources.</p></div>"
        body = f"<section class='hero'><div><h2>Connections & settings</h2><p>Read-only account scope for this Web beta. Changes remain part of the guided setup flow.</p></div></section><article class='card'><div class='card-head'><h2>Search settings</h2><a class='card-link' href='/onboarding'>Open setup</a></div>{content}</article>"
        return _page(
            "Settings",
            body,
            email=current["email"],
            csrf_token=current["csrf_token"],
            active_path="/settings",
            eyebrow="Account",
        )

    @router.get("/profile", response_class=HTMLResponse)
    def profile(positions_session: Annotated[str | None, Cookie()] = None) -> HTMLResponse:
        current = dashboard_session(positions_session)
        snapshot = portal_store.snapshot(current["tenant_id"])
        tracks = cast(list[dict[str, object]], snapshot["tracks"])
        preferences = snapshot["preferences"]
        approved = ONBOARDING_STATES.index(str(snapshot["state"])) >= ONBOARDING_STATES.index(
            "tracks_approved"
        )
        items = (
            "".join(
                f"<li><span><strong>{html.escape(str(item['label']))}</strong><br><small>{html.escape(', '.join(json.loads(str(item['keywords_json']))) or 'Semantic matching')}</small></span><span class='pill'>{html.escape(str(item['mode']).capitalize())}</span></li>"
                for item in tracks
            )
            or "<div class='empty'><strong>No directions yet</strong><p>Finish setup to approve your Agent proposal.</p></div>"
        )
        scope = "Not configured"
        if isinstance(preferences, dict):
            scope = ", ".join(json.loads(str(preferences["locations_json"]))) or scope
        heading = "Approved search strategy" if approved else "Proposed search strategy"
        description = (
            "These directions are approved for your search."
            if approved
            else "Review and confirm these Agent suggestions during setup."
        )
        body = f"<section class='hero'><div><h2>{heading}</h2><p>{description}</p></div></section><article class='card'><div class='card-head'><h2>Directions</h2><span class='pill'>{len(tracks)} total</span></div><ul class='list'>{items}</ul></article><article class='card' style='margin-top:16px'><h2>Search scope</h2><p>{html.escape(scope)}</p></article>"
        return _page(
            "Search directions",
            body,
            email=current["email"],
            csrf_token=current["csrf_token"],
            active_path="/profile",
            eyebrow="Strategy",
        )

    @router.get("/jobs", response_class=HTMLResponse)
    def jobs(
        q: str = "",
        source: Annotated[list[str] | None, Query()] = None,
        decision: Annotated[list[str] | None, Query()] = None,
        location: str = "",
        sort: str = "first_seen_at",
        order: str = "desc",
        page: int = 1,
        page_size: int = 50,
        positions_session: Annotated[str | None, Cookie()] = None,
    ) -> HTMLResponse:
        current = dashboard_session(positions_session)
        allowed_sorts = {
            "title": "normalized_title",
            "company": "company_name",
            "location": "location_text",
            "source": "source",
            "decision": "application_status",
            "first_seen_at": "first_seen_at",
        }
        if sort not in allowed_sorts or order not in {"asc", "desc"}:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Unsupported table sort")
        if page < 1 or page_size not in {25, 50, 100}:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Invalid table page")
        rows: list[dict[str, object]] = []
        total = 0
        if job_db is not None and job_db.is_file():
            clauses: list[str] = []
            parameters: list[object] = []
            cleaned_q = q.strip()[:200]
            if cleaned_q:
                clauses.append(
                    "(lower(j.normalized_title) LIKE ? OR lower(j.company_name) LIKE ? OR lower(j.location_text) LIKE ? OR lower(j.description_full) LIKE ?)"
                )
                term = f"%{cleaned_q.casefold()}%"
                parameters.extend([term, term, term, term])
            sources = list(dict.fromkeys((source or [])[:20]))
            if sources:
                clauses.append(f"j.source IN ({','.join('?' for _ in sources)})")
                parameters.extend(sources)
            decisions = list(dict.fromkeys((decision or [])[:10]))
            if decisions:
                clauses.append(
                    f"coalesce(a.application_status,'new') IN ({','.join('?' for _ in decisions)})"
                )
                parameters.extend(decisions)
            if location.strip():
                clauses.append("lower(j.location_text) LIKE ?")
                parameters.append(f"%{location.strip().casefold()[:120]}%")
            where = " WHERE " + " AND ".join(clauses) if clauses else ""
            connection = sqlite3.connect(f"file:{job_db.resolve()}?mode=ro", uri=True)
            connection.row_factory = sqlite3.Row
            try:
                total = int(
                    connection.execute(
                        f"SELECT count(*) FROM jobs j LEFT JOIN application_state a ON a.job_id=j.id{where}",
                        parameters,
                    ).fetchone()[0]
                )
                query = f"""SELECT j.id,j.normalized_title,j.company_name,j.location_text,j.source,
                    j.first_seen_at,coalesce(a.application_status,'new') application_status
                    FROM jobs j LEFT JOIN application_state a ON a.job_id=j.id{where}
                    ORDER BY {allowed_sorts[sort]} {order.upper()},j.id {order.upper()} LIMIT ? OFFSET ?"""
                rows = [
                    dict(row)
                    for row in connection.execute(
                        query, [*parameters, page_size, (page - 1) * page_size]
                    )
                ]
            finally:
                connection.close()
        sources_value = source or []
        decisions_value = decision or []
        decision_options = "".join(
            f'<option value="{value}"{selected}>{label}</option>'
            for value, label in (
                ("new", "Review"),
                ("interested", "Accept"),
                ("not_interested", "Drop"),
            )
            for selected in [" selected" if value in decisions_value else ""]
        )
        row_options = "".join(
            f"<option{selected}>{size}</option>"
            for size in (25, 50, 100)
            for selected in [" selected" if size == page_size else ""]
        )
        source_value = sources_value[0] if sources_value else ""
        filters = f'''<form class="job-filters" method="get"><label>Search<input name="q" value="{html.escape(q)}" placeholder="Title, company or location"></label><label>Location<input name="location" value="{html.escape(location)}"></label><label>Source<input name="source" value="{html.escape(source_value)}"></label><label>Decision<select name="decision"><option value="">All</option>{decision_options}</select></label><label>Rows<select name="page_size">{row_options}</select></label><button>Filter</button><a class="button secondary" href="/jobs">Clear</a></form>'''

        def jobs_url(**changes: object) -> str:
            values: dict[str, object] = {
                "q": q,
                "location": location,
                "source": sources_value,
                "decision": decisions_value,
                "sort": sort,
                "order": order,
                "page": page,
                "page_size": page_size,
            }
            values.update(changes)
            compact = {key: value for key, value in values.items() if value not in ("", [], None)}
            return "/jobs?" + urlencode(compact, doseq=True)

        if not rows:
            body = (
                filters
                + "<div class='card empty'><strong>No matching jobs</strong><p>Adjust the filters or wait for the first completed acquisition run.</p></div>"
            )
        else:

            def sort_link(key: str, label: str) -> str:
                next_order = "asc" if sort != key or order == "desc" else "desc"
                target = html.escape(jobs_url(sort=key, order=next_order, page=1), quote=True)
                return f'<a href="{target}">{label}</a>'

            body = (
                filters
                + f"<div class='table-meta'><strong>{total} jobs</strong><span>Page {page}</span></div><div class='card table-wrap'><table><thead><tr><th>{sort_link('title', 'Title')}</th><th>{sort_link('company', 'Company')}</th><th>{sort_link('location', 'Location')}</th><th>{sort_link('source', 'Source')}</th><th>{sort_link('decision', 'Decision')}</th><th>{sort_link('first_seen_at', 'First seen')}</th></tr></thead><tbody>"
            )
            for row in rows:
                body += (
                    "<tr>"
                    + "".join(
                        f"<td>{html.escape(str(row.get(key) or '—'))}</td>"
                        for key in (
                            "normalized_title",
                            "company_name",
                            "location_text",
                            "source",
                            "application_status",
                            "first_seen_at",
                        )
                    )
                    + "</tr>"
                )
            previous = (
                f'<a class="button secondary" href="{html.escape(jobs_url(page=page - 1), quote=True)}">Previous</a>'
                if page > 1
                else ""
            )
            following = (
                f'<a class="button secondary" href="{html.escape(jobs_url(page=page + 1), quote=True)}">Next</a>'
                if page * page_size < total
                else ""
            )
            body += f"</tbody></table></div><div class='pagination'>{previous}{following}</div><p class='muted'>Job application is never automatic.</p>"
        return _page(
            "Jobs",
            body,
            email=current["email"],
            csrf_token=current["csrf_token"],
            active_path="/jobs",
            eyebrow="Screening",
        )

    @router.get("/runs", response_class=HTMLResponse)
    def runs(positions_session: Annotated[str | None, Cookie()] = None) -> HTMLResponse:
        current = dashboard_session(positions_session)
        files: list[Path] = []
        if candidate_workspace is not None:
            root = candidate_workspace / "analysis/fine-screen"
            files = sorted(
                (
                    path
                    for path in root.glob("*")
                    if path.is_file() and path.suffix in {".json", ".csv"}
                ),
                key=lambda path: path.stat().st_mtime,
                reverse=True,
            )[:100]
        body = (
            "<ul>"
            + "".join(
                f"<li>{html.escape(path.name)} — {path.stat().st_size} bytes</li>" for path in files
            )
            + "</ul>"
        )
        return _page(
            "Activity",
            f"<article class='card'>{body or '<p>No runs yet.</p>'}</article>",
            email=current["email"],
            csrf_token=current["csrf_token"],
            active_path="/runs",
            eyebrow="Operations",
        )

    @router.get("/resumes", response_class=HTMLResponse)
    def resumes(positions_session: Annotated[str | None, Cookie()] = None) -> HTMLResponse:
        current = dashboard_session(positions_session)
        files: list[Path] = []
        if candidate_workspace is not None:
            root = candidate_workspace / "CV/Fine-Screened"
            files = sorted(root.glob("*.pdf"), key=lambda path: path.stat().st_mtime, reverse=True)[
                :100
            ]
        body = (
            "<ul>"
            + "".join(
                f'<li><a href="/resumes/{html.escape(path.name)}">{html.escape(path.name)}</a></li>'
                for path in files
            )
            + "</ul>"
        )
        return _page(
            "CV & evidence",
            f"<article class='card'>{body or '<p>No generated resumes yet.</p>'}</article>",
            email=current["email"],
            csrf_token=current["csrf_token"],
            active_path="/resumes",
            eyebrow="Documents",
        )

    @router.get("/resumes/{filename}")
    def resume_download(
        filename: str, positions_session: Annotated[str | None, Cookie()] = None
    ) -> FileResponse:
        dashboard_session(positions_session)
        if (
            candidate_workspace is None
            or Path(filename).name != filename
            or not filename.endswith(".pdf")
        ):
            raise HTTPException(status.HTTP_404_NOT_FOUND)
        root = (candidate_workspace / "CV/Fine-Screened").resolve()
        target = (root / filename).resolve()
        if not target.is_relative_to(root) or not target.is_file():
            raise HTTPException(status.HTTP_404_NOT_FOUND)
        return FileResponse(target, media_type="application/pdf", filename=filename)

    return router
