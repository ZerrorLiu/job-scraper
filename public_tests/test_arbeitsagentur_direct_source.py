"""arbeitsagentur_direct: direct reads of the public employment-agency search API.

Coverage for docs/public/specs/2026-08-27-public-employment-agency-source.md.
All network access is faked; no test reaches the real service.
"""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, unquote, urlsplit

import pytest

import job_scraper.collectors.base as base
from job_scraper.collectors.arbeitsagentur import (
    ArbeitsagenturDirectCollector,
)
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


def _source_config(**options: object) -> SourceConfig:
    return SourceConfig(
        enabled=True,
        max_listing_pages=1,
        search_queries=["Fictional Engineer"],
        locations=["Berlin"],
        options=options,
    )


def _window() -> SearchWindow:
    return SearchWindow(started_at=datetime.now(UTC), overlap_hours=24)


# The payload shapes below mirror the live API as validated on 2026-08-27:
# the search response lists under `ergebnisliste`, the two endpoints do not
# share a naming convention, and only the detail response carries description
# text. Fixtures that invent a self-consistent naming pass while the adapter
# reads fields the provider never sends, which is exactly what these shapes
# exist to prevent.
def _search_payload(*postings: dict) -> bytes:
    return json.dumps({"ergebnisliste": list(postings), "maxErgebnisse": len(postings)}).encode()


def _detail_payload(refnr: str, description: str = "A complete fictional description.") -> bytes:
    return json.dumps(
        {
            "referenznummer": refnr,
            "stellenangebotsBeschreibung": description,
            "istPrivateArbeitsvermittlung": False,
            "istArbeitnehmerUeberlassung": False,
        }
    ).encode()


def _decode_detail_refnr(url: str) -> str:
    """Recover the reference a detail url addresses, which is base64 of it."""
    return base64.b64decode(unquote(url.rsplit("/", 1)[-1])).decode()


def _posting(refnr: str, **overrides: object) -> dict:
    posting = {
        "referenznummer": refnr,
        "stellenangebotsTitel": "Fictional Engineer",
        "firma": "Example GmbH",
        "stellenlokationen": [
            {"adresse": {"ort": "Berlin", "plz": "10115", "land": "DEUTSCHLAND"}}
        ],
        "datumErsteVeroeffentlichung": "2026-08-01",
    }
    posting.update(overrides)
    return posting


def _routed_urlopen(routes: dict[str, bytes]):
    def fake_urlopen(request, timeout=None):
        url = request.full_url
        for prefix, body in routes.items():
            if url.startswith(prefix):
                return _FakeResponse(body)
        raise URLError(f"no fake route for {url}")

    return fake_urlopen


def test_no_locations_or_queries_yields_nothing_without_a_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_if_called(*_a, **_k):
        raise AssertionError("no request should be made with an empty query matrix")

    monkeypatch.setattr(base, "urlopen", fail_if_called)

    collector = ArbeitsagenturDirectCollector(
        _http_config(),
        SourceConfig(enabled=True, max_listing_pages=1, search_queries=[], locations=[]),
    )

    assert list(collector.collect(_window())) == []


def test_a_full_first_page_fetches_a_second_page(monkeypatch: pytest.MonkeyPatch) -> None:
    from job_scraper.collectors.arbeitsagentur import PAGE_SIZE

    seen_pages: list[int] = []

    def fake_urlopen(request, timeout=None):
        if "/pc/v6/jobs" in request.full_url:
            page = int(request.full_url.split("page=")[1].split("&")[0])
            seen_pages.append(page)
            if page == 1:
                return _FakeResponse(
                    _search_payload(*(_posting(f"REF-{i}") for i in range(PAGE_SIZE)))
                )
            return _FakeResponse(_search_payload(_posting("REF-LAST")))
        refnr = _decode_detail_refnr(request.full_url)
        return _FakeResponse(_detail_payload(refnr))

    monkeypatch.setattr(base, "urlopen", fake_urlopen)
    monkeypatch.setattr(base.time, "sleep", lambda _seconds: None)

    settings = _source_config()
    settings.max_listing_pages = 2
    collector = ArbeitsagenturDirectCollector(_http_config(), settings)
    records = list(collector.collect(_window()))

    assert seen_pages == [1, 2]
    assert len(records) == PAGE_SIZE + 1


