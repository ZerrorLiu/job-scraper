from __future__ import annotations

import pytest

from job_scraper.browser.chrome_cdp import (
    BrowserConnectionError,
    _assert_public_https_url,
    _different_destination,
    _find_apply_ctas,
)


@pytest.mark.parametrize(
    "value",
    [
        "http://example.test/apply",
        "https://localhost/apply",
        "https://127.0.0.1/apply",
        "https://user:password@example.test/apply",
    ],
)
def test_browser_rejects_unsafe_application_urls(value: str) -> None:
    with pytest.raises(BrowserConnectionError):
        _assert_public_https_url(value)


def test_browser_accepts_public_https_url() -> None:
    _assert_public_https_url("https://careers.example.test/apply/123")


def test_browser_detects_destination_redirect() -> None:
    assert _different_destination(
        "https://careers.example.test/apply/123?tracking=one",
        "https://careers.example.test/jobs/list",
    )
    assert not _different_destination(
        "https://careers.example.test/apply/123?tracking=one",
        "https://careers.example.test/apply/123?tracking=two",
    )


def test_browser_extracts_apply_ctas_without_clicking_them() -> None:
    assert _find_apply_ctas(
        ["Jetzt bewerben", "Save", "Apply now", "I'm interested", "Apply now"]
    ) == (
        "Jetzt bewerben",
        "Apply now",
        "I'm interested",
    )
