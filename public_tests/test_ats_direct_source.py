"""ats_direct: direct reads of employer applicant-tracking boards.

Coverage for docs/public/specs/2026-08-27-employer-direct-source-coverage.md.
All network access is faked; no test reaches a real board.
"""

from __future__ import annotations

from urllib.error import HTTPError, URLError

import pytest

import job_scraper.collectors.base as base
from job_scraper.collectors.ats import AtsDirectCollector
from job_scraper.config import HttpConfig, SourceConfig
from job_scraper.domain.models import SearchWindow

PERSONIO_XML = """<?xml version="1.0" encoding="UTF-8"?>
<workzag-jobs>
  <position>
    <id>4711</id>
    <name>Fictional Backend Engineer</name>
    <office>Berlin</office>
    <employmentType>Full-time</employmentType>
    <createdAt>2026-08-01T00:00:00Z</createdAt>
    <jobDescriptions>
      <jobDescription>
        <name>Your mission</name>
        <value><![CDATA[<p>Build fictional things.</p>]]></value>
      </jobDescription>
      <jobDescription>
        <name>Your profile</name>
        <value><![CDATA[<p>Fictional experience required.</p>]]></value>
      </jobDescription>
    </jobDescriptions>
  </position>
  <position>
    <id>4712</id>
    <name></name>
    <office>Munich</office>
  </position>
</workzag-jobs>
"""

JSONLD_HTML = """
<html><head>
<script type="application/ld+json">
{
  "@type": "JobPosting",
  "title": "Fictional Platform Engineer",
  "description": "<p>A complete fictional description.</p>",
  "datePosted": "2026-08-02",
  "hiringOrganization": {"name": "Fictional Careers Page GmbH"}
}
</script>
</head><body></body></html>
"""


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


def _source_config(boards: list[dict]) -> SourceConfig:
    return SourceConfig(enabled=True, options={"boards": boards})


def test_an_unknown_provider_id_is_a_build_time_configuration_error() -> None:
    with pytest.raises(ValueError, match="unknown provider"):
        AtsDirectCollector(
            _http_config(),
            _source_config([{"provider": "not_a_real_provider", "token": "example"}]),
        )


def test_a_board_missing_a_token_is_a_build_time_configuration_error() -> None:
    with pytest.raises(ValueError, match="token"):
        AtsDirectCollector(
            _http_config(),
            _source_config([{"provider": "personio"}]),
        )


def test_boards_must_be_a_list() -> None:
    with pytest.raises(ValueError, match="must be a list"):
        AtsDirectCollector(
            _http_config(),
            SourceConfig(enabled=True, options={"boards": "not-a-list"}),
        )


def test_each_board_entry_must_be_a_table() -> None:
    with pytest.raises(ValueError, match="must be a table"):
        AtsDirectCollector(
            _http_config(),
            SourceConfig(enabled=True, options={"boards": ["not-a-table"]}),
        )


