from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from job_scraper.integrations.browser_details import (
    MIN_DESCRIPTION_LENGTH,
    BrowserDetailContractError,
    BrowserDetailResult,
    BrowserDetailTask,
    BrowserSearchResult,
    BrowserSearchTask,
)
from job_scraper.integrations.email_recommendations import EmailJobCandidate
from job_scraper.jobs.ingest_email_recommendations import (
    browser_search_domain,
    browser_search_tasks,
    claim_browser_detail_task,
    claim_browser_search_task,
    expand_browser_search_results,
    import_browser_detail_results,
    main,
    merge_browser_detail_queue,
    parse_args,
    raw_from_browser_detail,
)

_TRACK_CONFIG_TEMPLATE = """
[project]
timezone = "UTC"
database_path = "jobs.db"
overlap_hours = 1
export_dir = "exports"
track_label = "Fictional Browser Track"

[filters]
country = "DE"
include_keywords = ["c++", "qt"]
exclude_keywords = []
target_keywords = ["c++", "qt"]
target_match_scope = "combined"
full_time_only = false
allowed_description_languages = ["English", "German", "Mixed", "Unknown"]

[http]
user_agent = "fictional"
timeout_seconds = 10
base_delay_seconds = 1.0
jitter_seconds = 0.0
max_retries = 0

[sources.indeed_brightdata]
enabled = false
""".strip()


def _browser_detail_import_args(tmp_path: Path, detail_queue_path: Path) -> argparse.Namespace:
    email_config_path = tmp_path / "email.toml"
    email_config_path.write_text("[tracks]\nconfig_paths = []\n", encoding="utf-8")
    track_config_path = tmp_path / "track.toml"
    track_config_path.write_text(_TRACK_CONFIG_TEMPLATE, encoding="utf-8")
    return parse_args(
        [
            "--config",
            str(email_config_path),
            "--browser-results",
            str(detail_queue_path),
            "--track-config",
            str(track_config_path),
            "--skip-notion",
            "--skip-status-import",
        ]
    )


def _complete_browser_search_detail_task(
    *, jk: str, title: str, company_name: str, location_raw: str, description: str
) -> dict[str, object]:
    search_payload = _search_task().to_dict()
    search_payload.update(
        {
            "status": "complete",
            "cards": [
                {
                    "url": f"https://de.indeed.com/viewjob?jk={jk}",
                    "title": title,
                    "company_name": company_name,
                    "location_raw": location_raw,
                    "context": "Fictional visible search card.",
                }
            ],
        }
    )
    card = BrowserSearchResult.from_mapping(search_payload).cards[0]
    payload = BrowserDetailTask.from_search_card(card).to_dict()
    payload.update({"status": "complete", "description": description})
    return payload


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


def test_browser_queue_keeps_prior_tasks_not_in_the_latest_input() -> None:
    prior = BrowserDetailTask.from_candidate(_candidate()).to_dict()
    other = _candidate()
    other.url = "https://de.indeed.com/viewjob?jk=fictional-browser-2"
    queue = merge_browser_detail_queue([other], [prior])

    assert {row["task_id"] for row in queue} == {
        prior["task_id"],
        BrowserDetailTask.from_candidate(other).task_id,
    }


def _search_task() -> BrowserSearchTask:
    return BrowserSearchTask.create(
        domain="de.indeed.com",
        query="fictional platform engineer",
        location="Berlin",
        created_at=datetime(2026, 8, 27, tzinfo=UTC),
    )


def _completed_search_payload() -> dict[str, object]:
    payload = _search_task().to_dict()
    payload.update(
        {
            "status": "complete",
            "cards": [
                {
                    "url": "https://de.indeed.com/viewjob?jk=browser-discovery-1&utm=ignored",
                    "title": "Platform Engineer",
                    "company_name": "Example GmbH",
                    "location_raw": "Berlin",
                    "context": "Fictional visible search card.",
                }
            ],
        }
    )
    return payload


def test_browser_search_task_is_canonical_and_rejects_non_indeed_domain() -> None:
    task = _search_task()

    assert task.url == "https://de.indeed.com/jobs?q=fictional+platform+engineer&l=Berlin"
    assert task.task_id == _search_task().task_id
    with pytest.raises(BrowserDetailContractError, match="Indeed domain"):
        BrowserSearchTask.create(
            domain="example.test",
            query="Platform Engineer",
            location="Berlin",
            created_at=datetime(2026, 8, 27, tzinfo=UTC),
        )
    with pytest.raises(BrowserDetailContractError, match="Indeed domain"):
        BrowserSearchTask.create(
            domain="notindeed.com",
            query="Platform Engineer",
            location="Berlin",
            created_at=datetime(2026, 8, 27, tzinfo=UTC),
        )


def test_browser_search_market_uses_track_country_when_not_explicit() -> None:
    assert browser_search_domain({}, "Germany") == "de.indeed.com"
    assert browser_search_domain({}, "DE") == "de.indeed.com"


def test_browser_search_tasks_use_the_existing_indeed_track_matrix(tmp_path: Path) -> None:
    config_path = tmp_path / "track.toml"
    config_path.write_text(
        """
[project]
timezone = "UTC"
database_path = "jobs.db"
overlap_hours = 1
export_dir = "exports"

[filters]
country = "Germany"
include_keywords = ["platform"]
exclude_keywords = []
minimum_english_ratio = 0.5

[http]
user_agent = "fictional"
timeout_seconds = 10
base_delay_seconds = 1.0
jitter_seconds = 0.0
max_retries = 0

[sources.indeed_brightdata]
enabled = false
search_queries = ["Platform Engineer"]
locations = ["Berlin"]
domain = "de.indeed.com"
""".strip(),
        encoding="utf-8",
    )

    tasks = browser_search_tasks([config_path], datetime(2026, 8, 27, tzinfo=UTC))

    assert [task.url for task in tasks] == [
        "https://de.indeed.com/jobs?q=Platform+Engineer&l=Berlin"
    ]


