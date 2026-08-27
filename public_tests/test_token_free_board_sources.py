"""workable_direct, arbeitnow_direct, berlinstartupjobs_direct.

Coverage for docs/public/specs/2026-08-27-token-free-board-sources.md.
All network access is faked; no test reaches a real board.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit

import pytest

import job_scraper.collectors.base as base
from job_scraper.collectors.arbeitnow import ArbeitnowDirectCollector
from job_scraper.collectors.berlinstartupjobs import (
    PAGE_SIZE,
    BerlinStartupJobsDirectCollector,
)
from job_scraper.collectors.workable import WorkableDirectCollector
from job_scraper.config import HttpConfig, SourceConfig
from job_scraper.domain.models import SearchWindow


class _FakeResponse:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def read(self, size: int | None = None) -> bytes:
        return self._body if size is None else self._body[:size]


def _http_config() -> HttpConfig:
    return HttpConfig(
        user_agent="fictional-agent",
        timeout_seconds=5,
        base_delay_seconds=0,
        jitter_seconds=0,
        max_retries=0,
    )


def _window() -> SearchWindow:
    return SearchWindow(started_at=datetime.now(UTC), overlap_hours=24)


def _rate_limit(url: str) -> HTTPError:
    return HTTPError(url, 429, "Too Many Requests", {}, None)  # type: ignore[arg-type]


# --- workable_direct -------------------------------------------------------
#
# The payload shape mirrors the live search as validated on 2026-08-27: the
# listing carries full text split across three sections, the employer under
# `company.title`, the location as an object, and an opaque `nextPageToken`.


def _workable_posting(**overrides: object) -> dict:
    posting = {
        "id": "job-1",
        "title": "Fictional AI Engineer",
        "company": {"title": "Fictional Labs", "website": "https://example.invalid"},
        "location": {"city": "Berlin", "region": "", "countryName": "Germany"},
        "workplace": "on_site",
        "employmentType": "Full-time",
        "created": "2026-08-01T00:00:00Z",
        "language": "en",
        "description": "<p>Build things.</p>",
        "requirementsSection": "<ul><li>Five years</li></ul>",
        "benefitsSection": "<p>Coffee</p>",
        "url": "https://boards.invalid/view/job-1",
    }
    posting.update(overrides)
    return posting


def _workable_config(**kwargs: object) -> SourceConfig:
    return SourceConfig(
        enabled=True,
        max_listing_pages=int(kwargs.pop("max_listing_pages", 1)),
        search_queries=list(kwargs.pop("search_queries", ["AI"])),
        locations=list(kwargs.pop("locations", ["Germany"])),
    )


def test_workable_maps_a_posting_and_joins_all_three_text_sections(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        base,
        "urlopen",
        lambda request, timeout=None: _FakeResponse(
            json.dumps({"jobs": [_workable_posting()], "nextPageToken": ""}).encode()
        ),
    )

    records = list(WorkableDirectCollector(_http_config(), _workable_config()).collect(_window()))

    assert len(records) == 1
    record = records[0]
    assert record.company_name == "Fictional Labs"
    assert record.location_raw == "Berlin, Germany"
    # `on_site` is the vendor's spelling; the pipeline's is `onsite`.
    assert record.remote_type == "onsite"
    # Losing the requirements section would blind the requirement rules.
    assert "Build things." in record.job_description
    assert "Five years" in record.job_description
    assert "Coffee" in record.job_description
    assert record.raw_payload["listing_language"] == "en"


def test_workable_sends_the_configured_query_and_location(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[dict[str, list[str]]] = []

    def fake_urlopen(request, timeout=None):
        seen.append(parse_qs(urlsplit(request.full_url).query))
        return _FakeResponse(json.dumps({"jobs": [], "nextPageToken": ""}).encode())

    monkeypatch.setattr(base, "urlopen", fake_urlopen)

    list(
        WorkableDirectCollector(
            _http_config(), _workable_config(search_queries=["Applied AI"], locations=["Germany"])
        ).collect(_window())
    )

    assert seen == [{"query": ["Applied AI"], "location": ["Germany"]}]


def test_workable_follows_the_page_cursor_until_it_is_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen_tokens: list[str] = []

    def fake_urlopen(request, timeout=None):
        params = parse_qs(urlsplit(request.full_url).query)
        token = params.get("pageToken", [""])[0]
        seen_tokens.append(token)
        if not token:
            return _FakeResponse(
                json.dumps(
                    {"jobs": [_workable_posting(id="job-1")], "nextPageToken": "cursor-2"}
                ).encode()
            )
        return _FakeResponse(
            json.dumps({"jobs": [_workable_posting(id="job-2")], "nextPageToken": ""}).encode()
        )

    monkeypatch.setattr(base, "urlopen", fake_urlopen)
    monkeypatch.setattr(base.time, "sleep", lambda _seconds: None)

    records = list(
        WorkableDirectCollector(_http_config(), _workable_config(max_listing_pages=5)).collect(
            _window()
        )
    )

    assert seen_tokens == ["", "cursor-2"]
    assert [record.source_job_id for record in records] == ["job-1", "job-2"]


def test_workable_rate_limit_keeps_the_postings_already_collected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    def fake_urlopen(request, timeout=None):
        if "pageToken" in request.full_url:
            raise _rate_limit(request.full_url)
        return _FakeResponse(
            json.dumps(
                {"jobs": [_workable_posting(id="job-1")], "nextPageToken": "cursor-2"}
            ).encode()
        )

    monkeypatch.setattr(base, "urlopen", fake_urlopen)
    monkeypatch.setattr(base.time, "sleep", lambda _seconds: None)

    records = list(
        WorkableDirectCollector(
            _http_config(), _workable_config(max_listing_pages=5), event_logger=events.append
        ).collect(_window())
    )

    assert [record.source_job_id for record in records] == ["job-1"]
    assert any("rate limited" in event for event in events)


def test_workable_one_failing_query_leaves_the_other_intact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    def fake_urlopen(request, timeout=None):
        if "query=Broken" in request.full_url:
            raise URLError("boom")
        return _FakeResponse(
            json.dumps({"jobs": [_workable_posting()], "nextPageToken": ""}).encode()
        )

    monkeypatch.setattr(base, "urlopen", fake_urlopen)

    records = list(
        WorkableDirectCollector(
            _http_config(),
            _workable_config(search_queries=["Broken", "Working"]),
            event_logger=events.append,
        ).collect(_window())
    )

    assert len(records) == 1
    assert any("Broken" in event for event in events)


def test_workable_drops_a_posting_with_no_employer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        base,
        "urlopen",
        lambda request, timeout=None: _FakeResponse(
            json.dumps(
                {"jobs": [_workable_posting(company={"title": ""})], "nextPageToken": ""}
            ).encode()
        ),
    )

    records = list(WorkableDirectCollector(_http_config(), _workable_config()).collect(_window()))

    assert records == []


def test_workable_keeps_a_posting_the_vendor_marks_as_another_language(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Language is the pipeline's decision, not the adapter's."""
    monkeypatch.setattr(
        base,
        "urlopen",
        lambda request, timeout=None: _FakeResponse(
            json.dumps({"jobs": [_workable_posting(language="de")], "nextPageToken": ""}).encode()
        ),
    )

    records = list(WorkableDirectCollector(_http_config(), _workable_config()).collect(_window()))

    assert len(records) == 1
    assert records[0].raw_payload["listing_language"] == "de"


