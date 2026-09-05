"""CLI glue for one tenant's browser API, queue, and outbox."""

from __future__ import annotations

import argparse
import json
import os
import smtplib
from email.message import EmailMessage
from pathlib import Path
from urllib.parse import urlsplit

import uvicorn

from job_scraper.adapters.processors.codex_resume_analyzer import CodexResumeAnalyzer
from job_scraper.adapters.server.browser_task_server import (
    create_app,
    drain_outbox,
    refresh_search_tasks,
)
from job_scraper.adapters.server.google_oauth import GoogleOAuthClient
from job_scraper.adapters.storage.browser_task_store import BrowserTaskStore
from job_scraper.adapters.storage.portal_store import PortalStore
from job_scraper.configuration.loader import get_config_root
from job_scraper.integrations.email_recommendations import load_email_ingest_config
from job_scraper.jobs.ingest_email_recommendations import (
    browser_email_tasks,
    default_email_config_path,
)


def _default_store_path() -> Path:
    return get_config_root().parent / "data" / "browser_tasks.db"


def _resolve_store(args: argparse.Namespace) -> BrowserTaskStore:
    store = BrowserTaskStore(Path(args.db) if args.db else _default_store_path())
    store.initialize()
    return store


def serve(args: argparse.Namespace) -> int:
    store = _resolve_store(args)
    portal_store = None
    sender = None
    upload_root = None
    analyzer = None
    google_oauth = None
    if args.portal_db:
        if not args.upload_root or not args.public_url:
            raise ValueError("--portal-db requires --upload-root and --public-url")
        portal_store = PortalStore(Path(args.portal_db))
        portal_store.initialize()
        upload_root = Path(args.upload_root).resolve()
        public_url = args.public_url.rstrip("/")
        public_parts = urlsplit(public_url)
        if not public_parts.hostname or public_parts.scheme not in {"http", "https"}:
            raise ValueError("--public-url must be an absolute HTTP(S) URL")
        if public_parts.scheme != "https" and not args.insecure_cookie:
            raise ValueError("Production --public-url must use HTTPS")
        if args.dev_print_login_link:

            def print_login_link(email: str, path: str) -> None:
                print(f"LOGIN {email} {public_url}{path}")

            sender = print_login_link
        else:
            sender = _smtp_sender(public_url)
        if not args.dev_basic_resume_analysis:
            configured_homes = tuple(
                Path(value).expanduser().resolve()
                for value in os.environ.get("POSITIONS_CODEX_HOMES", "").split(os.pathsep)
                if value.strip()
            )
            analyzer = CodexResumeAnalyzer(
                model=args.resume_analysis_model, codex_homes=configured_homes
            )
        google_client_id = os.environ.get("POSITIONS_GOOGLE_OAUTH_CLIENT_ID", "").strip()
        google_client_secret = os.environ.get("POSITIONS_GOOGLE_OAUTH_CLIENT_SECRET", "").strip()
        if bool(google_client_id) != bool(google_client_secret):
            raise ValueError(
                "Set both POSITIONS_GOOGLE_OAUTH_CLIENT_ID and POSITIONS_GOOGLE_OAUTH_CLIENT_SECRET"
            )
        if google_client_id:
            google_oauth = GoogleOAuthClient(
                client_id=google_client_id,
                client_secret=google_client_secret,
                redirect_uri=f"{public_url}/auth/google/callback",
            )
    uvicorn.run(
        create_app(
            store=store,
            portal_store=portal_store,
            upload_root=upload_root,
            send_login=sender,
            resume_analyzer=analyzer,
            google_oauth=google_oauth,
            candidate_workspace=(
                Path(args.candidate_workspace).resolve() if args.candidate_workspace else None
            ),
            job_db=(Path(args.job_db).resolve() if args.job_db else None),
            allowed_hosts=(
                [urlsplit(args.public_url).hostname or "", "127.0.0.1", "localhost"]
                if args.public_url
                else None
            ),
            secure_cookie=not args.insecure_cookie,
        ),
        host=args.host,
        port=args.port,
        workers=1,
    )
    return 0


def _smtp_sender(public_url: str):
    host = os.environ.get("POSITIONS_SMTP_HOST", "")
    sender_address = os.environ.get("POSITIONS_SMTP_FROM", "")
    if not host or not sender_address:
        raise ValueError("Set POSITIONS_SMTP_HOST and POSITIONS_SMTP_FROM")
    port = int(os.environ.get("POSITIONS_SMTP_PORT", "587"))
    username = os.environ.get("POSITIONS_SMTP_USERNAME")
    password = os.environ.get("POSITIONS_SMTP_PASSWORD")

    def send(recipient: str, path: str) -> None:
        message = EmailMessage()
        message["Subject"] = "Your Positions sign-in link"
        message["From"] = sender_address
        message["To"] = recipient
        message.set_content(
            f"Sign in to Positions:\n\n{public_url}{path}\n\nThis link expires in 15 minutes."
        )
        with smtplib.SMTP(host, port, timeout=30) as smtp:
            smtp.starttls()
            if username and password:
                smtp.login(username, password)
            smtp.send_message(message)

    return send


def serve_enroll_token(args: argparse.Namespace) -> int:
    print(_resolve_store(args).create_enrollment_token(ttl_seconds=args.ttl_seconds))
    return 0


def browser_search_refresh(args: argparse.Namespace) -> int:
    store = _resolve_store(args)
    config = load_email_ingest_config(args.config or default_email_config_path())
    print(json.dumps({"created": refresh_search_tasks(store, config)}))
    return 0


def browser_email_refresh(args: argparse.Namespace) -> int:
    store = _resolve_store(args)
    config = load_email_ingest_config(args.config or default_email_config_path())
    created = sum(
        store.enqueue("detail", task.task_id, task.to_dict())
        for task in browser_email_tasks(config)
    )
    print(json.dumps({"created": created}))
    return 0


def browser_status(args: argparse.Namespace) -> int:
    print(json.dumps(_resolve_store(args).status(), sort_keys=True))
    return 0


def browser_revoke_device(args: argparse.Namespace) -> int:
    changed = int(_resolve_store(args).revoke_device(args.device_id))
    if changed != args.expect_count:
        print(json.dumps({"error": "expect-count mismatch", "actual": changed}))
        return 2
    print(json.dumps({"revoked": changed, "device_id": args.device_id}))
    return 0


def browser_outbox_list(args: argparse.Namespace) -> int:
    print(json.dumps(_resolve_store(args).list_outbox(args.state), sort_keys=True))
    return 0


def browser_outbox_retry(args: argparse.Namespace) -> int:
    changed = int(_resolve_store(args).retry_outbox(args.event_id))
    if changed != args.expect_count:
        print(json.dumps({"error": "expect-count mismatch", "actual": changed}))
        return 2
    print(json.dumps({"retried": changed, "event_id": args.event_id}))
    return 0


def browser_outbox_run(args: argparse.Namespace) -> int:
    store = _resolve_store(args)
    config = load_email_ingest_config(args.config or default_email_config_path())
    applied, failed = drain_outbox(
        store=store, email_config=config, skip_notion=args.skip_notion, limit=args.limit
    )
    print(json.dumps({"applied": applied, "failed": failed}))
    return 1 if failed else 0
