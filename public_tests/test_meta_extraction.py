"""`extract_meta_content` must stay linear on hostile detail pages.

The live daily run stalled on a real posting: one `re.search` held the GIL for
ten CPU minutes, starving the SQLite writer and every fetch worker in the same
process. SIGTERM could not stop it either, because CPython only runs signal
handlers between bytecodes and the whole match was a single C call. These tests
pin both halves of the contract -- the extraction results, and the bound on how
long a miss may take.
"""

from time import process_time

import pytest

from job_scraper.integrations.email_recommendations import extract_meta_content


def test_reads_content_after_the_key():
    html = '<meta property="og:title" content="Senior C++ Engineer">'
    assert extract_meta_content(html, "og:title") == "Senior C++ Engineer"


def test_reads_content_before_the_key():
    html = '<meta content="A description" name="description">'
    assert extract_meta_content(html, "description") == "A description"


def test_key_match_ignores_case():
    html = '<meta PROPERTY="OG:Title" CONTENT="Mixed case">'
    assert extract_meta_content(html, "og:title") == "Mixed case"


def test_unescapes_and_normalizes_whitespace():
    html = '<meta name="description" content="Qt  &amp;\n  C++">'
    assert extract_meta_content(html, "description") == "Qt & C++"


def test_single_quoted_and_unquoted_values():
    assert extract_meta_content("<meta name='description' content='single'>", "description") == (
        "single"
    )
    assert extract_meta_content("<meta name=description content=bare>", "description") == "bare"


def test_missing_key_returns_empty():
    html = '<meta property="og:image" content="https://example.invalid/a.png">'
    assert extract_meta_content(html, "og:title") == ""


def test_ignores_a_matching_key_on_a_different_tag():
    """A `content` from a later, unrelated tag must not be borrowed."""
    html = '<meta property="og:title"><meta name="description" content="other">'
    assert extract_meta_content(html, "og:title") == ""


def test_picks_the_first_matching_tag():
    html = '<meta name="description" content="first"><meta name="description" content="second">'
    assert extract_meta_content(html, "description") == "first"


def _hostile_page() -> str:
    """A miss shaped like the posting that stalled production.

    Two ingredients drive the old blowup, so both are needed here. Many `<meta>`
    tags carry a `content` but never the wanted key, giving the engine hundreds
    of start positions. The body then supplies thousands of quoted attributes,
    and every one is another place a DOTALL `.*?` can stop and re-try the rest
    of the pattern. Cost grew quadratically with page size -- measured at 0.12s
    for 23KB, 1.88s for 103KB, 4.27s for the 192KB built here -- which puts a
    real multi-megabyte page in the tens of minutes.
    """
    tags = "".join(
        f'<meta name="unrelated{index}" content="value {index}">' for index in range(800)
    )
    body = "".join(
        f'<div class="card" data-id="{index}">'
        f'<a href="/job/{index}" title="Role {index}">x</a></div>'
        for index in range(2000)
    )
    return f"<html><head>{tags}</head><body>{body}</body></html>"


@pytest.mark.parametrize("key", ["og:title", "og:description"])
def test_a_miss_on_a_hostile_page_stays_fast(key):
    html = _hostile_page()
    started = process_time()
    assert extract_meta_content(html, key) == ""
    # The old pattern pair needed minutes here. Linear scanning of a few hundred
    # KB is milliseconds; one second leaves room for a slow CI box while still
    # failing loudly on any return to backtracking.
    assert process_time() - started < 1.0


def test_a_hit_on_a_hostile_page_still_finds_the_value():
    html = _hostile_page().replace("</head>", '<meta property="og:title" content="Found"></head>')
    started = process_time()
    assert extract_meta_content(html, "og:title") == "Found"
    assert process_time() - started < 1.0