def test_workable_makes_no_request_with_an_empty_query_matrix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_if_called(*_a, **_k):
        raise AssertionError("no request should be made with an empty query matrix")

    monkeypatch.setattr(base, "urlopen", fail_if_called)

    collector = WorkableDirectCollector(
        _http_config(), _workable_config(search_queries=[], locations=[])
    )

    assert list(collector.collect(_window())) == []


# --- arbeitnow_direct ------------------------------------------------------


def _arbeitnow_posting(**overrides: object) -> dict:
    posting = {
        "slug": "fictional-ai-engineer-1",
        "company_name": "Fictional Labs",
        "title": "Fictional AI Engineer",
        # The feed HTML-escapes its HTML; a reader that does not unescape
        # first strips nothing and stores the markup as text.
        "description": "&lt;p&gt;Build things.&lt;/p&gt;",
        "remote": True,
        "url": "https://board.invalid/jobs/fictional-ai-engineer-1",
        "tags": ["Python"],
        "job_types": ["full_time"],
        "location": "Berlin; Munich",
        "created_at": 1787853327,
    }
    posting.update(overrides)
    return posting


def _feed_config(max_listing_pages: int = 1) -> SourceConfig:
    return SourceConfig(enabled=True, max_listing_pages=max_listing_pages)


def test_arbeitnow_maps_a_posting_and_converts_its_timestamp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        base,
        "urlopen",
        lambda request, timeout=None: _FakeResponse(
            json.dumps({"data": [_arbeitnow_posting()]}).encode()
        ),
    )

    records = list(ArbeitnowDirectCollector(_http_config(), _feed_config()).collect(_window()))

    assert len(records) == 1
    record = records[0]
    assert record.company_name == "Fictional Labs"
    assert record.job_description == "Build things."
    assert record.remote_type == "remote"
    # Only the first of several places is kept, as with every other source.
    assert record.location_raw == "Berlin"
    # A ten-digit integer is not a date the pipeline can read.
    assert record.posted_at_text.startswith("2026-")