def test_a_failed_board_is_reported_through_the_event_logger(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    monkeypatch.setattr(
        base, "urlopen", lambda *_a, **_k: (_ for _ in ()).throw(URLError("connection reset"))
    )

    collector = AtsDirectCollector(
        _http_config(),
        _source_config([{"provider": "personio", "token": "bad-board"}]),
        event_logger=events.append,
    )
    list(collector.collect(_window()))

    assert any("bad-board" in message for message in events)


def test_jsonld_board_with_no_embedded_jobposting_is_isolated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(base, "urlopen", lambda *_a, **_k: _FakeResponse(b"<html></html>"))

    collector = AtsDirectCollector(
        _http_config(),
        _source_config([{"provider": "jsonld", "token": "https://careers.example.test/none"}]),
    )

    assert list(collector.collect(_window())) == []


def test_jsonld_board_with_no_title_is_isolated(monkeypatch: pytest.MonkeyPatch) -> None:
    html = """
    <script type="application/ld+json">
    {"@type": "JobPosting", "description": "Fictional description with no title."}
    </script>
    """
    monkeypatch.setattr(base, "urlopen", lambda *_a, **_k: _FakeResponse(html.encode()))

    collector = AtsDirectCollector(
        _http_config(),
        _source_config([{"provider": "jsonld", "token": "https://careers.example.test/no-title"}]),
    )

    assert list(collector.collect(_window())) == []


def test_jsonld_board_http_error_is_isolated(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(request, timeout=None):
        raise HTTPError(request.full_url, 500, "Server Error", None, None)

    monkeypatch.setattr(base, "urlopen", fake_urlopen)

    collector = AtsDirectCollector(
        _http_config(),
        _source_config([{"provider": "jsonld", "token": "https://careers.example.test/down"}]),
    )

    assert list(collector.collect(_window())) == []


def test_personio_board_yields_a_record_with_joined_titled_description_sections(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(base, "urlopen", lambda *_a, **_k: _FakeResponse(PERSONIO_XML.encode()))

    collector = AtsDirectCollector(
        _http_config(),
        _source_config(
            [{"provider": "personio", "token": "examplecorp", "company_name": "Example Corp"}]
        ),
    )
    records = list(collector.collect(_window()))

    assert len(records) == 1  # the second <position> has no name and is skipped
    record = records[0]
    assert record.source == "ats_direct"
    assert record.source_job_id == "examplecorp-4711"
    assert record.company_name == "Example Corp"
    assert record.location_raw == "Berlin"
    assert record.source_url == "https://examplecorp.jobs.personio.de/job/4711"
    assert "Your mission" in record.job_description
    assert "Build fictional things." in record.job_description
    assert "Your profile" in record.job_description


def test_personio_board_falls_back_to_a_title_cased_token_without_a_company_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(base, "urlopen", lambda *_a, **_k: _FakeResponse(PERSONIO_XML.encode()))

    collector = AtsDirectCollector(
        _http_config(),
        _source_config([{"provider": "personio", "token": "example-corp"}]),
    )
    records = list(collector.collect(_window()))

    assert records[0].company_name == "Example Corp"


def test_jsonld_board_reuses_the_shared_jobposting_extractor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(base, "urlopen", lambda *_a, **_k: _FakeResponse(JSONLD_HTML.encode()))

    collector = AtsDirectCollector(
        _http_config(),
        _source_config([{"provider": "jsonld", "token": "https://careers.example.test/backend"}]),
    )
    records = list(collector.collect(_window()))

    assert len(records) == 1
    record = records[0]
    assert record.title == "Fictional Platform Engineer"
    assert record.company_name == "Fictional Careers Page GmbH"
    assert record.job_description == "A complete fictional description."


def test_one_bad_token_does_not_stop_the_run(monkeypatch: pytest.MonkeyPatch) -> None:
    responses = {
        "https://good.jobs.personio.de/xml": _FakeResponse(PERSONIO_XML.encode()),
    }

    def fake_urlopen(request, timeout=None):
        url = request.full_url
        if url not in responses:
            raise URLError("connection reset")
        return responses[url]

    monkeypatch.setattr(base, "urlopen", fake_urlopen)

    collector = AtsDirectCollector(
        _http_config(),
        _source_config(
            [
                {"provider": "personio", "token": "bad-token"},
                {"provider": "personio", "token": "good"},
            ]
        ),
    )
    records = list(collector.collect(_window()))

    assert len(records) == 1
    assert records[0].source_job_id == "good-4711"


def test_a_404_from_a_board_is_isolated_to_that_token(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(request, timeout=None):
        raise HTTPError(request.full_url, 404, "Not Found", None, None)

    monkeypatch.setattr(base, "urlopen", fake_urlopen)

    collector = AtsDirectCollector(
        _http_config(),
        _source_config([{"provider": "personio", "token": "retired-board"}]),
    )

    assert list(collector.collect(_window())) == []


def test_malformed_xml_is_isolated_to_that_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(base, "urlopen", lambda *_a, **_k: _FakeResponse(b"not xml at all <<<"))

    collector = AtsDirectCollector(
        _http_config(),
        _source_config([{"provider": "personio", "token": "broken-feed"}]),
    )

    assert list(collector.collect(_window())) == []


def _window() -> SearchWindow:
    from datetime import UTC, datetime

    return SearchWindow(started_at=datetime.now(UTC), overlap_hours=24)


def test_independent_user_path_simulation_through_the_production_registry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Builds the source the way `job-scraper run` does, through the real registry.

    Three boards, three different failure modes (connection error, HTTP 404,
    malformed XML) and one success -- the criterion most likely to pass in a
    narrow unit test and fail in a real run is per-token isolation across a
    mixed batch built through the composition root, not a collector
    instantiated directly.
    """
    from job_scraper.registry.builtins import (
        SourceBuildRequest,
        build_source,
        create_builtin_registry,
    )

    def fake_urlopen(request, timeout=None):
        url = request.full_url
        if "unreachable" in url:
            raise URLError("connection reset")
        if "retired" in url:
            raise HTTPError(url, 404, "Not Found", None, None)
        if "malformed" in url:
            return _FakeResponse(b"<<< not xml")
        if "goodboard" in url:
            return _FakeResponse(PERSONIO_XML.encode())
        raise AssertionError(f"unexpected URL in simulation: {url}")

    monkeypatch.setattr(base, "urlopen", fake_urlopen)

    registry = create_builtin_registry()
    request = SourceBuildRequest(
        http=_http_config(),
        settings=_source_config(
            [
                {"provider": "personio", "token": "unreachable"},
                {"provider": "personio", "token": "retired"},
                {"provider": "personio", "token": "malformed"},
                {"provider": "personio", "token": "goodboard", "company_name": "Good Co"},
            ]
        ),
    )
    source = build_source(registry, "ats_direct", request)
    records = list(source.collect(_window()))

    assert [record.source_job_id for record in records] == ["goodboard-4711"]
    assert records[0].company_name == "Good Co"
