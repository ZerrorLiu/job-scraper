from __future__ import annotations

from job_scraper.config import FiltersConfig
from job_scraper.domain.policies import FilterPolicy, FreshnessPolicy, TargetRule
from job_scraper.pipeline.normalize import parse_country_codes


def policy_from_legacy(
    filters: FiltersConfig,
    *,
    max_post_age_hours: int = 24,
) -> FilterPolicy:
    """Translate the TOML filter model into a domain FilterPolicy.

    This lives in `configuration/` rather than `pipeline/` because it is the
    only place that needs to know both shapes; keeping it under `pipeline/`
    forced every pipeline module to import the config layer.
    """

    rules = tuple(
        TargetRule(
            name=rule.name,
            keywords=tuple(rule.keywords),
            match_scope=rule.match_scope,
            keyword_groups=tuple(tuple(group) for group in rule.keyword_groups),
            minimum_keyword_matches=rule.minimum_keyword_matches,
        )
        for rule in filters.target_rules
    )
    return FilterPolicy(
        countries=tuple(parse_country_codes(filters.country)),
        signals=tuple(filters.include_keywords),
        excluded_terms=tuple(filters.exclude_keywords),
        acceptance_terms=tuple(filters.target_keywords),
        acceptance_scope=filters.target_match_scope,
        acceptance_rules=rules,
        allowed_companies=tuple(filters.company_names),
        full_time_only=filters.full_time_only,
        allow_part_time=filters.allow_part_time,
        allow_temporary=filters.allow_temporary,
        excluded_requirement_patterns=tuple(filters.excluded_requirement_patterns),
        require_english=filters.require_english,
        allowed_description_languages=tuple(filters.allowed_description_languages),
        minimum_english_ratio=filters.minimum_english_ratio,
        freshness=FreshnessPolicy(
            max_age_hours=max_post_age_hours,
            require_posted_at=False,
        ),
    )
