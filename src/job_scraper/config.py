from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from difflib import get_close_matches
from pathlib import Path


@dataclass(slots=True)
class HttpConfig:
    user_agent: str
    timeout_seconds: int
    base_delay_seconds: float
    jitter_seconds: float
    max_retries: int


@dataclass(slots=True)
class SourceConfig:
    enabled: bool = False
    max_listing_pages: int = 1
    max_detail_fetches: int = 1
    detail_workers: int = 1
    query_workers: int = 1
    search_queries: list[str] = field(default_factory=list)
    locations: list[str] = field(default_factory=list)
    inherit_search_matrix_from: str = ""
    results_per_input: int = 10
    options: dict[str, object] = field(default_factory=dict)


@dataclass(slots=True)
class TargetRuleConfig:
    name: str
    keywords: list[str]
    match_scope: str
    keyword_groups: list[list[str]] = field(default_factory=list)
    minimum_keyword_matches: int = 1


@dataclass(slots=True)
class FiltersConfig:
    country: str
    include_keywords: list[str]
    exclude_keywords: list[str]
    target_keywords: list[str]
    target_match_scope: str
    minimum_english_ratio: float
    target_rules: list[TargetRuleConfig] = field(default_factory=list)
    company_names: list[str] = field(default_factory=list)
    excluded_company_names: list[str] = field(default_factory=list)
    full_time_only: bool = True
    allow_part_time: bool = False
    allow_temporary: bool = False
    excluded_requirement_patterns: list[str] = field(default_factory=list)
    require_english: bool = True
    allowed_description_languages: list[str] = field(default_factory=list)


@dataclass(slots=True)
class NotionConfig:
    enabled: bool
    token: str = ""
    database_id: str = ""
    data_source_id: str = ""
    parent_page_id: str = ""
    container_title: str = "Job Scraper Daily Tables"
    daily_table_prefix: str = ""


@dataclass(slots=True)
class ProjectConfig:
    timezone: str
    database_path: Path
    overlap_hours: int
    export_dir: Path
    track_label: str = ""
    export_filename_prefix: str = "jobs"
    recent_post_age_hours: int = 24
    bootstrap_post_age_hours: int = 24
    workspace_database_path: Path | None = None
    retained_exports: int = 0


@dataclass(slots=True)
class AppConfig:
    project: ProjectConfig
    filters: FiltersConfig
    http: HttpConfig
    sources: dict[str, SourceConfig]
    notion: NotionConfig


KNOWN_PROJECT_FIELDS = {
    "timezone",
    "database_path",
    "overlap_hours",
    "export_dir",
    "track_label",
    "export_filename_prefix",
    "recent_post_age_hours",
    "bootstrap_post_age_hours",
    "workspace_database_path",
    "retained_exports",
}
KNOWN_FILTER_FIELDS = {
    "country",
    "include_keywords",
    "exclude_keywords",
    "target_keywords",
    "target_match_scope",
    "target_rules",
    "minimum_english_ratio",
    "company_names",
    "excluded_company_names",
    "full_time_only",
    "allow_part_time",
    "allow_temporary",
    "excluded_requirement_patterns",
    "require_english",
    "allowed_description_languages",
}
KNOWN_HTTP_FIELDS = {
    "user_agent",
    "timeout_seconds",
    "base_delay_seconds",
    "jitter_seconds",
    "max_retries",
}
KNOWN_NOTION_FIELDS = {
    "enabled",
    "data_source_id",
    "parent_page_id",
    "container_title",
    "daily_table_prefix",
}


def reject_unknown_keys(section: str, values: dict, known: set[str]) -> None:
    """Fail on a key this section does not understand.

    Unlike `sources.*`, these sections have no documented extension point, so a
    key that is not recognized is a typo or a setting that was removed. Silently
    ignoring it meant a config could look configured while running on defaults.
    """
    unknown = sorted(key for key in values if key not in known)
    if not unknown:
        return
    hints = []
    for key in unknown:
        suggestion = get_close_matches(key, known, n=1, cutoff=0.8)
        hints.append(f"{key} (did you mean {suggestion[0]!r}?)" if suggestion else key)
    raise ValueError(f"[{section}] has unknown fields: {', '.join(hints)}")


