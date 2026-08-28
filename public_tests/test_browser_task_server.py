from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from job_scraper.adapters.server.browser_task_server import create_app, process_outbox_event
from job_scraper.adapters.storage.browser_task_store import BrowserTaskStore
from job_scraper.integrations.browser_details import BrowserSearchTask
from job_scraper.integrations.email_recommendations import EmailIngestConfig


def _setup(tmp_path: Path) -> tuple[TestClient, BrowserTaskStore, dict[str, str]]:
    store = BrowserTaskStore(tmp_path / "browser.db")
    store.initialize()
    token = store.create_enrollment_token()
    client = TestClient(create_app(store=store))
    enrolled = client.post(
        "/v1/enrollments/redeem",
        headers={"Authorization": f"Enrollment {token}"},
        json={"device_id": "fictional-device"},
    )
    assert enrolled.status_code == 200
    auth = {"Authorization": f"Bearer {enrolled.json()['device_token']}"}
    return client, store, auth


def test_auth_versioned_routes_and_transactional_search_expansion(tmp_path: Path) -> None:
    client, store, auth = _setup(tmp_path)
    assert client.post("/v1/browser/tasks/claim?kind=search", json={}).status_code == 401
    task = BrowserSearchTask.create(
        domain="de.indeed.com",
        query="Fictional Engineer",
        location="Example City",
        created_at=datetime.now(UTC),
    )
    store.enqueue("search", task.task_id, task.to_dict())
    claim = client.post("/v1/browser/tasks/claim?kind=search", headers=auth, json={}).json()
    assert claim["kind"] == "search" and claim["contract_version"] == "1.0"
    result = client.post(
        f"/v1/browser/tasks/{task.task_id}/results",
        headers=auth,
        json={
            "lease_id": claim["lease_id"],
            "idempotency_key": "search-result-1",
            "status": "complete",
            "observed_at": datetime.now(UTC).isoformat(),
            "cards": [
                {
                    "url": "https://de.indeed.com/viewjob?jk=fictional-1",
                    "title": "Fictional Engineer",
                    "company_name": "Example GmbH",
                    "location_raw": "Example City",
                    "context": "Visible fictional card",
                }
            ],
        },
    )
    assert result.status_code == 200
    assert (
        client.post("/v1/browser/tasks/claim?kind=detail", headers=auth, json={}).status_code == 204
    )
    event = store.claim_outbox()
    assert event is not None
    config = EmailIngestConfig(
        "", 993, "", "", "INBOX", True, 7, 10, [], [], tmp_path / "state.json", []
    )
    process_outbox_event(event, store=store, email_config=config, skip_notion=True)
    detail = client.post("/v1/browser/tasks/claim?kind=detail", headers=auth, json={})
    assert detail.status_code == 200
    assert detail.json()["payload"]["url"].endswith("jk=fictional-1")


def test_stale_lease_and_unknown_block_reason_fail_closed(tmp_path: Path) -> None:
    client, store, auth = _setup(tmp_path)
    task = BrowserSearchTask.create(
        domain="de.indeed.com",
        query="Fictional Engineer",
        location="Example City",
        created_at=datetime.now(UTC),
    )
    store.enqueue("search", task.task_id, task.to_dict())
    claim = client.post("/v1/browser/tasks/claim?kind=search", headers=auth, json={}).json()
    body = {
        "lease_id": "wrong",
        "idempotency_key": "blocked-result-1",
        "status": "blocked",
        "observed_at": datetime.now(UTC).isoformat(),
        "error": "captcha",
    }
    assert (
        client.post(
            f"/v1/browser/tasks/{task.task_id}/results", headers=auth, json=body
        ).status_code
        == 409
    )
    body["lease_id"] = claim["lease_id"]
    body["error"] = "some prose"
    assert (
        client.post(
            f"/v1/browser/tasks/{task.task_id}/results", headers=auth, json=body
        ).status_code
        == 422
    )
