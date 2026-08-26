from __future__ import annotations

import re
from collections.abc import Sequence
from functools import lru_cache

from job_scraper.domain.policies import TargetRule

STUDENT_OR_INTERNSHIP_PATTERNS = (
    r"\bworking student\b",
    r"\bwerkstudent(?:in)?\b",
    r"\bstudent assistant\b",
    r"\bstudentische hilfskraft\b",
    r"\bpraktik(?:um|ant|antin)?\b",
    r"\bintern(?:ship)?\b",
    r"\benrolled student\b",
    r"\bmaster(?:'s)? student\b",
    r"\bphd student\b",
)
PART_TIME_PATTERNS = (r"\bpart[- ]time\b", r"\bteilzeit\b")


def is_full_time_role(
    title: str,
    description: str,
    employment_type: str = "unknown",
    *,
    allow_part_time: bool = False,
    allow_temporary: bool = False,
) -> bool:
    del description
    lowered_employment = (employment_type or "").strip().lower()
    if lowered_employment in {"internship", "student"}:
        return False
    combined = " ".join(part for part in [title, employment_type] if part).lower()
    if any(re.search(pattern, combined) for pattern in STUDENT_OR_INTERNSHIP_PATTERNS):
        return False
    if not allow_part_time and (
        lowered_employment == "part-time"
        or any(re.search(pattern, combined) for pattern in PART_TIME_PATTERNS)
    ):
        return False
    return allow_temporary or lowered_employment != "temporary"


def has_excluded_keyword(
    title: str, description: str, employment_type: str, exclude_keywords: list[str] | None = None
) -> bool:
    # Title-only by design: excluded_terms is a fast title-level filter.
    # Description-level exclusion is a separate policy
    # (excluded_requirement_patterns / RequirementExclusionStep).
    del description, employment_type
    if not exclude_keywords:
        return False
    return text_matches_keywords(title, "", exclude_keywords, "title")


def text_matches_keywords(
    title: str, description: str, keywords: list[str], match_scope: str
) -> bool:
    if not keywords:
        return True
    haystack = _Haystacks(title, description).for_scope(match_scope)
    return any_keyword_matches(keywords, haystack)


class _Haystacks:
    """The two searchable forms of one candidate, normalized at most once each.

    A profile can carry many acceptance rules -- eight in one real track -- and
    each one asks for either the title or the title-plus-description. Building
    that string per rule meant lowercasing and re-splitting the entire job
    description once per rule, which is the dominant cost of evaluating a
    candidate for any profile that scopes its rules to "combined".
    """

    __slots__ = ("_combined", "_description", "_title", "_title_text")

    def __init__(self, title: str, description: str) -> None:
        self._title = title
        self._description = description
        self._title_text: str | None = None
        self._combined: str | None = None

    def for_scope(self, match_scope: str) -> str:
        if match_scope == "combined":
            if self._combined is None:
                joined = " ".join(part for part in (self._title, self._description) if part)
                self._combined = normalize_text(joined)
            return self._combined
        if self._title_text is None:
            self._title_text = normalize_text(self._title)
        return self._title_text


def text_matches_target(
    title: str,
    description: str,
    keywords: list[str],
    match_scope: str,
    target_rules: Sequence[TargetRule] | None = None,
) -> bool:
    if target_rules:
        haystacks = _Haystacks(title, description)
        return any(_rule_matches(haystacks, rule) for rule in target_rules)
    return text_matches_keywords(title, description, keywords, match_scope)


def text_matches_target_rule(title: str, description: str, rule: TargetRule) -> bool:
    return _rule_matches(_Haystacks(title, description), rule)


def _rule_matches(haystacks: _Haystacks, rule: TargetRule) -> bool:
    haystack = haystacks.for_scope(rule.match_scope or "title")
    if rule.keyword_groups:
        # Every group must contribute a hit: the groups are AND-ed so a rule can
        # require, say, a domain term *and* a role term rather than either one.
        return all(any_keyword_matches(group, haystack) for group in rule.keyword_groups)
    minimum = max(1, rule.minimum_keyword_matches)
    if minimum == 1:
        return any_keyword_matches(rule.keywords, haystack)
    return count_matching_keywords(rule.keywords, haystack) >= minimum


def rule_haystack(title: str, description: str, match_scope: str) -> str:
    return _Haystacks(title, description).for_scope(match_scope)


def keyword_matches_text(keyword: str, haystack: str) -> bool:
    pattern = _keyword_pattern(normalize_text(keyword))
    return pattern is not None and pattern.search(haystack) is not None


def any_keyword_matches(keywords: Sequence[str], haystack: str) -> bool:
    """True when any keyword occurs, in one pass instead of one pass each.

    Searching a long description once per configured keyword was the single
    largest cost in evaluating a candidate: one real profile carries 46 rule
    keywords, so a description was scanned up to 46 times to answer one
    question. A single alternation answers it in one scan, with identical
    word-boundary semantics.
    """
    pattern = _keyword_group_pattern(tuple(keywords))
    return pattern is not None and pattern.search(haystack) is not None


def count_matching_keywords(keywords: Sequence[str], haystack: str) -> int:
    """How many distinct keywords occur, for rules with a minimum-match count.

    Still one scan: the alternation captures every hit, and distinct keywords
    are recovered from the matched text.
    """
    pattern = _keyword_group_pattern(tuple(keywords))
    if pattern is None:
        return 0
    found = {match.casefold() for match in pattern.findall(haystack)}
    if not found:
        return 0
    return sum(1 for keyword in {normalize_text(k) for k in keywords} if keyword in found)


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


@lru_cache(maxsize=4096)
def _keyword_pattern(normalized_keyword: str) -> re.Pattern[str] | None:
    """Compile once per distinct keyword.

    Configured keyword lists are long and reused for every candidate, so
    rebuilding the pattern string on each call thrashed the interpreter's own
    small regex cache.
    """
    if not normalized_keyword:
        return None
    escaped = re.escape(normalized_keyword).replace(r"\ ", r"\s+")
    return re.compile(rf"(?<![a-z0-9]){escaped}(?![a-z0-9])")


def normalize_text(value: str) -> str:
    return " ".join((value or "").lower().split())


def company_matches_allowlist(
    company_name: str, allowed_company_names: list[str] | None = None
) -> bool:
    if not allowed_company_names:
        return True
    return any_keyword_matches(allowed_company_names, normalize_text(company_name))