def load_config(path: str | Path) -> AppConfig:
    config_path = Path(path)
    config_root = config_path.parent
    with config_path.open("rb") as handle:
        raw = tomllib.load(handle)

    project = raw["project"]
    filters = raw["filters"]
    http = raw["http"]
    sources = raw["sources"]
    notion = raw.get("notion", {})
    reject_unknown_keys("project", project, KNOWN_PROJECT_FIELDS)
    reject_unknown_keys("filters", filters, KNOWN_FILTER_FIELDS)
    reject_unknown_keys("http", http, KNOWN_HTTP_FIELDS)
    reject_unknown_keys("notion", notion, KNOWN_NOTION_FIELDS)
    notion_token = environment_credential("NOTION_INTEGRATION_TOKEN")
    notion_database_id = environment_credential("NOTION_DATABASE_ID")
    notion_parent_page_id = environment_credential("NOTION_PARENT_PAGE_ID")

    source_configs = {
        str(source_id).strip().lower().replace("-", "_"): load_source_config(
            sources,
            str(source_id),
        )
        for source_id in sources
    }
    for source_name, source_config in source_configs.items():
        inherit_search_matrix(source_name, source_config, source_configs)

    allowed_description_languages = [
        str(value).strip()
        for value in filters.get("allowed_description_languages", [])
        if str(value).strip()
    ]
    minimum_english_ratio = resolve_minimum_english_ratio(
        filters,
        allowed_description_languages,
    )

    return AppConfig(
        project=ProjectConfig(
            timezone=project["timezone"],
            database_path=(config_root / project["database_path"]).resolve(),
            overlap_hours=int(project["overlap_hours"]),
            export_dir=(config_root / project["export_dir"]).resolve(),
            track_label=str(project.get("track_label", "")).strip(),
            export_filename_prefix=str(project.get("export_filename_prefix", "jobs")).strip()
            or "jobs",
            recent_post_age_hours=int(project.get("recent_post_age_hours", 24)),
            retained_exports=max(0, int(project.get("retained_exports", 0))),
            bootstrap_post_age_hours=int(
                project.get("bootstrap_post_age_hours", project.get("recent_post_age_hours", 24))
            ),
            workspace_database_path=(
                (config_root / str(project["workspace_database_path"])).resolve()
                if str(project.get("workspace_database_path", "")).strip()
                else None
            ),
        ),
        filters=FiltersConfig(
            country=filters["country"],
            include_keywords=[value.lower() for value in filters["include_keywords"]],
            exclude_keywords=[value.lower() for value in filters["exclude_keywords"]],
            target_keywords=[
                value.lower()
                for value in filters.get("target_keywords", filters["include_keywords"])
            ],
            target_match_scope=str(filters.get("target_match_scope", "title")).strip().lower()
            or "title",
            minimum_english_ratio=minimum_english_ratio,
            target_rules=load_target_rules(filters),
            company_names=[
                str(value).strip()
                for value in filters.get("company_names", [])
                if str(value).strip()
            ],
            excluded_company_names=[
                str(value).strip()
                for value in filters.get("excluded_company_names", [])
                if str(value).strip()
            ],
            full_time_only=bool(filters.get("full_time_only", True)),
            allow_part_time=bool(filters.get("allow_part_time", False)),
            allow_temporary=bool(filters.get("allow_temporary", False)),
            excluded_requirement_patterns=[
                str(value).strip()
                for value in filters.get("excluded_requirement_patterns", [])
                if str(value).strip()
            ],
            require_english=bool(filters.get("require_english", True)),
            allowed_description_languages=allowed_description_languages,
        ),
        http=HttpConfig(
            user_agent=http["user_agent"],
            timeout_seconds=int(http["timeout_seconds"]),
            base_delay_seconds=float(http["base_delay_seconds"]),
            jitter_seconds=float(http["jitter_seconds"]),
            max_retries=int(http["max_retries"]),
        ),
        sources=source_configs,
        notion=NotionConfig(
            enabled=bool(notion.get("enabled", False)),
            token=notion_token,
            database_id=notion_database_id,
            data_source_id=str(notion.get("data_source_id", "")).strip(),
            parent_page_id=notion_parent_page_id or str(notion.get("parent_page_id", "")).strip(),
            container_title=str(notion.get("container_title", "Job Scraper Daily Tables")),
            daily_table_prefix=str(notion.get("daily_table_prefix", "")).strip(),
        ),
    )


def resolve_minimum_english_ratio(filters: dict, allowed_description_languages: list[str]) -> float:
    """Resolve `minimum_english_ratio`, honoring which of the two language modes applies.

    `allowed_description_languages`, when non-empty, decides the language
    verdict by membership alone -- see `is_allowed_description_language` in
    `pipeline/language_filter.py`. `minimum_english_ratio` cannot affect any
    verdict in that mode, so a profile setting both is asked to restate its
    intent rather than have the ratio silently do nothing. See
    `docs/public/specs/2026-08-27-description-language-policy-defect.md`.
    """
    has_ratio = "minimum_english_ratio" in filters
    if allowed_description_languages:
        if has_ratio:
            raise ValueError(
                "[filters] minimum_english_ratio has no effect once "
                "allowed_description_languages is set: the language verdict is decided by "
                "list membership alone. Remove minimum_english_ratio, or empty "
                "allowed_description_languages to use the ratio gate instead."
            )
        return 0.0
    if not has_ratio:
        raise ValueError(
            "[filters] minimum_english_ratio is required when allowed_description_languages "
            "is empty"
        )
    return float(filters["minimum_english_ratio"])


