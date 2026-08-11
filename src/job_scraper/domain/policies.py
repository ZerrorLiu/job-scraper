from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class TargetRule:
    name: str
    keywords: tuple[str, ...] = ()
    match_scope: str = "title"
    keyword_groups: tuple[tuple[str, ...], ...] = ()
    minimum_keyword_matches: int = 1


@dataclass(frozen=True, slots=True)
class FreshnessPolicy:
    max_age_hours: int = 24
    require_posted_at: bool = False


@dataclass(frozen=True, slots=True)
class FilterPolicy:
    countries: tuple[str, ...]
    signals: tuple[str, ...] = ()
    excluded_terms: tuple[str, ...] = ()
    acceptance_terms: tuple[str, ...] = ()
    acceptance_scope: str = "title"
    acceptance_rules: tuple[TargetRule, ...] = ()
    allowed_companies: tuple[str, ...] = ()
    full_time_only: bool = True
    allow_part_time: bool = False
    allow_temporary: bool = False
    excluded_requirement_patterns: tuple[str, ...] = ()
    require_english: bool = True
    allowed_description_languages: tuple[str, ...] = ()
    minimum_english_ratio: float = 0.85
    freshness: FreshnessPolicy = field(default_factory=FreshnessPolicy)
