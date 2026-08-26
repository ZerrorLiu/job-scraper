from __future__ import annotations

from datetime import UTC, datetime

from job_scraper.adapters.sinks.notion_daily import NotionDailySink
from job_scraper.domain.models import JobHistorySnapshot, JobRecord
from job_scraper.ports.sinks import PublishContext


def _job(title: str, source_job_id: str, source_url: str = "") -> JobRecord:
    observed_at = datetime(2026, 1, 1, tzinfo=UTC)
    return JobRecord(
        source="fictional",
        source_job_id=source_job_id,
        source_url=source_url or f"https://example.test/{source_job_id}",
        canonical_url=source_url or f"https://example.test/{source_job_id}",
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


class _AcceptedJob:
    def __init__(self, job: JobRecord, job_id: str) -> None:
        self.job = job
        self.job_id = job_id
        self.linked_job_ids: list[str] = []


class _Repository:
    def get_job_history(
        self, job_id: str, company_name: str, started_at: datetime
    ) -> JobHistorySnapshot:
        del job_id, company_name, started_at
        return JobHistorySnapshot(status_label="new")

    def upsert_notion_state(
        self, job_id: str, page_id: str, data_source_id: str, payload_hash: str, status: str
    ) -> None:
        del job_id, page_id, data_source_id, payload_hash, status


class _FakeNotionClient:
    def __init__(self, *, fail_titles: frozenset[str] = frozenset()) -> None:
        self._fail_titles = fail_titles
        self.created_pages: list[dict] = []
        self._next_id = 0

    def enabled(self) -> bool:
        return True

    def ensure_daily_database(
        self,
        title: str,
        *,
        legacy_titles: list[str],
        bound_database_id: str = "",
    ) -> dict:
        del title, legacy_titles, bound_database_id
        return {"id": "database-1", "data_sources": [{"id": "data-source-1"}]}

    def ensure_job_views(self, database_id: str, data_source_id: str, local_date: object) -> None:
        del database_id, data_source_id, local_date

    def get_data_source_property_types(self, data_source_id: str) -> dict[str, str]:
        del data_source_id
        return {}

    def list_data_source_pages(self, data_source_id: str) -> list[dict]:
        del data_source_id
        return []

    def _build_page(self, page_id: str, properties: dict) -> dict:
        title = properties["Job"]["title"][0]["text"]["content"]
        url = properties["Job"]["title"][0]["text"].get("link", {}).get("url", "")
        company = properties["Company"]["rich_text"][0]["text"]["content"]
        return {
            "id": page_id,
            "properties": {
                "Job": {
                    "title": [
                        {
                            "plain_text": title,
                            "text": {"content": title, "link": {"url": url} if url else None},
                            "href": url or None,
                        }
                    ]
                },
                "Company": {"rich_text": [{"plain_text": company}]},
            },
        }

    def create_data_source_page(
        self, data_source_id: str, properties: dict, children: list[dict]
    ) -> dict:
        del data_source_id, children
        title = properties["Job"]["title"][0]["text"]["content"]
        if title in self._fail_titles:
            raise RuntimeError(f"Notion API rejected {title}")
        self._next_id += 1
        page = self._build_page(f"page-{self._next_id}", properties)
        self.created_pages.append(page)
        return page

    def create_or_update_page(self, page_id: str, properties: dict) -> dict:
        return self._build_page(page_id, properties)

    def list_child_blocks(self, page_id: str) -> list[dict]:
        del page_id
        return []

    def append_child_blocks(self, page_id: str, blocks: list[dict]) -> None:
        del page_id, blocks

    def update_block(self, block_id: str, payload: dict) -> None:
        del block_id, payload

    def payload_hash(self, properties: dict) -> str:
        del properties
        return "fictional-hash"


def test_one_failing_job_does_not_abort_the_rest_of_the_batch() -> None:
    client = _FakeNotionClient(fail_titles=frozenset({"Fictional Engineer 2"}))
    sink = NotionDailySink(
        _Repository(),  # type: ignore[arg-type]
        client,  # type: ignore[arg-type]
        timezone_name="UTC",
        table_prefix="Fictional",
        track_label="fictional",
        started_at=datetime(2026, 1, 1, tzinfo=UTC),
        sleeper=lambda _seconds: None,
    )
    jobs = [
        _AcceptedJob(_job("Fictional Engineer 1", "job-1"), "job-1"),
        _AcceptedJob(_job("Fictional Engineer 2", "job-2"), "job-2"),
        _AcceptedJob(_job("Fictional Engineer 3", "job-3"), "job-3"),
    ]

    result = sink.publish(jobs, PublishContext(run_id="fictional-run", profile_id="fictional"))

    assert result.published == 2
    assert len(result.errors) == 1
    assert "Fictional Engineer 2" in result.errors[0]
    assert [
        page["properties"]["Job"]["title"][0]["plain_text"] for page in client.created_pages
    ] == [
        "Fictional Engineer 1",
        "Fictional Engineer 3",
    ]


def test_two_jobs_that_should_merge_do_not_create_duplicate_pages_in_one_batch() -> None:
    client = _FakeNotionClient()
    sink = NotionDailySink(
        _Repository(),  # type: ignore[arg-type]
        client,  # type: ignore[arg-type]
        timezone_name="UTC",
        table_prefix="Fictional",
        track_label="fictional",
        started_at=datetime(2026, 1, 1, tzinfo=UTC),
        sleeper=lambda _seconds: None,
    )
    same_url = "https://example.test/shared-posting"
    jobs = [
        _AcceptedJob(_job("Fictional Engineer", "job-1", source_url=same_url), "job-1"),
        _AcceptedJob(_job("Fictional Engineer", "job-2", source_url=same_url), "job-2"),
    ]

    result = sink.publish(jobs, PublishContext(run_id="fictional-run", profile_id="fictional"))

    assert not result.errors
    assert len(client.created_pages) == 1
    assert result.published == 2