def load_source_config(sources: dict, source_name: str) -> SourceConfig:
    raw_source = sources.get(source_name)
    if raw_source is None:
        return SourceConfig(
            enabled=False,
            max_listing_pages=0,
            max_detail_fetches=0,
        )
    if not isinstance(raw_source, dict):
        raise ValueError(f"sources.{source_name} must be a TOML table")
    known_fields = {
        "enabled",
        "max_listing_pages",
        "max_detail_fetches",
        "detail_workers",
        "query_workers",
        "search_queries",
        "locations",
        "inherit_search_matrix_from",
        "results_per_input",
        "options",
    }
    values = {key: value for key, value in raw_source.items() if key in known_fields}
    explicit_options = values.pop("options", {})
    if explicit_options and not isinstance(explicit_options, dict):
        raise ValueError(f"sources.{source_name}.options must be a TOML table")
    unknown_keys = [key for key in raw_source if key not in known_fields]
    # Unknown top-level keys are the documented extension point for
    # adapter-specific settings (docs/public/configuration.md) and are
    # preserved in SourceConfig.options unchanged. Only flag a key close
    # enough to a known field name to plausibly be a typo of it (e.g.
    # "max_detial_fetches"), so a mistyped known field surfaces as an error
    # instead of silently taking SourceConfig's default value.
    for key in unknown_keys:
        suggestion = get_close_matches(key, known_fields, n=1, cutoff=0.8)
        if suggestion:
            raise ValueError(
                f"sources.{source_name}.{key} is not a known field; did you mean {suggestion[0]!r}?"
            )
    options = dict(explicit_options)
    options.update({key: raw_source[key] for key in unknown_keys})
    return SourceConfig(**values, options=options)


def environment_credential(name: str) -> str:
    value = os.getenv(name, "").strip()
    return "" if "YOUR_ACTUAL_" in value.upper() else value


def inherit_search_matrix(
    source_name: str,
    source_config: SourceConfig,
    available_sources: dict[str, SourceConfig],
) -> None:
    inherited_name = source_config.inherit_search_matrix_from.strip().lower()
    if not inherited_name:
        return
    if inherited_name == source_name:
        raise ValueError(f"sources.{source_name} cannot inherit its search matrix from itself")
    inherited = available_sources.get(inherited_name)
    if inherited is None:
        raise ValueError(
            f"sources.{source_name} references unknown source matrix {inherited_name!r}"
        )
    if not source_config.search_queries:
        source_config.search_queries = list(inherited.search_queries)
    if not source_config.locations:
        source_config.locations = list(inherited.locations)


def validate_source_config(source_name: str, source_config: SourceConfig) -> None:
    if not source_config.enabled:
        return
    if source_config.max_listing_pages < 1:
        raise ValueError(
            f"sources.{source_name}.max_listing_pages must be greater than 0 when enabled"
        )
    if source_config.max_detail_fetches < 1:
        raise ValueError(
            f"sources.{source_name}.max_detail_fetches must be greater than 0 when enabled"
        )
    if not source_config.search_queries:
        raise ValueError(f"sources.{source_name}.search_queries must not be empty when enabled")
    if not source_config.locations:
        raise ValueError(f"sources.{source_name}.locations must not be empty when enabled")
    if source_config.results_per_input < 1:
        raise ValueError(f"sources.{source_name}.results_per_input must be greater than 0")
    if source_config.query_workers < 1:
        raise ValueError(f"sources.{source_name}.query_workers must be greater than 0")


def load_target_rules(filters: dict) -> list[TargetRuleConfig]:
    rules: list[TargetRuleConfig] = []
    for raw_rule in filters.get("target_rules", []):
        if not isinstance(raw_rule, dict):
            continue
        keywords = [
            str(value).strip().lower()
            for value in raw_rule.get("keywords", [])
            if str(value).strip()
        ]
        keyword_groups = load_keyword_groups(raw_rule)
        if not keywords and not keyword_groups:
            continue
        rules.append(
            TargetRuleConfig(
                name=str(raw_rule.get("name", "")).strip(),
                keywords=keywords,
                match_scope=str(
                    raw_rule.get("match_scope", filters.get("target_match_scope", "title"))
                )
                .strip()
                .lower()
                or "title",
                keyword_groups=keyword_groups,
                minimum_keyword_matches=max(
                    1,
                    int(raw_rule.get("minimum_keyword_matches", 1)),
                ),
            )
        )
    return rules


def load_keyword_groups(raw_rule: dict) -> list[list[str]]:
    groups: list[list[str]] = []
    for raw_group in raw_rule.get("keyword_groups", []):
        if not isinstance(raw_group, list):
            continue
        group = [str(value).strip().lower() for value in raw_group if str(value).strip()]
        if group:
            groups.append(group)
    return groups
