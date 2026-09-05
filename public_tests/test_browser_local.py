from __future__ import annotations

import csv
import io
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from job_scraper.adapters.storage.browser_task_store import BrowserTaskStore
from job_scraper.cli.main import main


def invoke(capsys, workspace: Path, *arguments: str, code: int = 0):
    assert main(["browser", "local", *arguments, "--workspace", str(workspace)]) == code
    return json.loads(capsys.readouterr().out)


def enqueue(capsys, workspace: Path, query: str = "Fictional Engineer"):
    return invoke(
        capsys,
        workspace,
        "search",
        "--query",
        query,
        "--location",
        "Example City",
        "--country",
        "DE",
        "--max-results",
        "1",
    )


def result_file(workspace: Path, **fields):
    path = workspace / "result.json"
    path.write_text(
        json.dumps({"observed_at": datetime.now(UTC).isoformat(), **fields}), encoding="utf-8"
    )
    return path


def complete(capsys, workspace, claim, path, code=0):
    return invoke(
        capsys,
        workspace,
        "complete",
        "--task-id",
        claim["task_id"],
        f"--lease-id={claim['lease_id']}",
        "--result",
        str(path),
        code=code,
    )


def search_result(workspace, suffix="fictional1"):
    return result_file(
        workspace,
        status="complete",
        cards=[
            {
                "url": f"https://de.indeed.com/viewjob?jk={suffix}",
                "title": "Fictional Engineer",
                "company_name": "Example Ltd",
                "location_raw": "Example City",
                "context": "Visible card",
            }
        ],
    )


def test_full_local_flow_without_config_or_network(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("JOB_SCRAPER_CONFIG_DIR", str(tmp_path / "nonexistent"))
    monkeypatch.setattr(
        "job_scraper.adapters.storage.browser_task_store.secrets.token_urlsafe",
        lambda _: "--fictional-lease",
    )
    workspace = tmp_path / "with spaces"
    queued = enqueue(capsys, workspace)
    assert queued["created"]
    assert not enqueue(capsys, workspace)["created"]
    claim = invoke(capsys, workspace, "claim")
    assert claim["kind"] == "search"
    assert invoke(capsys, workspace, "claim")["lease_id"] == claim["lease_id"]
    invoke(
        capsys,
        workspace,
        "heartbeat",
        "--task-id",
        claim["task_id"],
        f"--lease-id={claim['lease_id']}",
    )
    path = search_result(workspace)
    assert not complete(capsys, workspace, claim, path)["replayed"]
    assert complete(capsys, workspace, claim, path)["replayed"]
    detail = invoke(capsys, workspace, "claim")
    assert detail["kind"] == "detail"
    description = (
        "Fictional full description with detailed responsibilities and qualifications. " * 8
    )
    path = result_file(
        workspace,
        status="complete",
        title="=FORMULA()",
        company_name="Example Ltd",
        location_raw="Example City",
        description=description,
    )
    complete(capsys, workspace, detail, path)
    assert invoke(capsys, workspace, "claim", code=4)["empty"]
    exported = invoke(capsys, workspace, "export")
    with Path(exported["csv"]).open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 1 and rows[0]["description"] == description
    assert rows[0]["title"] == "'=FORMULA()"
    assert invoke(capsys, workspace, "status")["jobs"] == 1
    assert not (tmp_path / "nonexistent").exists()


@pytest.mark.parametrize(
    "extra",
    [
        {"url": "https://example.com"},
        {"status": "in_progress"},
        {"observed_at": "2026-01-01T00:00:00"},
        {"error": "unknown", "status": "blocked"},
        {"status": ["complete"]},
    ],
)
def test_rejects_invalid_result_without_consuming_lease(tmp_path, capsys, extra):
    enqueue(capsys, tmp_path)
    claim = invoke(capsys, tmp_path, "claim")
    path = result_file(tmp_path, **{"status": "complete", "cards": [], **extra})
    assert "error" in complete(capsys, tmp_path, claim, path, code=2)
    assert invoke(capsys, tmp_path, "claim")["lease_id"] == claim["lease_id"]


def test_blocked_page_and_expired_lease(tmp_path, capsys):
    enqueue(capsys, tmp_path)
    stale = invoke(capsys, tmp_path, "claim")
    store = BrowserTaskStore(tmp_path / "browser_tasks.db")
    with store.connect() as connection:
        connection.execute(
            "UPDATE browser_tasks SET lease_expires_at=?",
            ((datetime.now(UTC) - timedelta(seconds=1)).isoformat(),),
        )
    claim = invoke(capsys, tmp_path, "claim")
    assert claim["lease_id"] != stale["lease_id"]
    path = result_file(tmp_path, status="blocked", error="captcha")
    complete(capsys, tmp_path, stale, path, code=2)
    complete(capsys, tmp_path, claim, path)
    assert invoke(capsys, tmp_path, "status")["tasks"]["blocked"] == 1
    assert invoke(capsys, tmp_path, "export")["jobs"] == 0
    assert not enqueue(capsys, tmp_path)["created"]


def test_result_bound_and_partial_export_recovery(tmp_path, capsys, monkeypatch):
    enqueue(capsys, tmp_path)
    claim = invoke(capsys, tmp_path, "claim")
    path = search_result(tmp_path)
    body = json.loads(path.read_text())
    body["cards"].append({**body["cards"][0], "url": "https://de.indeed.com/viewjob?jk=fictional2"})
    path.write_text(json.dumps(body))
    complete(capsys, tmp_path, claim, path, code=2)
    path = search_result(tmp_path)
    from job_scraper.cli import browser_local

    original = browser_local._drain
    calls = 0

    def interrupted(store):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("Simulated interruption after durable completion")
        original(store)

    monkeypatch.setattr(browser_local, "_drain", interrupted)
    complete(capsys, tmp_path, claim, path, code=2)
    monkeypatch.setattr(browser_local, "_drain", original)
    assert invoke(capsys, tmp_path, "claim")["kind"] == "detail"


def test_cumulative_export_keeps_previous_jobs(tmp_path, capsys):
    for number in range(2):
        enqueue(capsys, tmp_path, f"Fictional Engineer {number}")
        claim = invoke(capsys, tmp_path, "claim")
        complete(capsys, tmp_path, claim, search_result(tmp_path, f"fictional{number}"))
        detail = invoke(capsys, tmp_path, "claim")
        path = result_file(
            tmp_path,
            status="complete",
            title="Fictional Engineer",
            company_name="Example",
            location_raw="Example City",
            description="Detailed fictional responsibilities. " * 10,
        )
        complete(capsys, tmp_path, detail, path)
        assert invoke(capsys, tmp_path, "export")["jobs"] == number + 1


def test_local_output_on_windows_legacy_console(tmp_path, monkeypatch):
    import importlib

    cli = importlib.import_module("job_scraper.cli.main")
    monkeypatch.setattr(cli, "load_dotenv", lambda: pytest.fail("Local mode read dotenv"))
    buffer = io.BytesIO()
    console = io.TextIOWrapper(buffer, encoding="gbk")
    monkeypatch.setattr("sys.stdout", console)
    assert (
        main(
            [
                "browser",
                "local",
                "search",
                "--query",
                "Engineer €",
                "--location",
                "Example City",
                "--country",
                "DE",
                "--workspace",
                str(tmp_path),
            ]
        )
        == 0
    )
    console.flush()
    assert json.loads(buffer.getvalue().decode("gbk"))["created"]
