from __future__ import annotations

import re
from collections.abc import Sequence

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


def is_target_role(
    title: str,
    description: str,
    employment_type: str = "unknown",
    target_keywords: list[str] | None = None,
    match_scope: str = "title",
    target_rules: Sequence[object] | None = None,
    full_time_only: bool = True,
) -> bool:
    if full_time_only and not is_full_time_role(title, description, employment_type):
        return False
    return text_matches_target(
        title,
        description,
        target_keywords or [],
        match_scope,
        target_rules=target_rules,
    )


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
    del description, employment_type
    if not exclude_keywords:
        return False
    return text_matches_keywords(title, "", exclude_keywords, "title")


def text_matches_keywords(
    title: str, description: str, keywords: list[str], match_scope: str
) -> bool:
    if not keywords:
        return True
    title_text = normalize_text(title)
    combined_text = normalize_text(" ".join(part for part in [title, description] if part))
    haystack = combined_text if match_scope == "combined" else title_text
    return any(keyword_matches_text(keyword, haystack) for keyword in keywords)


def text_matches_target(
    title: str,
    description: str,
    keywords: list[str],
    match_scope: str,
    target_rules: Sequence[object] | None = None,
) -> bool:
    if target_rules:
        return any(text_matches_target_rule(title, description, rule) for rule in target_rules)
    return text_matches_keywords(title, description, keywords, match_scope)


def text_matches_target_rule(title: str, description: str, rule: object) -> bool:
    scope = target_rule_match_scope(rule)
    keyword_groups = target_rule_keyword_groups(rule)
    if keyword_groups:
        haystack = rule_haystack(title, description, scope)
        return all(
            any(keyword_matches_text(keyword, haystack) for keyword in group)
            for group in keyword_groups
        )
    keywords = target_rule_keywords(rule)
    minimum_matches = target_rule_minimum_keyword_matches(rule)
    haystack = rule_haystack(title, description, scope)
    matches = sum(keyword_matches_text(keyword, haystack) for keyword in keywords)
    return matches >= minimum_matches


def target_rule_keywords(rule: object) -> list[str]:
    raw_keywords = (
        rule.get("keywords", []) if isinstance(rule, dict) else getattr(rule, "keywords", [])
    )
    return [str(value).strip().lower() for value in raw_keywords if str(value).strip()]


def target_rule_keyword_groups(rule: object) -> list[list[str]]:
    raw_groups = (
        rule.get("keyword_groups", [])
        if isinstance(rule, dict)
        else getattr(rule, "keyword_groups", [])
    )
    groups: list[list[str]] = []
    for raw_group in raw_groups:
        if not isinstance(raw_group, list):
            continue
        group = [str(value).strip().lower() for value in raw_group if str(value).strip()]
        if group:
            groups.append(group)
    return groups


def target_rule_match_scope(rule: object) -> str:
    raw_scope = (
        rule.get("match_scope", "title")
        if isinstance(rule, dict)
        else getattr(rule, "match_scope", "title")
    )
    scope = str(raw_scope).strip().lower()
    return scope or "title"


def target_rule_minimum_keyword_matches(rule: object) -> int:
    raw_value = (
        rule.get("minimum_keyword_matches", 1)
        if isinstance(rule, dict)
        else getattr(rule, "minimum_keyword_matches", 1)
    )
    try:
        return max(1, int(raw_value))
    except (TypeError, ValueError):
        return 1


def rule_haystack(title: str, description: str, match_scope: str) -> str:
    title_text = normalize_text(title)
    combined_text = normalize_text(" ".join(part for part in [title, description] if part))
    return combined_text if match_scope == "combined" else title_text


def keyword_matches_text(keyword: str, haystack: str) -> bool:
    normalized_keyword = normalize_text(keyword)
    if not normalized_keyword:
        return False
    escaped = re.escape(normalized_keyword).replace(r"\ ", r"\s+")
    pattern = rf"(?<![a-z0-9]){escaped}(?![a-z0-9])"
    return re.search(pattern, haystack) is not None


def normalize_text(value: str) -> str:
    return " ".join((value or "").lower().split())


def company_matches_allowlist(
    company_name: str, allowed_company_names: list[str] | None = None
) -> bool:
    if not allowed_company_names:
        return True
    normalized_company = normalize_text(company_name)
    return any(keyword_matches_text(name, normalized_company) for name in allowed_company_names)
