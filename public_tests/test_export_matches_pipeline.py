"""CsvSink's language pre-filter must follow the same two-mode contract the
former `language` acquisition step followed, independent of content-quality
gating (which no longer exists as deterministic policy -- see
`pipeline/export_filter.py`).
"""

from __future__ import annotations

from job_scraper.domain.policies import FilterPolicy


class _RecordingReader:
    """Captures the language filter the sink pushes down into the query."""

    def __init__(self) -> None:
        self.languages: list[str] | object | None = "not called"

    def export_jobs(self, languages: list[str] | None = None) -> list[dict[str, object]]:
        self.languages = languages
        return []


def _publish_with(policy: FilterPolicy, tmp_path) -> _RecordingReader:
    from job_scraper.adapters.sinks.csv import CsvSink
    from job_scraper.ports.sinks import PublishContext

    reader = _RecordingReader()
    CsvSink(reader, tmp_path / "export.csv", policy).publish(
        [],
        PublishContext(run_id="run-1", profile_id="export-test"),
    )
    return reader


def test_export_reads_every_language_the_policy_admits(tmp_path) -> None:
    """A profile that admits German must not have German dropped on the way out.

    The sink chooses which languages to read *before* export, so a language
    excluded here can never be re-admitted later. It previously read
    `require_english` regardless of the allowed-language list, which meant a
    profile admitting German acquired German postings and then exported none
    of them -- silently, because the rows were still in the database.
    """
    policy = FilterPolicy(
        countries=("DE",),
        require_english=True,
        allowed_description_languages=("English", "German", "Mixed", "Unknown"),
    )

    reader = _publish_with(policy, tmp_path)

    assert reader.languages == ["English", "German", "Mixed", "Unknown"]


def test_export_falls_back_to_english_only_when_no_list_is_configured(tmp_path) -> None:
    policy = FilterPolicy(countries=("DE",), require_english=True)

    assert _publish_with(policy, tmp_path).languages == ["English"]


def test_export_reads_every_language_when_english_is_not_required(tmp_path) -> None:
    policy = FilterPolicy(countries=("DE",), require_english=False)

    assert _publish_with(policy, tmp_path).languages is None