def test_browser_search_result_requires_complete_visible_cards() -> None:
    result = BrowserSearchResult.from_mapping(_completed_search_payload())

    assert len(result.cards) == 1
    assert result.cards[0].url == "https://de.indeed.com/viewjob?jk=browser-discovery-1"
    malformed = _completed_search_payload()
    malformed["cards"] = [{"url": "https://example.test/jobs/1"}]
    with pytest.raises(BrowserDetailContractError, match="viewjob URL"):
        BrowserSearchResult.from_mapping(malformed)


def test_browser_search_claim_and_expansion_preserve_imported_detail(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    search_queue_path = tmp_path / "browser-search.jsonl"
    detail_queue_path = tmp_path / "browser-detail.jsonl"
    search_queue_path.write_text(json.dumps(_search_task().to_dict()) + "\n", encoding="utf-8")

    assert claim_browser_search_task(search_queue_path) == 0
    assert "task_id" in capsys.readouterr().out
    search_payload = _completed_search_payload()
    search_queue_path.write_text(json.dumps(search_payload) + "\n", encoding="utf-8")
    discovered_task = BrowserDetailTask.from_search_card(
        BrowserSearchResult.from_mapping(search_payload).cards[0]
    ).to_dict()
    discovered_task["status"] = "imported"
    detail_queue_path.write_text(json.dumps(discovered_task) + "\n", encoding="utf-8")

    args = parse_args(
        [
            "--browser-search-results",
            str(search_queue_path),
            "--browser-detail-queue",
            str(detail_queue_path),
        ]
    )
    assert expand_browser_search_results(args) == 0

    expanded_search = json.loads(search_queue_path.read_text(encoding="utf-8"))
    merged_detail = json.loads(detail_queue_path.read_text(encoding="utf-8"))
    assert expanded_search["status"] == "expanded"
    assert merged_detail["status"] == "imported"


def test_browser_search_expansion_invalid_paths_are_cli_errors(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    queue_path = tmp_path / "search.jsonl"

    assert parse_args(["--browser-search-results", str(queue_path)]).browser_detail_queue is None
    assert main(["--browser-search-results", str(queue_path)]) == 2
    assert "requires --browser-detail-queue" in capsys.readouterr().err
    assert (
        main(
            [
                "--browser-search-results",
                str(queue_path),
                "--browser-detail-queue",
                str(queue_path),
            ]
        )
        == 2
    )
    assert "different files" in capsys.readouterr().err


def test_browser_search_detail_preserves_indeed_provenance() -> None:
    search_result = BrowserSearchResult.from_mapping(_completed_search_payload())
    task = BrowserDetailTask.from_search_card(search_result.cards[0])
    payload = task.to_dict()
    payload.update({"status": "complete", "description": "D" * MIN_DESCRIPTION_LENGTH})

    raw = raw_from_browser_detail(
        BrowserDetailResult.from_mapping(payload), datetime(2026, 8, 27, tzinfo=UTC)
    )

    assert raw.source == "indeed"
    assert raw.source_job_id == "browser-discovery-1"
    assert raw.raw_payload["acquisition_mode"] == "authorised_browser_search"


def test_browser_detail_import_keeps_distinct_indeed_jobs_as_separate_rows(
    tmp_path: Path,
) -> None:
    """Two different Indeed postings must land as two rows, not one overwriting the other.

    canonicalize_url() used to strip every query string, including Indeed's
    jk parameter -- the only thing that makes /viewjob URLs distinct -- which
    made the storage layer's canonical_url lookup treat every Indeed job as
    an update to whichever one arrived first.
    """

    detail_queue_path = tmp_path / "browser-detail.jsonl"
    first = _complete_browser_search_detail_task(
        jk="fictional-jk-1",
        title="Qt C++ Developer",
        company_name="Fictional Foo GmbH",
        location_raw="Berlin",
        description="Fictional Qt and C++ desktop application role. " * 8,
    )
    second = _complete_browser_search_detail_task(
        jk="fictional-jk-2",
        title="Senior C++ Systems Engineer",
        company_name="Fictional Bar GmbH",
        location_raw="Munich",
        description="Fictional senior C++ systems engineering role. " * 8,
    )
    detail_queue_path.write_text(
        "\n".join(json.dumps(row) for row in (first, second)) + "\n",
        encoding="utf-8",
    )

    args = _browser_detail_import_args(tmp_path, detail_queue_path)
    assert import_browser_detail_results(args) == 0

    queue = [
        json.loads(line) for line in detail_queue_path.read_text(encoding="utf-8").splitlines()
    ]
    assert [row["status"] for row in queue] == ["imported", "imported"]

    database_path = tmp_path / "jobs.db"
    with sqlite3.connect(database_path) as connection:
        rows = connection.execute(
            "SELECT source_job_id, normalized_title, company_name FROM jobs ORDER BY normalized_title"
        ).fetchall()
    assert rows == [
        ("fictional-jk-1", "Qt C++ Developer", "Fictional Foo GmbH"),
        ("fictional-jk-2", "Senior C++ Systems Engineer", "Fictional Bar GmbH"),
    ]
