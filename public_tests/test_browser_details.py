from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from job_scraper.integrations.browser_details import (
    MIN_DESCRIPTION_LENGTH,
    BrowserDetailContractError,
    BrowserDetailResult,
    BrowserDetailTask,
)
from job_scraper.integrations.email_recommendations import EmailJobCandidate
from job_scraper.jobs.ingest_email_recommendations import (
    claim_browser_detail_task,
    merge_browser_detail_queue,
    parse_args,
)


def _candidate() -> EmailJobCandidate:
    return EmailJobCandidate(
        url="https://de.indeed.com/viewjob?jk=fictional-browser-1&utm=ignored",
        title="Platform Engineer Ø",
        company_name="Example GmbH",
        location_raw="Berlin",
        context="A fictional recommendation card.",
        message_id="<fictional@example.test>",
        email_subject="Fictional recommendation",
        email_from="alerts@example.test",
        email_date=datetime(2026, 8, 27, tzinfo=UTC),
        anchor_text="Platform Engineer",
    )


def _complete_payload() -> dict[str, object]:
    payload = BrowserDetailTask.from_candidate(_candidate()).to_dict()
    payload.update(
        {
            "status": "complete",
            "description": "D" * MIN_DESCRIPTION_LENGTH,
        }
    )
    return payload


def test_browser_task_canonicalizes_indeed_url_and_is_stable() -> None:
    task = BrowserDetailTask.from_candidate(_candidate())

    assert task.url == "https://de.indeed.com/viewjob?jk=fictional-browser-1"
    assert task.task_id == BrowserDetailTask.from_candidate(_candidate()).task_id
    assert task.to_dict()["status"] == "pending"


def test_complete_browser_result_requires_full_detail() -> None:
    result = BrowserDetailResult.from_mapping(_complete_payload())

    assert result.is_complete
    assert result.task.to_candidate().url == result.task.url


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"description": "too short"}, "at least"),
        ({"description": "Please sign in to Indeed to continue. " * 8}, "login or blocking"),
        ({"task_id": "wrong"}, "task_id"),
        ({"status": "blocked", "error": ""}, "missing error"),
    ],
)
def test_browser_result_rejects_incomplete_or_untrusted_payload(
    change: dict[str, object], message: str
) -> None:
    payload = _complete_payload()
    payload.update(change)

    with pytest.raises(BrowserDetailContractError, match=message):
        BrowserDetailResult.from_mapping(payload)


def test_browser_queue_claim_allows_only_one_in_progress_task(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    queue_path = tmp_path / "browser-queue.jsonl"
    task = BrowserDetailTask.from_candidate(_candidate()).to_dict()
    queue_path.write_text(json.dumps(task) + "\n", encoding="utf-8")

    assert claim_browser_detail_task(queue_path) == 0
    assert "\\u" in capsys.readouterr().out
    assert claim_browser_detail_task(queue_path) == 2
    claimed = json.loads(queue_path.read_text(encoding="utf-8"))

    assert claimed["status"] == "in_progress"
    assert claimed["lease_started_at"]


def test_browser_sender_scope_is_available_only_as_an_explicit_queue_option() -> None:
    args = parse_args(
        [
            "--browser-queue",
            "local/queue.jsonl",
            "--browser-sender",
            "indeed",
        ]
    )

    assert args.browser_queue == Path("local/queue.jsonl")
    assert args.browser_senders == ["indeed"]


def test_browser_queue_deduplicates_repeated_email_cards_by_canonical_task() -> None:
    pending = BrowserDetailTask.from_candidate(_candidate()).to_dict()
    completed = dict(pending)
    completed["status"] = "complete"
    completed["description"] = "D" * MIN_DESCRIPTION_LENGTH
    queue = merge_browser_detail_queue([_candidate(), _candidate()], [pending, completed])

    assert len(queue) == 1
    assert queue[0]["status"] == "complete"