def test_a_non_403_http_error_is_not_wrapped_as_an_endpoint_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_urlopen(request, timeout=None):
        raise HTTPError(request.full_url, 500, "Server Error", None, None)

    monkeypatch.setattr(base, "urlopen", fake_urlopen)

    collector = ArbeitsagenturDirectCollector(
        _http_config(),
        _source_config(),
        event_logger=lambda message: events.append(message),
    )
    events: list[str] = []

    assert list(collector.collect(_window())) == []
    assert any("HTTP Error 500" in message for message in events)


def test_search_and_detail_hit_independently_pinned_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen_urls: list[str] = []

    def fake_urlopen(request, timeout=None):
        seen_urls.append(request.full_url)
        if "/pc/v6/jobs" in request.full_url:
            return _FakeResponse(_search_payload(_posting("REF-1")))
        return _FakeResponse(_detail_payload("REF-1"))

    monkeypatch.setattr(base, "urlopen", fake_urlopen)

    collector = ArbeitsagenturDirectCollector(_http_config(), _source_config())
    records = list(collector.collect(_window()))

    assert len(records) == 1
    assert any("/pc/v6/jobs" in url for url in seen_urls)
    assert any("/pc/v4/jobdetails/" in url for url in seen_urls)


def test_pinned_paths_are_configurable(monkeypatch: pytest.MonkeyPatch) -> None:
    seen_urls: list[str] = []

    def fake_urlopen(request, timeout=None):
        seen_urls.append(request.full_url)
        if "/pc/v7/jobs" in request.full_url:
            return _FakeResponse(_search_payload(_posting("REF-1")))
        return _FakeResponse(_detail_payload("REF-1"))

    monkeypatch.setattr(base, "urlopen", fake_urlopen)

    collector = ArbeitsagenturDirectCollector(
        _http_config(),
        _source_config(search_path="pc/v7/jobs"),
    )
    list(collector.collect(_window()))

    assert any("/pc/v7/jobs" in url for url in seen_urls)


def test_a_403_on_search_raises_a_named_non_retried_error_not_an_auth_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = 0

    def fake_urlopen(request, timeout=None):
        nonlocal attempts
        attempts += 1
        raise HTTPError(request.full_url, 403, "Forbidden", None, None)

    monkeypatch.setattr(base, "urlopen", fake_urlopen)

    collector = ArbeitsagenturDirectCollector(
        _http_config(),
        _source_config(),
        event_logger=lambda message: events.append(message),
    )
    events: list[str] = []
    records = list(collector.collect(_window()))

    assert records == []
    assert attempts == 1  # not retried
    assert any("403" in message and "credential" in message for message in events)


