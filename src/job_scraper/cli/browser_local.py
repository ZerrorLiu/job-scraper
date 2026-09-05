"""Same-machine browser transport; no server, credentials, or private config required."""

from __future__ import annotations

import argparse
import csv
import json
import os
import sqlite3
import tempfile
from dataclasses import asdict
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

from job_scraper.adapters.storage.browser_task_store import (
    BrowserTaskStore,
    BrowserTaskStoreError,
)
from job_scraper.collectors.data_integration_adapter import INDEED_MARKETS
from job_scraper.integrations.browser_details import (
    BrowserDetailResult,
    BrowserDetailTask,
    BrowserSearchResult,
    BrowserSearchTask,
)

BLOCK_REASONS = {
    "login_required",
    "captcha",
    "access_denied",
    "page_unavailable",
    "unexpected_layout",
    "navigation_outside_allowlist",
}
CSV_FIELDS = ("url", "title", "company_name", "location_raw", "description", "observed_at")


def add_local_parser(subparsers: argparse._SubParsersAction) -> None:
    local = subparsers.add_parser(
        "local", help="Collect Indeed in connected Chrome on this machine."
    )
    commands = local.add_subparsers(dest="local_command", required=True)
    for name in ("search", "claim", "heartbeat", "complete", "status", "export"):
        command = commands.add_parser(name)
        command.add_argument("--workspace", type=Path, default=Path("data/browser-local"))
        if name == "search":
            command.add_argument("--query", required=True)
            command.add_argument("--location", required=True)
            command.add_argument(
                "--country", required=True, type=str.upper, choices=sorted(INDEED_MARKETS)
            )
            command.add_argument(
                "--max-results", type=int, choices=range(1, 51), default=10, metavar="1..50"
            )
        if name in {"heartbeat", "complete"}:
            command.add_argument("--task-id", required=True)
            command.add_argument("--lease-id", required=True)
        if name == "complete":
            command.add_argument("--result", type=Path, required=True)


def _drain(store: BrowserTaskStore) -> None:
    while event := store.claim_outbox():
        try:
            if event.kind == "search":
                result = BrowserSearchResult.from_mapping(event.payload)
                for card in result.cards:
                    task = BrowserDetailTask.from_search_card(card)
                    store.enqueue("detail", task.task_id, task.to_dict())
            store.finish_outbox(event.event_id)
        except Exception as exc:
            store.fail_outbox(event.event_id, str(exc))
            raise


def _complete(store: BrowserTaskStore, args: argparse.Namespace) -> dict[str, object]:
    if args.result.stat().st_size > 2_000_000:
        raise ValueError("Result file exceeds 2 MB")
    submitted = json.loads(args.result.read_text(encoding="utf-8-sig"))
    if not isinstance(submitted, dict):
        raise ValueError("Result must be a JSON object")
    stored = store.get(args.task_id)
    if stored is None:
        raise ValueError("Unknown task ID")
    allowed = {"status", "observed_at", "error"}
    allowed |= (
        {"cards"}
        if stored["kind"] == "search"
        else {"title", "company_name", "location_raw", "description"}
    )
    if set(submitted) - allowed:
        raise ValueError("Result has unknown fields; task identity comes from the lease")
    for key, value in submitted.items():
        if key != "cards" and not isinstance(value, str):
            raise ValueError(f"{key} must be text")
    observed = submitted.get("observed_at")
    if not isinstance(observed, str):
        raise ValueError("observed_at must be an ISO timestamp with timezone")
    parsed = datetime.fromisoformat(observed.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("observed_at requires a timezone")
    if submitted.get("status") not in {"complete", "blocked", "unavailable"}:
        raise ValueError("Invalid result status")
    if submitted["status"] != "complete" and submitted.get("error") not in BLOCK_REASONS:
        raise ValueError("Unknown block reason")
    payload = stored["payload"]
    assert isinstance(payload, dict)
    merged = {**payload, **submitted}
    if stored["kind"] == "search":
        validated = BrowserSearchResult.from_mapping(merged)
        if len(validated.cards) > int(payload["max_results"]):
            raise ValueError("Search result exceeds the requested max_results")
    else:
        BrowserDetailResult.from_mapping(merged)
    encoded = json.dumps(submitted, sort_keys=True, ensure_ascii=False).encode()
    outcome = store.complete(
        args.task_id,
        args.lease_id,
        idempotency_key=sha256(encoded).hexdigest(),
        status=submitted["status"],
        result=merged,
    )
    _drain(store)
    return {"status": outcome.status, "replayed": outcome.replayed, "task_id": args.task_id}


def _safe_cell(value: object) -> str:
    text = str(value or "")
    return (
        "'" + text
        if text.lstrip().startswith(("=", "+", "-", "@")) or text.startswith(("\t", "\r", "\n"))
        else text
    )


def _export(store: BrowserTaskStore, workspace: Path) -> dict[str, object]:
    destination = workspace / "jobs.csv"
    latest: dict[str, dict[str, object]] = {}
    for result in store.completed_details():
        latest[str(result["url"])] = result
    fd, temporary = tempfile.mkstemp(prefix=".jobs-", suffix=".csv", dir=workspace)
    try:
        with os.fdopen(fd, "w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
            writer.writeheader()
            for url in sorted(latest):
                writer.writerow({field: _safe_cell(latest[url].get(field)) for field in CSV_FIELDS})
        os.replace(temporary, destination)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return {"jobs": len(latest), "csv": str(destination), "database": str(store.path)}


def run_local(args: argparse.Namespace) -> int:
    try:
        workspace = args.workspace.expanduser().resolve()
        workspace.mkdir(parents=True, exist_ok=True)
        store = BrowserTaskStore(workspace / "browser_tasks.db")
        store.initialize()
        _drain(store)
        output: object
        if args.local_command == "search":
            task = BrowserSearchTask.create(
                domain=INDEED_MARKETS[args.country],
                query=args.query,
                location=args.location,
                created_at=datetime.now(UTC),
            )
            payload = {**task.to_dict(), "max_results": args.max_results}
            created = store.enqueue("search", task.task_id, payload)
            output = {
                "created": created,
                "task_id": task.task_id,
                "url": task.url,
                "workspace": str(workspace),
                "next": "Use skills/positions-browser-worker/SKILL.md in your connected Chrome agent.",
            }
        elif args.local_command == "claim":
            claim = store.claim("detail", resume_active=True) or store.claim(
                "search", resume_active=True
            )
            if claim is None:
                print(json.dumps({"empty": True}))
                return 4
            output = {**asdict(claim), "contract_version": "1.0"}
        elif args.local_command == "heartbeat":
            output = {"lease_expires_at": store.heartbeat(args.task_id, args.lease_id)}
        elif args.local_command == "complete":
            output = _complete(store, args)
        elif args.local_command == "export":
            output = _export(store, workspace)
        else:
            output = {
                **store.status(),
                "workspace": str(workspace),
                "jobs": len(store.completed_details()),
            }
        print(json.dumps(output, ensure_ascii=True))
        return 0
    except (ValueError, OSError, sqlite3.Error, BrowserTaskStoreError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=True))
        return 2