def test_arbeitnow_stops_at_an_empty_page(monkeypatch: pytest.MonkeyPatch) -> None:
    seen_pages: list[str] = []

    def fake_urlopen(request, timeout=None):
        page = parse_qs(urlsplit(request.full_url).query)["page"][0]
        seen_pages.append(page)
        if page == "1":
            return _FakeResponse(json.dumps({"data": [_arbeitnow_posting()]}).encode())
        return _FakeResponse(json.dumps({"data": []}).encode())

    monkeypatch.setattr(base, "urlopen", fake_urlopen)
    monkeypatch.setattr(base.time, "sleep", lambda _seconds: None)

    records = list(ArbeitnowDirectCollector(_http_config(), _feed_config(5)).collect(_window()))

    assert seen_pages == ["1", "2"]
    assert len(records) == 1


def test_arbeitnow_rate_limit_ends_paging_and_keeps_earlier_pages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    def fake_urlopen(request, timeout=None):
        if "page=2" in request.full_url:
            raise _rate_limit(request.full_url)
        return _FakeResponse(json.dumps({"data": [_arbeitnow_posting()]}).encode())

    monkeypatch.setattr(base, "urlopen", fake_urlopen)
    monkeypatch.setattr(base.time, "sleep", lambda _seconds: None)

    records = list(
        ArbeitnowDirectCollector(
            _http_config(), _feed_config(5), event_logger=events.append
        ).collect(_window())
    )

    assert len(records) == 1
    assert any("rate limited" in event for event in events)


def test_arbeitnow_drops_a_posting_with_no_employer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        base,
        "urlopen",
        lambda request, timeout=None: _FakeResponse(
            json.dumps({"data": [_arbeitnow_posting(company_name="")]}).encode()
        ),
    )

    records = list(ArbeitnowDirectCollector(_http_config(), _feed_config()).collect(_window()))

    assert records == []


# --- berlinstartupjobs_direct ----------------------------------------------


def _bsj_posting(**overrides: object) -> dict:
    posting = {
        "id": 1,
        "date_gmt": "2026-08-27T09:30:16",
        "link": "https://board.invalid/engineering/fictional-ai-engineer/",
        "title": {"rendered": "Fictional AI Engineer // Fictional Labs"},
        "content": {"rendered": "<p>Build things.</p>"},
        "class_list": ["tag-ai"],
    }
    posting.update(overrides)
    return posting


def test_berlinstartupjobs_splits_the_employer_out_of_the_title(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        base,
        "urlopen",
        lambda request, timeout=None: _FakeResponse(json.dumps([_bsj_posting()]).encode()),
    )

    records = list(
        BerlinStartupJobsDirectCollector(_http_config(), _feed_config()).collect(_window())
    )

    assert len(records) == 1
    record = records[0]
    assert record.title == "Fictional AI Engineer"
    assert record.company_name == "Fictional Labs"
    assert record.job_description == "Build things."
    assert record.location_raw == "Berlin, Germany"


def test_berlinstartupjobs_splits_on_the_first_separator_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An employer name may itself contain the separator."""
    monkeypatch.setattr(
        base,
        "urlopen",
        lambda request, timeout=None: _FakeResponse(
            json.dumps(
                [_bsj_posting(title={"rendered": "Engineer // Labs // https://x.invalid"})]
            ).encode()
        ),
    )

    records = list(
        BerlinStartupJobsDirectCollector(_http_config(), _feed_config()).collect(_window())
    )

    assert records[0].title == "Engineer"
    assert records[0].company_name == "Labs // https://x.invalid"


def test_berlinstartupjobs_drops_a_title_without_the_separator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    monkeypatch.setattr(
        base,
        "urlopen",
        lambda request, timeout=None: _FakeResponse(
            json.dumps([_bsj_posting(title={"rendered": "Just A Role"})]).encode()
        ),
    )

    records = list(
        BerlinStartupJobsDirectCollector(
            _http_config(), _feed_config(), event_logger=events.append
        ).collect(_window())
    )

    assert records == []
    assert any("no recoverable company name" in event for event in events)


def test_berlinstartupjobs_treats_a_client_error_past_the_end_as_the_end(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Paging past the last page answers 400 rather than an empty list."""
    events: list[str] = []

    def fake_urlopen(request, timeout=None):
        if "page=2" in request.full_url:
            raise HTTPError(request.full_url, 400, "Bad Request", {}, None)  # type: ignore[arg-type]
        return _FakeResponse(json.dumps([_bsj_posting(id=n) for n in range(PAGE_SIZE)]).encode())

    monkeypatch.setattr(base, "urlopen", fake_urlopen)
    monkeypatch.setattr(base.time, "sleep", lambda _seconds: None)

    records = list(
        BerlinStartupJobsDirectCollector(
            _http_config(), _feed_config(5), event_logger=events.append
        ).collect(_window())
    )

    assert len(records) == PAGE_SIZE
    assert events == []


