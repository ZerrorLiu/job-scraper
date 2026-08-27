"""minimum_english_ratio and allowed_description_languages are one contract.

Regression coverage for docs/public/specs/2026-08-27-description-language-policy-defect.md:
a non-empty allowed_description_languages decides the verdict by label
membership alone, and minimum_english_ratio is refused at load time rather
than silently doing nothing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from job_scraper.config import load_config, resolve_minimum_english_ratio
from job_scraper.pipeline.language_filter import is_allowed_description_language

BASE = """
[project]
timezone = "UTC"
database_path = "jobs.db"
export_dir = "exports"
overlap_hours = 24

[filters]
country = "DE"
include_keywords = ["engineer"]
exclude_keywords = []
{filters_extra}

[http]
user_agent = "fictional/1.0"
timeout_seconds = 10
base_delay_seconds = 1.0
jitter_seconds = 0.5
max_retries = 2

[sources.linkedin_direct]
enabled = false
"""


def _write(tmp_path: Path, filters_extra: str) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(BASE.format(filters_extra=filters_extra), encoding="utf-8")
    return path


@pytest.mark.parametrize(
    "language,ratio",
    [
        ("German", 0.0),
        ("German", 0.4),
        ("Mixed", 0.6),
        ("Mixed", 0.74),
        ("English", 0.75),
        ("English", 0.84),
        ("English", 1.0),
    ],
)
def test_a_non_empty_allowed_list_admits_every_label_it_names_regardless_of_ratio(
    language: str, ratio: float
) -> None:
    """The historical bug rejected English 0.75-0.84 while accepting less-English labels.

    With minimum_english_ratio decoupled from this branch, every label named
    in the list is admitted purely by membership -- no ratio can carve out an
    inverted band inside it.
    """
    assert is_allowed_description_language(
        language,
        ratio,
        english_threshold=0.85,
        require_english=True,
        allowed_languages=("English", "German", "Mixed", "Unknown"),
    )


def test_a_label_absent_from_a_non_empty_allowed_list_is_rejected_regardless_of_ratio() -> None:
    assert not is_allowed_description_language(
        "German",
        0.9,
        english_threshold=0.0,
        require_english=True,
        allowed_languages=("English",),
    )


def test_no_ratio_produces_an_inversion_across_the_full_ratio_range() -> None:
    """Property check: within a fixed non-empty allowed list, verdict never
    depends on the ratio, so a higher-English description can never be
    rejected while a lower-English one carrying an admitted label is
    accepted."""
    allowed = ("English", "German", "Mixed", "Unknown")
    for threshold in (0.0, 0.5, 0.75, 0.85, 0.99, 1.0):
        for label in allowed:
            for ratio in (0.0, 0.25, 0.5, 0.74, 0.75, 0.84, 0.85, 1.0):
                assert is_allowed_description_language(
                    label,
                    ratio,
                    english_threshold=threshold,
                    require_english=True,
                    allowed_languages=allowed,
                )


def test_the_empty_list_branch_still_gates_on_ratio_and_require_english() -> None:
    assert is_allowed_description_language(
        "English",
        0.9,
        english_threshold=0.85,
        require_english=True,
        allowed_languages=(),
    )
    assert not is_allowed_description_language(
        "English",
        0.5,
        english_threshold=0.85,
        require_english=True,
        allowed_languages=(),
    )
    assert not is_allowed_description_language(
        "German",
        0.0,
        english_threshold=0.85,
        require_english=True,
        allowed_languages=(),
    )


def test_the_empty_list_branch_admits_everything_when_require_english_is_false() -> None:
    assert is_allowed_description_language(
        "German",
        0.0,
        english_threshold=0.85,
        require_english=False,
        allowed_languages=(),
    )


def test_a_profile_setting_both_keys_is_refused_at_load_naming_the_contradiction(
    tmp_path: Path,
) -> None:
    config = _write(
        tmp_path,
        "minimum_english_ratio = 0.85\n"
        'allowed_description_languages = ["English", "German", "Mixed", "Unknown"]\n',
    )

    with pytest.raises(ValueError, match="minimum_english_ratio") as error:
        load_config(config)

    assert "allowed_description_languages" in str(error.value)


def test_a_profile_using_only_the_allowed_list_loads_without_the_ratio_key(
    tmp_path: Path,
) -> None:
    config = _write(
        tmp_path,
        'allowed_description_languages = ["English", "German", "Mixed", "Unknown"]\n',
    )

    loaded = load_config(config)

    assert loaded.filters.minimum_english_ratio == 0.0
    assert loaded.filters.allowed_description_languages == [
        "English",
        "German",
        "Mixed",
        "Unknown",
    ]


def test_omitting_the_ratio_with_an_empty_allowed_list_names_the_key(tmp_path: Path) -> None:
    config = _write(tmp_path, "")

    with pytest.raises(ValueError, match="minimum_english_ratio"):
        load_config(config)


def test_a_profile_using_only_the_ratio_still_loads(tmp_path: Path) -> None:
    config = _write(tmp_path, "minimum_english_ratio = 0.5\n")

    loaded = load_config(config)

    assert loaded.filters.minimum_english_ratio == 0.5
    assert loaded.filters.allowed_description_languages == []


def test_resolve_minimum_english_ratio_matches_load_config_directly() -> None:
    with pytest.raises(ValueError, match="minimum_english_ratio"):
        resolve_minimum_english_ratio(
            {"minimum_english_ratio": 0.85},
            ["English", "German"],
        )

    assert resolve_minimum_english_ratio({}, ["English"]) == 0.0
    assert resolve_minimum_english_ratio({"minimum_english_ratio": 0.6}, []) == 0.6
