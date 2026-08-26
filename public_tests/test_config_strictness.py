"""A setting the loader does not understand must fail, not be ignored."""

from __future__ import annotations

from pathlib import Path

import pytest

from job_scraper.config import load_config

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
minimum_english_ratio = 0.5

[http]
user_agent = "fictional/1.0"
timeout_seconds = 10
base_delay_seconds = 1.0
jitter_seconds = 0.5
max_retries = 2

[sources.linkedin_direct]
enabled = false
"""


def _write(tmp_path: Path, extra: str = "", base: str = BASE) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(base + extra, encoding="utf-8")
    return path


def test_a_valid_config_still_loads(tmp_path: Path) -> None:
    assert load_config(_write(tmp_path)).project.timezone == "UTC"


@pytest.mark.parametrize(
    "section,line,expected",
    [
        ("project", "recent_post_age_hour = 24", "recent_post_age_hours"),
        ("filters", "full_time_onl = true", "full_time_only"),
        ("http", "timeout_second = 5", "timeout_seconds"),
        ("notion", 'daily_table_prefx = "AI"', "daily_table_prefix"),
    ],
)
def test_a_mistyped_field_is_rejected_with_a_suggestion(
    tmp_path: Path, section: str, line: str, expected: str
) -> None:
    """Silently defaulting a mistyped field is how a config lies about itself."""
    if section == "notion":
        config = _write(tmp_path, f"\n[notion]\nenabled = true\n{line}\n")
    else:
        base = BASE.replace(f"[{section}]", f"[{section}]\n{line}")
        config = _write(tmp_path, base=base)

    with pytest.raises(ValueError) as error:
        load_config(config)

    assert section in str(error.value)
    assert expected in str(error.value)


def test_an_unknown_field_without_a_near_match_is_still_rejected(tmp_path: Path) -> None:
    base = BASE.replace("[project]", "[project]\nzzz_unused = 1")

    with pytest.raises(ValueError, match="zzz_unused"):
        load_config(_write(tmp_path, base=base))


def test_a_notion_token_in_the_config_file_is_refused(tmp_path: Path) -> None:
    """Notion credentials remain environment-only runtime configuration."""
    config = _write(tmp_path, '\n[notion]\nenabled = true\ntoken = "secret"\n')

    with pytest.raises(ValueError, match="token"):
        load_config(config)


def test_source_sections_keep_their_documented_extension_point(tmp_path: Path) -> None:
    """Unknown keys under sources.* are adapter options, not typos."""
    base = BASE.replace("enabled = false", 'enabled = false\ncustom_adapter_setting = "value"')

    config = load_config(_write(tmp_path, base=base))

    assert config.sources["linkedin_direct"].options["custom_adapter_setting"] == "value"


def test_a_typo_of_a_known_source_field_is_still_caught(tmp_path: Path) -> None:
    base = BASE.replace("enabled = false", "enabled = false\nmax_detial_fetches = 5")

    with pytest.raises(ValueError, match="max_detail_fetches"):
        load_config(_write(tmp_path, base=base))