def test_berlinstartupjobs_stops_on_a_short_page(monkeypatch: pytest.MonkeyPatch) -> None:
    seen_pages: list[str] = []

    def fake_urlopen(request, timeout=None):
        seen_pages.append(parse_qs(urlsplit(request.full_url).query)["page"][0])
        return _FakeResponse(json.dumps([_bsj_posting()]).encode())

    monkeypatch.setattr(base, "urlopen", fake_urlopen)

    list(BerlinStartupJobsDirectCollector(_http_config(), _feed_config(5)).collect(_window()))

    assert seen_pages == ["1"]


# --- registry ---------------------------------------------------------------


def test_all_three_sources_build_through_the_production_registry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An independent user path: one run per source, through real construction.

    Per-source isolation is the criterion most likely to pass in a unit test
    and fail in a run, so each source is built the way the composition root
    builds it rather than instantiated directly.
    """
    from job_scraper.registry.builtins import (
        SourceBuildRequest,
        build_source,
        create_builtin_registry,
    )

    registry = create_builtin_registry()
    bodies = {
        "workable_direct": json.dumps(
            {"jobs": [_workable_posting()], "nextPageToken": ""}
        ).encode(),
        "arbeitnow_direct": json.dumps({"data": [_arbeitnow_posting()]}).encode(),
        "berlinstartupjobs_direct": json.dumps([_bsj_posting()]).encode(),
    }

    for source_id, body in bodies.items():
        monkeypatch.setattr(
            base, "urlopen", lambda request, timeout=None, _body=body: _FakeResponse(_body)
        )
        source = build_source(
            registry,
            source_id,
            SourceBuildRequest(http=_http_config(), settings=_workable_config()),
        )
        records = list(source.collect(_window()))
        assert len(records) == 1, source_id
        assert records[0].company_name == "Fictional Labs", source_id


# --- failure paths shared by all three --------------------------------------
#
# These are the isolation guarantees the spec makes, and each one is a branch
# a happy-path fixture never reaches.


def _raise(exc: Exception):
    def fake_urlopen(request, timeout=None):
        raise exc

    return fake_urlopen


def test_workable_non_rate_limit_http_error_fails_that_query_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    def fake_urlopen(request, timeout=None):
        if "query=Broken" in request.full_url:
            raise HTTPError(request.full_url, 500, "Server Error", {}, None)  # type: ignore[arg-type]
        return _FakeResponse(
            json.dumps({"jobs": [_workable_posting()], "nextPageToken": ""}).encode()
        )

    monkeypatch.setattr(base, "urlopen", fake_urlopen)

    records = list(
        WorkableDirectCollector(
            _http_config(),
            _workable_config(search_queries=["Broken", "Working"]),
            event_logger=events.append,
        ).collect(_window())
    )

    assert len(records) == 1
    assert any("500" in event for event in events)


def test_workable_rejects_a_payload_that_is_not_an_object(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    monkeypatch.setattr(
        base,
        "urlopen",
        lambda request, timeout=None: _FakeResponse(json.dumps([1, 2, 3]).encode()),
    )

    records = list(
        WorkableDirectCollector(
            _http_config(), _workable_config(), event_logger=events.append
        ).collect(_window())
    )

    assert records == []
    assert any("not an object" in event for event in events)


def test_workable_does_not_emit_the_same_posting_twice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The same posting can match two queries; it is emitted once."""
    monkeypatch.setattr(
        base,
        "urlopen",
        lambda request, timeout=None: _FakeResponse(
            json.dumps({"jobs": [_workable_posting()], "nextPageToken": ""}).encode()
        ),
    )

    records = list(
        WorkableDirectCollector(
            _http_config(), _workable_config(search_queries=["AI", "Applied AI"])
        ).collect(_window())
    )

    assert len(records) == 1


