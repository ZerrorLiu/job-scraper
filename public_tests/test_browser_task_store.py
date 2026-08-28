from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from job_scraper.adapters.storage.browser_task_store import (
    BrowserTaskStore,
    BrowserTaskStoreError,
    LostLeaseError,
)


def _store(tmp_path: Path) -> BrowserTaskStore:
    store = BrowserTaskStore(tmp_path / "browser_tasks.db")
    store.initialize()
    return store


def test_claim_is_atomic_across_connections(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.enqueue("search", "task-1", {"url": "https://example.test"})
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(lambda _: BrowserTaskStore(store.path).claim("search"), range(2)))
    assert sum(claim is not None for claim in claims) == 1


def test_expired_lease_is_reclaimed_and_stale_heartbeat_fails(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.enqueue("detail", "task-1", {"url": "https://example.test"})
    first = store.claim("detail")
    assert first is not None
    with sqlite3.connect(store.path) as connection:
        connection.execute(
            "UPDATE browser_tasks SET lease_expires_at='2000-01-01T00:00:00+00:00' WHERE task_id='task-1'"
        )
    second = store.claim("detail")
    assert second is not None and second.lease_id != first.lease_id
    with pytest.raises(LostLeaseError):
        store.heartbeat("task-1", first.lease_id)


def test_completion_and_outbox_are_atomic_and_replayable(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.enqueue("detail", "task-1", {"url": "https://example.test"})
    claim = store.claim("detail")
    assert claim is not None
    first = store.complete(
        "task-1",
        claim.lease_id,
        idempotency_key="submission-1",
        status="complete",
        result={"status": "complete"},
    )
    assert not first.replayed
    assert len(store.list_outbox("pending")) == 1
    replay = store.complete(
        "task-1",
        claim.lease_id,
        idempotency_key="submission-1",
        status="complete",
        result={"status": "complete"},
    )
    assert replay.replayed and len(store.list_outbox()) == 1
    with pytest.raises(BrowserTaskStoreError, match="another idempotency"):
        store.complete(
            "task-1", claim.lease_id, idempotency_key="different-key", status="complete", result={}
        )


def test_outbox_failure_becomes_poison_then_can_be_retried(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.enqueue("search", "task-1", {})
    claim = store.claim("search")
    assert claim is not None
    outcome = store.complete(
        "task-1",
        claim.lease_id,
        idempotency_key="submission-1",
        status="blocked",
        result={"status": "blocked"},
    )
    for expected_attempt in range(1, 6):
        event = store.claim_outbox(event_id=outcome.event_id)
        assert event is not None and event.attempts == expected_attempt
        state = store.fail_outbox(event.event_id, "fictional failure")
        if expected_attempt < 5:
            with sqlite3.connect(store.path) as connection:
                connection.execute("UPDATE browser_outbox SET next_attempt_at='2000-01-01'")
    assert state == "failed"
    assert store.retry_outbox(outcome.event_id)


def test_stale_processing_outbox_is_recovered_after_restart(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.enqueue("search", "task-1", {})
    claim = store.claim("search")
    assert claim is not None
    outcome = store.complete(
        "task-1",
        claim.lease_id,
        idempotency_key="submission-1",
        status="blocked",
        result={"status": "blocked"},
    )
    assert store.claim_outbox() is not None
    with sqlite3.connect(store.path) as connection:
        connection.execute("UPDATE browser_outbox SET updated_at='2000-01-01'")
    recovered = BrowserTaskStore(store.path).claim_outbox(event_id=outcome.event_id)
    assert recovered is not None and recovered.attempts == 2


def test_enrollment_is_single_use_single_active_worker_and_revocable(tmp_path: Path) -> None:
    store = _store(tmp_path)
    token = store.create_enrollment_token()
    device_token = store.redeem_enrollment_token(token, device_id="fictional-device")
    assert store.verify_device_token(device_token)
    with pytest.raises(BrowserTaskStoreError, match="already redeemed"):
        store.redeem_enrollment_token(token, device_id="other-device")
    other = store.create_enrollment_token()
    with pytest.raises(BrowserTaskStoreError, match="already has an active"):
        store.redeem_enrollment_token(other, device_id="other-device")
    assert store.revoke_device("fictional-device")
    assert not store.verify_device_token(device_token)
