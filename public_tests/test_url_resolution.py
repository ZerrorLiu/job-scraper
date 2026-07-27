from job_scraper.adapters.sinks.notion_payload import build_job_title
from job_scraper.domain.url_resolution import resolve_external_application_url


def test_url_resolution_requires_a_distinct_public_https_destination() -> None:
    source = "https://jobs.example.test/view/123"

    assert (
        resolve_external_application_url(source, "https://careers.example.test/apply/123")
        == "https://careers.example.test/apply/123"
    )
    assert resolve_external_application_url(source, source) == ""
    assert resolve_external_application_url(source, "http://careers.example.test/apply/123") == ""
    assert resolve_external_application_url(source, "https://localhost/apply/123") == ""
    assert resolve_external_application_url(source, "https://user:pass@example.test/apply") == ""


def test_notion_title_link_uses_only_a_resolved_application_url() -> None:
    source_only = type(
        "FakeJob",
        (),
        {"title": "Example", "source_url": "https://jobs.example.test/1", "application_url": ""},
    )()
    resolved = type(
        "FakeJob",
        (),
        {
            "title": "Example",
            "source_url": "https://jobs.example.test/1",
            "application_url": "https://careers.example.test/1",
        },
    )()

    assert "link" not in build_job_title(source_only)["title"][0]["text"]
    assert build_job_title(resolved)["title"][0]["text"]["link"]["url"] == (
        "https://careers.example.test/1"
    )