def test_workable_tolerates_a_location_that_is_not_an_object(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        base,
        "urlopen",
        lambda request, timeout=None: _FakeResponse(
            json.dumps({"jobs": [_workable_posting(location=None)], "nextPageToken": ""}).encode()
        ),
    )

    records = list(WorkableDirectCollector(_http_config(), _workable_config()).collect(_window()))

    assert records[0].location_raw == ""


def test_arbeitnow_non_rate_limit_http_error_ends_paging_with_an_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    def fake_urlopen(request, timeout=None):
        if "page=2" in request.full_url:
            raise HTTPError(request.full_url, 500, "Server Error", {}, None)  # type: ignore[arg-type]
        return _FakeResponse(json.dumps({"data": [_arbeitnow_posting()]}).encode())

    monkeypatch.setattr(base, "urlopen", fake_urlopen)
    monkeypatch.setattr(base.time, "sleep", lambda _seconds: None)

    records = list(
        ArbeitnowDirectCollector(
            _http_config(), _feed_config(5), event_logger=events.append
        ).collect(_window())
    )

    assert len(records) == 1
    assert any("page 2 failed" in event for event in events)


def test_arbeitnow_transport_failure_ends_paging_with_an_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    monkeypatch.setattr(base, "urlopen", _raise(URLError("boom")))

    records = list(
        ArbeitnowDirectCollector(
            _http_config(), _feed_config(2), event_logger=events.append
        ).collect(_window())
    )

    assert records == []
    assert any("page 1 failed" in event for event in events)


def test_arbeitnow_keeps_a_posting_with_no_location(monkeypatch: pytest.MonkeyPatch) -> None:
    """Placing a posting is the pipeline's decision, so an empty location stays empty."""
    monkeypatch.setattr(
        base,
        "urlopen",
        lambda request, timeout=None: _FakeResponse(
            json.dumps(
                {"data": [_arbeitnow_posting(location="", created_at="not-a-number")]}
            ).encode()
        ),
    )

    records = list(ArbeitnowDirectCollector(_http_config(), _feed_config()).collect(_window()))

    assert len(records) == 1
    assert records[0].location_raw == ""
    # A non-numeric timestamp is passed through for the pipeline to reject,
    # not coerced into a date that was never sent.
    assert records[0].posted_at_text == "not-a-number"


def test_arbeitnow_keeps_only_the_first_of_a_semicolon_location_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        base,
        "urlopen",
        lambda request, timeout=None: _FakeResponse(
            json.dumps(
                {
                    "data": [
                        _arbeitnow_posting(slug="a", location="Berlin; Munich"),
                        # A comma list still carries its country words, which the
                        # country check reads, so it is passed through whole.
                        _arbeitnow_posting(slug="b", location="Paris (France), Amsterdam"),
                    ]
                }
            ).encode()
        ),
    )

    records = list(ArbeitnowDirectCollector(_http_config(), _feed_config()).collect(_window()))

    assert [record.location_raw for record in records] == [
        "Berlin",
        "Paris (France), Amsterdam",
    ]


def test_berlinstartupjobs_reports_a_server_error_rather_than_swallowing_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    monkeypatch.setattr(
        base,
        "urlopen",
        _raise(HTTPError("https://board.invalid", 500, "Server Error", {}, None)),  # type: ignore[arg-type]
    )

    records = list(
        BerlinStartupJobsDirectCollector(
            _http_config(), _feed_config(), event_logger=events.append
        ).collect(_window())
    )

    assert records == []
    assert any("page 1 failed" in event for event in events)


def test_berlinstartupjobs_rejects_a_payload_that_is_not_a_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    monkeypatch.setattr(
        base,
        "urlopen",
        lambda request, timeout=None: _FakeResponse(json.dumps({"posts": []}).encode()),
    )

    records = list(
        BerlinStartupJobsDirectCollector(
            _http_config(), _feed_config(), event_logger=events.append
        ).collect(_window())
    )

    assert records == []
    assert any("not a list" in event for event in events)


def test_berlinstartupjobs_keeps_a_posting_whose_id_is_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A falsy-but-valid id must not be read as "no id" and dropped."""
    monkeypatch.setattr(
        base,
        "urlopen",
        lambda request, timeout=None: _FakeResponse(json.dumps([_bsj_posting(id=0)]).encode()),
    )

    records = list(
        BerlinStartupJobsDirectCollector(_http_config(), _feed_config()).collect(_window())
    )

    assert [record.source_job_id for record in records] == ["0"]
