"""CLI glue for one tenant's browser API, queue, and outbox."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import uvicorn

from job_scraper.adapters.server.browser_task_server import (
    create_app,
    drain_outbox,
    refresh_search_tasks,
)
from job_scraper.adapters.storage.browser_task_store import BrowserTaskStore
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
    uvicorn.run(create_app(store=_resolve_store(args)), host=args.host, port=args.port, workers=1)
    return 0


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
