from __future__ import annotations

import json
from dataclasses import replace
from typing import Protocol

from job_scraper.domain.policies import FilterPolicy
from job_scraper.pipeline.language_filter import (
    is_allowed_description_language,
    matches_requirement_patterns,
)
from job_scraper.pipeline.normalize import looks_like_germany, looks_like_target_countries
from job_scraper.pipeline.role_filter import (
    company_matches_allowlist,
    has_excluded_keyword,
    is_full_time_role,
    text_matches_target,
)


class ExportRow(Protocol):
    def __getitem__(self, key: str) -> object: ...

    def keys(self) -> list[str]: ...


def export_row_is_germany(row: ExportRow) -> bool:
    location_text = str(row["location_text"] or "")
    payload = _payload(row)
    raw_country = str(payload.get("location_country") or "").strip()
    country = raw_country or str(row["country_code"] or "")
    return looks_like_germany(location_text, country)


def export_row_matches_policy(row: ExportRow, policy: FilterPolicy) -> bool:
    title = str(row["normalized_title"] or "")
    description = str(row["description_full"] or "")
    employment_type = str(row["employment_type"] or "")
    description_language = str(row["description_language"] or "")
    try:
        english_ratio = float(str(row["english_ratio"] or 0.0))
    except (TypeError, ValueError):
        english_ratio = 0.0

    if not _matches_country(row, policy):
        return False
    if policy.allowed_companies and not company_matches_allowlist(
        str(row["company_name"] or ""),
        list(policy.allowed_companies),
    ):
        return False
    if policy.full_time_only and not is_full_time_role(
        title,
        description,
        employment_type,
        allow_part_time=policy.allow_part_time,
        allow_temporary=policy.allow_temporary,
    ):
        return False
    if has_excluded_keyword(
        title,
        description,
        employment_type,
        list(policy.excluded_terms),
    ):
        return False
    payload = _payload(row)
    fallback_only = str(payload.get("detail_status") or "") == "email_fallback"
    acceptance_scope = "title" if fallback_only else policy.acceptance_scope
    acceptance_rules = (
        [replace(rule, match_scope="title") for rule in policy.acceptance_rules]
        if fallback_only
        else list(policy.acceptance_rules)
    )
    if not text_matches_target(
        title,
        description,
        list(policy.acceptance_terms),
        acceptance_scope,
        target_rules=acceptance_rules,
    ):
        return False
    language_text = " ".join(part for part in (title, description) if part)
    if (
        policy.excluded_requirement_patterns
        and language_text
        and matches_requirement_patterns(
            language_text,
            policy.excluded_requirement_patterns,
        )
    ):
        return False
    return is_allowed_description_language(
        description_language,
        english_ratio,
        policy.minimum_english_ratio,
        require_english=policy.require_english,
        allowed_languages=policy.allowed_description_languages,
    )


def _matches_country(row: ExportRow, policy: FilterPolicy) -> bool:
    location_text = str(row["location_text"] or "")
    payload = _payload(row)
    raw_country = str(payload.get("location_country") or "").strip()
    search_location = str(payload.get("search_location") or "").strip()
    country = str(row["country_code"] or "")
    country_filter = ",".join(policy.countries)
    if looks_like_target_countries(
        location_text,
        country,
        country_filter,
        raw_country=raw_country,
    ):
        return True
    if country in policy.countries and looks_like_target_countries(
        search_location,
        "",
        country_filter,
    ):
        return True
    return not _known_value(country) and not _known_value(raw_country)


def _payload(row: ExportRow) -> dict[str, object]:
    raw_payload_text = str(row["raw_payload_json"] or "")
    if not raw_payload_text:
        return {}
    try:
        payload = json.loads(raw_payload_text)
    except json.JSONDecodeError:
        return {}
    return dict(payload) if isinstance(payload, dict) else {}


def _known_value(value: object) -> bool:
    return str(value or "").strip().casefold() not in {"", "n/a", "unknown", "none", "null"}
