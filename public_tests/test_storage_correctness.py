from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError

import job_scraper.integrations.notion as notion_module
from job_scraper.adapters.sinks.csv import CsvSink
from job_scraper.domain.models import JobRecord
from job_scraper.domain.policies import FilterPolicy
from job_scraper.integrations.notion import NotionClient
from job_scraper.ports.sinks import PublishContext
from job_scraper.storage.db import Database


def _job(source_job_id: str, title: str = "Fictional Engineer") -> JobRecord:
    observed_at = datetime(2026, 1, 1, tzinfo=UTC)
    return JobRecord(
        source="fictional",
        source_job_id=source_job_id,
        source_url=f"https://example.test/{source_job_id}",
        canonical_url=f"https://example.test/{source_job_id}",
        title=title,
        company_name="Example GmbH",
        location_raw="Berlin",
        country="DE",
        city="Berlin",
        region="",
        remote_type="onsite",
        employment_type="full-time",
        seniority="unknown",
        posted_at=observed_at,
        first_seen_at=observed_at,
        scraped_at=observed_at,
        job_description="A fictional job description.",
        description_language="English",
        english_ratio=1.0,
        keyword_hits=[],
        tech_stack=[],
        salary_text="",
        salary_min=None,
        salary_max=None,
        salary_currency=None,
        dedupe_key=source_job_id,
    )


def test_database_connect_enables_foreign_key_enforcement(tmp_path: Path) -> None:
    database = Database(tmp_path / "workspace.db")
    database.initialize()

    with database.connect() as connection:
        value = connection.execute("PRAGMA foreign_keys").fetchone()[0]

    assert value == 1


def test_match_job_id_returns_empty_for_ambiguous_title_and_company(tmp_path: Path) -> None:
    database = Database(tmp_path / "workspace.db")
    database.initialize()
    database.upsert_job(_job("posting-1"), "run-1")
    database.upsert_job(_job("posting-2"), "run-1")  # same title+company, distinct posting

    assert database.match_job_id_for_notion_page("Fictional Engineer", "Example GmbH") == ""


def test_match_job_id_resolves_unique_title_and_company(tmp_path: Path) -> None:
    database = Database(tmp_path / "workspace.db")
    database.initialize()
    job_id, _is_new = database.upsert_job(_job("posting-1"), "run-1")

    assert database.match_job_id_for_notion_page("Fictional Engineer", "Example GmbH") == job_id


def test_csv_sink_write_is_atomic_and_leaves_prior_file_intact_on_failure(
    tmp_path: Path, monkeypatch
) -> None:
    destination = tmp_path / "cumulative.csv"
    destination.write_text("existing,content\n1,2\n", encoding="utf-8")

    database = Database(tmp_path / "workspace.db")
    database.initialize()
    database.upsert_job(_job("posting-1"), "run-1")

    import job_scraper.adapters.sinks.csv as csv_sink_module

    class ExplodingWriter:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def writeheader(self) -> None:
            raise RuntimeError("disk full")

    monkeypatch.setattr(csv_sink_module.csv, "DictWriter", ExplodingWriter)

    sink = CsvSink(database, destination, FilterPolicy(countries=("DE",)))

    try:
        sink.publish([], PublishContext(run_id="fictional-run", profile_id="fictional"))
    except RuntimeError:
        pass
    else:
        raise AssertionError("expected the simulated write failure to propagate")

    assert destination.read_text(encoding="utf-8") == "existing,content\n1,2\n"
    assert list(tmp_path.glob("*.tmp")) == []


def test_notion_client_honors_retry_after_header(monkeypatch) -> None:
    from job_scraper.config import NotionConfig

    delays: list[float] = []
    attempts = 0

    def flaky_urlopen(*_args: object, **_kwargs: object):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            error = HTTPError(
                "https://api.notion.com/v1/pages",
                429,
                "Too Many Requests",
                {"Retry-After": "7"},  # type: ignore[arg-type]
                None,
            )
            raise error

        class _Response:
            def __enter__(self) -> _Response:
                return self

            def __exit__(self, *_exc: object) -> None:
                return None

            def read(self) -> bytes:
                return b'{"ok": true}'

        return _Response()

    monkeypatch.setattr(notion_module, "urlopen", flaky_urlopen)
    monkeypatch.setattr(notion_module.time, "sleep", lambda seconds: delays.append(seconds))

    client = NotionClient(
        NotionConfig(
            enabled=True,
            token="fictional-token",
            database_id="",
            parent_page_id="fictional-page",
        )
    )
    result = client.request("https://api.notion.com/v1/pages", "POST", {})

    assert result == {"ok": True}
    assert delays == [7.0]