def test_detail_field_naming_does_not_collide_with_search_field_naming(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Search has no description field at all; only detail's stellenbeschreibung does."""

    def fake_urlopen(request, timeout=None):
        if "/pc/v6/jobs" in request.full_url:
            return _FakeResponse(_search_payload(_posting("REF-1")))
        return _FakeResponse(_detail_payload("REF-1", description="Full fictional detail text."))

    monkeypatch.setattr(base, "urlopen", fake_urlopen)

    collector = ArbeitsagenturDirectCollector(_http_config(), _source_config())
    records = list(collector.collect(_window()))

    assert records[0].job_description == "Full fictional detail text."


def test_a_detail_fetch_failure_drops_that_posting_not_the_listing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_urlopen(request, timeout=None):
        if "/pc/v6/jobs" in request.full_url:
            return _FakeResponse(_search_payload(_posting("REF-1"), _posting("REF-2")))
        refnr = _decode_detail_refnr(request.full_url)
        if refnr == "REF-1":
            raise URLError("connection reset")
        return _FakeResponse(_detail_payload(refnr))

    monkeypatch.setattr(base, "urlopen", fake_urlopen)

    collector = ArbeitsagenturDirectCollector(_http_config(), _source_config())
    records = list(collector.collect(_window()))

    assert [record.source_job_id for record in records] == ["REF-2"]


def test_the_same_posting_reached_through_two_queries_is_one_record(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_urlopen(request, timeout=None):
        if "/pc/v6/jobs" in request.full_url:
            return _FakeResponse(_search_payload(_posting("REF-SHARED")))
        return _FakeResponse(_detail_payload("REF-SHARED"))

    monkeypatch.setattr(base, "urlopen", fake_urlopen)

    collector = ArbeitsagenturDirectCollector(
        _http_config(),
        _source_config(),
    )
    collector.search_queries = ["Fictional Engineer", "Fictional Developer"]
    records = list(collector.collect(_window()))

    assert len(records) == 1


def _search_params(url: str) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}


# The exclusions are applied by the API, not by inspecting each posting: the
# per-posting flags are optional in both payloads, so a posting that omits one
# cannot be told from a posting that sets it false. These tests therefore
# assert the request the adapter sends, and let the fake service apply the
# filter the way the real one does.
def test_private_intermediary_postings_are_excludable_independently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: list[dict[str, str]] = []

    def fake_urlopen(request, timeout=None):
        if "/pc/v6/jobs" in request.full_url:
            params = _search_params(request.full_url)
            sent.append(params)
            postings = [_posting("REF-EMPLOYER")]
            if params.get("pav") != "false":
                postings.insert(0, _posting("REF-AGENCY"))
            return _FakeResponse(_search_payload(*postings))
        return _FakeResponse(_detail_payload(_decode_detail_refnr(request.full_url)))

    monkeypatch.setattr(base, "urlopen", fake_urlopen)

    collector = ArbeitsagenturDirectCollector(
        _http_config(),
        _source_config(exclude_private_intermediary=True),
    )
    records = list(collector.collect(_window()))

    assert sent[0]["pav"] == "false"
    assert "zeitarbeit" not in sent[0]
    assert [record.source_job_id for record in records] == ["REF-EMPLOYER"]


def test_temporary_employment_postings_are_excludable_independently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: list[dict[str, str]] = []

    def fake_urlopen(request, timeout=None):
        if "/pc/v6/jobs" in request.full_url:
            params = _search_params(request.full_url)
            sent.append(params)
            postings = [_posting("REF-PERM")]
            if params.get("zeitarbeit") != "false":
                postings.insert(0, _posting("REF-TEMP"))
            return _FakeResponse(_search_payload(*postings))
        return _FakeResponse(_detail_payload(_decode_detail_refnr(request.full_url)))

    monkeypatch.setattr(base, "urlopen", fake_urlopen)

    collector = ArbeitsagenturDirectCollector(
        _http_config(),
        _source_config(exclude_temporary_employment=True),
    )
    records = list(collector.collect(_window()))

    assert sent[0]["zeitarbeit"] == "false"
    assert "pav" not in sent[0]
    assert [record.source_job_id for record in records] == ["REF-PERM"]


def test_the_default_profile_excludes_neither_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[dict[str, str]] = []

    def fake_urlopen(request, timeout=None):
        if "/pc/v6/jobs" in request.full_url:
            sent.append(_search_params(request.full_url))
            return _FakeResponse(_search_payload(_posting("REF-AGENCY")))
        return _FakeResponse(_detail_payload(_decode_detail_refnr(request.full_url)))

    monkeypatch.setattr(base, "urlopen", fake_urlopen)

    collector = ArbeitsagenturDirectCollector(_http_config(), _source_config())
    records = list(collector.collect(_window()))

    assert "pav" not in sent[0]
    assert "zeitarbeit" not in sent[0]
    assert [record.source_job_id for record in records] == ["REF-AGENCY"]


def test_a_query_returning_zero_results_is_not_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(base, "urlopen", lambda *_a, **_k: _FakeResponse(_search_payload()))

    collector = ArbeitsagenturDirectCollector(_http_config(), _source_config())

    assert list(collector.collect(_window())) == []


def test_one_failing_query_does_not_stop_the_run(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(request, timeout=None):
        if "Fictional Engineer" in request.full_url:
            raise URLError("connection reset")
        if "/pc/v6/jobs" in request.full_url:
            return _FakeResponse(_search_payload(_posting("REF-OK")))
        return _FakeResponse(_detail_payload("REF-OK"))

    monkeypatch.setattr(base, "urlopen", fake_urlopen)

    collector = ArbeitsagenturDirectCollector(_http_config(), _source_config())
    collector.search_queries = ["Fictional Engineer", "Fictional Developer"]
    records = list(collector.collect(_window()))

    assert [record.source_job_id for record in records] == ["REF-OK"]


def test_independent_user_path_simulation_through_the_production_registry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Builds the source the way `job-scraper run` does, through the real registry.

    A 2-query x 2-location matrix where one (query, location) pair's search
    call fails outright and another pair's detail fetch fails for one of its
    two postings -- per-query failure isolation across a full matrix is the
    criterion most likely to pass in a narrow unit test and fail in a run.
    """
    from job_scraper.registry.builtins import (
        SourceBuildRequest,
        build_source,
        create_builtin_registry,
    )

    def fake_urlopen(request, timeout=None):
        url = request.full_url
        if "Broken+Query" in url:
            raise URLError("connection reset")
        if "/pc/v6/jobs" in url:
            if "Hamburg" in url:
                return _FakeResponse(_search_payload(_posting("REF-HH-1"), _posting("REF-HH-2")))
            return _FakeResponse(_search_payload(_posting("REF-B-1")))
        refnr = _decode_detail_refnr(url)
        if refnr == "REF-HH-2":
            raise URLError("connection reset")
        return _FakeResponse(_detail_payload(refnr))

    monkeypatch.setattr(base, "urlopen", fake_urlopen)

    registry = create_builtin_registry()
    settings = _source_config()
    settings.search_queries = ["Good Query", "Broken Query"]
    settings.locations = ["Berlin", "Hamburg"]
    request = SourceBuildRequest(http=_http_config(), settings=settings)
    source = build_source(registry, "arbeitsagentur_direct", request)

    records = {record.source_job_id for record in source.collect(_window())}

    assert records == {"REF-B-1", "REF-HH-1"}


def test_every_mapped_field_is_populated_from_a_realistic_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pins the whole field mapping against the live payload shape at once.

    The adapter shipped reading the previous endpoint version's field names
    throughout while the search path was pinned to the current one. Nothing
    failed: the search response's result list was absent under the name being
    read, so every query returned an empty list and the source produced no
    records and no error. Asserting record identity alone does not catch that
    -- a fixture inventing the same wrong names passes -- so this pins each
    mapped field to a payload shaped like the real one.
    """

    def fake_urlopen(request, timeout=None):
        if "/pc/v6/jobs" in request.full_url:
            return _FakeResponse(
                _search_payload(
                    {
                        "referenznummer": "REF-MAP",
                        "stellenangebotsTitel": "Fictional Coordinator",
                        "firma": "Example Handels GmbH",
                        "stellenlokationen": [
                            {
                                "adresse": {
                                    "ort": "Beispielstadt",
                                    "plz": "12345",
                                    "land": "DEUTSCHLAND",
                                }
                            }
                        ],
                        "datumErsteVeroeffentlichung": "2026-08-01",
                        "externeURL": "https://example.invalid/apply",
                    }
                )
            )
        return _FakeResponse(
            json.dumps(
                {
                    "referenznummer": "REF-MAP",
                    "stellenangebotsBeschreibung": "A complete fictional description.",
                    "istPrivateArbeitsvermittlung": False,
                    "istArbeitnehmerUeberlassung": True,
                }
            ).encode()
        )

    monkeypatch.setattr(base, "urlopen", fake_urlopen)

    collector = ArbeitsagenturDirectCollector(_http_config(), _source_config())
    (record,) = list(collector.collect(_window()))

    assert record.source_job_id == "REF-MAP"
    assert record.title == "Fictional Coordinator"
    assert record.company_name == "Example Handels GmbH"
    # The country word must survive into the text: downstream country checks
    # fall back to a known-places list, which a small town is not on.
    assert record.location_raw == "Beispielstadt, 12345, Deutschland"
    assert record.posted_at_text == "2026-08-01"
    assert record.job_description == "A complete fictional description."
    assert record.canonical_url.endswith("/jobdetail/REF-MAP")
    assert record.employment_type == "temporary"
    assert record.raw_payload["external_url"] == "https://example.invalid/apply"
    assert record.raw_payload["is_private_intermediary"] is False


def test_a_posting_omitting_the_optional_flags_does_not_report_them_as_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`None` means the provider did not say, which is not the same as false."""

    def fake_urlopen(request, timeout=None):
        if "/pc/v6/jobs" in request.full_url:
            return _FakeResponse(_search_payload(_posting("REF-QUIET")))
        return _FakeResponse(
            json.dumps(
                {
                    "referenznummer": "REF-QUIET",
                    "stellenangebotsBeschreibung": "A complete fictional description.",
                }
            ).encode()
        )

    monkeypatch.setattr(base, "urlopen", fake_urlopen)

    collector = ArbeitsagenturDirectCollector(_http_config(), _source_config())
    (record,) = list(collector.collect(_window()))

    assert record.raw_payload["is_private_intermediary"] is None
    assert record.raw_payload["is_temporary_employment"] is None
    assert record.raw_payload["external_url"] is None
    assert record.employment_type == "unknown"
