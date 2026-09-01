from __future__ import annotations

import re
from collections.abc import Sequence
from functools import lru_cache


def any_keyword_matches(keywords: Sequence[str], haystack: str) -> bool:
    """True when any keyword occurs, in one pass instead of one pass each."""
    pattern = _keyword_group_pattern(tuple(keywords))
    return pattern is not None and pattern.search(haystack) is not None


@lru_cache(maxsize=1024)
def _keyword_group_pattern(keywords: tuple[str, ...]) -> re.Pattern[str] | None:
    normalized = sorted(
        {text for keyword in keywords if (text := normalize_text(keyword))},
        key=len,
        reverse=True,
    )
    if not normalized:
        return None
    alternation = "|".join(re.escape(text).replace(r"\ ", r"\s+") for text in normalized)
    return re.compile(rf"(?<![a-z0-9])(?:{alternation})(?![a-z0-9])")


def normalize_text(value: str) -> str:
    return " ".join((value or "").lower().split())


def company_matches_allowlist(
    company_name: str, allowed_company_names: list[str] | None = None
) -> bool:
    if not allowed_company_names:
        return True
    return any_keyword_matches(allowed_company_names, normalize_text(company_name))
