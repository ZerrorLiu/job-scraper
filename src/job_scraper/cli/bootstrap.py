from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

# See pipeline.steps.DEFAULT_STEPS for why this order is what it is.
DEFAULT_PIPELINE = (
    "country",
    "freshness",
    "company",
    "employment_scope",
    "excluded_terms",
    "role",
    "requirement_exclusion",
    "language",
)


@dataclass(frozen=True, slots=True)
class BootstrapRequest:
    config_root: Path
    profile_id: str
    label: str
    queries: tuple[str, ...]
    locations: tuple[str, ...]
    countries: tuple[str, ...]
    keywords: tuple[str, ...]
    sources: tuple[str, ...]
    channels: tuple[str, ...] = ()
    pipeline: tuple[str, ...] = DEFAULT_PIPELINE
    sinks: tuple[str, ...] = ("csv",)
    enable_email: bool = False
    timezone: str = "UTC"
    imap_host: str = ""
    processing_mode: str = "core"


@dataclass(frozen=True, slots=True)
class BootstrapResult:
    profile_path: Path
    runtime_path: Path
    email_path: Path | None = None


def initialize_profile(request: BootstrapRequest) -> BootstrapResult:
    from job_scraper.registry.builtins import create_builtin_registry

    registry = create_builtin_registry()
    profile_id = _profile_id(request.profile_id)
    label = _required_text(request.label, "label")
    queries = _required_values(request.queries, "queries")
    locations = _required_values(request.locations, "locations")
    countries = _required_values(request.countries, "countries")
    keywords = _required_values(request.keywords, "keywords")
    sources = _component_values(request.sources, registry.sources.available(), "sources")
    channels = _optional_component_values(
        request.channels,
        registry.channels.available(),
        "channels",
    )
    pipeline = _component_values(
        request.pipeline,
        registry.steps.available(),
        "pipeline",
    )
    sinks = _component_values(request.sinks, registry.sinks.available(), "sinks")
    processing_mode = request.processing_mode.strip().lower()
    if processing_mode not in {"core", "review", "discovery"}:
        raise ValueError("processing_mode must be core, review, or discovery")
    if request.enable_email and "email_imap" not in channels:
        channels = (*channels, "email_imap")

    root = request.config_root.expanduser().resolve()
    profiles_dir = root / "profiles"
    profile_path = profiles_dir / f"{profile_id}.toml"
    runtime_path = root / f"{profile_id}.toml"
    email_path = root / "email.toml" if "email_imap" in channels else None
    targets = [profile_path, runtime_path]
    if email_path is not None and not email_path.exists():
        targets.append(email_path)
    existing = [path for path in targets if path.exists()]
    if existing:
        rendered = ", ".join(str(path) for path in existing)
        raise FileExistsError(f"Refusing to overwrite existing private configuration: {rendered}")

    profiles_dir.mkdir(parents=True, exist_ok=True)
    profile_path.write_text(
        _profile_toml(
            profile_id=profile_id,
            label=label,
            runtime_filename=runtime_path.name,
            queries=queries,
            locations=locations,
            sources=sources,
            channels=channels,
            pipeline=pipeline,
            sinks=sinks,
            processing_mode=processing_mode,
        ),
        encoding="utf-8",
    )
    runtime_path.write_text(
        _runtime_toml(
            profile_id=profile_id,
            label=label,
            countries=countries,
            keywords=keywords,
            sources=sources,
            sinks=sinks,
            timezone=_required_text(request.timezone, "timezone"),
        ),
        encoding="utf-8",
    )
    if email_path is not None and not email_path.exists():
        email_path.write_text(
            _email_toml(request.imap_host),
            encoding="utf-8",
        )
    return BootstrapResult(
        profile_path=profile_path,
        runtime_path=runtime_path,
        email_path=email_path,
    )


def _profile_toml(
    *,
    profile_id: str,
    label: str,
    runtime_filename: str,
    queries: tuple[str, ...],
    locations: tuple[str, ...],
    sources: tuple[str, ...],
    channels: tuple[str, ...],
    pipeline: tuple[str, ...],
    sinks: tuple[str, ...],
    processing_mode: str,
) -> str:
    return (
        "[profile]\n"
        f"id = {_toml_string(profile_id)}\n"
        f"label = {_toml_string(label)}\n"
        f"runtime_config = {_toml_string(f'../{runtime_filename}')}\n"
        "enabled = true\n"
        f"processing_mode = {_toml_string(processing_mode)}\n"
        f"sources = {_toml_array(sources)}\n"
        f"channels = {_toml_array(channels)}\n"
        f"pipeline = {_toml_array(pipeline)}\n"
        f"sinks = {_toml_array(sinks)}\n"
        f"base_queries = {_toml_array(queries)}\n"
        f"locations = {_toml_array(locations)}\n"
        "early_career_modifiers = []\n"
        "watchlists = []\n"
    )


def _runtime_toml(
    *,
    profile_id: str,
    label: str,
    countries: tuple[str, ...],
    keywords: tuple[str, ...],
    sources: tuple[str, ...],
    sinks: tuple[str, ...],
    timezone: str,
) -> str:
    notion_enabled = "notion_daily" in sinks
    common = (
        "[project]\n"
        f"timezone = {_toml_string(timezone)}\n"
        f'database_path = "data/{profile_id}.db"\n'
        'workspace_database_path = "data/workspace.db"\n'
        'export_dir = "exports"\n'
        f"track_label = {_toml_string(label)}\n"
        f"export_filename_prefix = {_toml_string(f'jobs_{profile_id}')}\n"
        "overlap_hours = 48\n"
        "recent_post_age_hours = 24\n"
        "bootstrap_post_age_hours = 24\n\n"
        "[filters]\n"
        f"country = {_toml_string(','.join(countries))}\n"
        f"include_keywords = {_toml_array(keywords)}\n"
        "exclude_keywords = []\n"
        f"target_keywords = {_toml_array(keywords)}\n"
        'target_match_scope = "combined"\n'
        "target_rules = []\n"
        "company_names = []\n"
        "full_time_only = false\n"
        "allow_part_time = true\n"
        "allow_temporary = true\n"
        "excluded_requirement_patterns = []\n"
        "require_english = false\n"
        "allowed_description_languages = []\n"
        "minimum_english_ratio = 0.0\n\n"
        "[http]\n"
        'user_agent = "job-scraper/0.2"\n'
        "timeout_seconds = 20\n"
        "base_delay_seconds = 1.0\n"
        "jitter_seconds = 0.5\n"
        "max_retries = 2\n\n"
    )
    notion = (
        "[notion]\n"
        f"enabled = {_toml_bool(notion_enabled)}\n"
        f"daily_table_prefix = {_toml_string(label)}\n"
        'container_title = "Job Discovery"\n'
    )
    return common + _source_toml(sources) + notion


def _source_toml(sources: tuple[str, ...]) -> str:
    sections: list[str] = []
    for source_id in sources:
        max_listing_pages = 1 if source_id == "indeed_brightdata" else 4
        query_workers = 1 if source_id == "indeed_brightdata" else 4
        sections.append(
            f"[sources.{source_id}]\n"
            "enabled = true\n"
            f"max_listing_pages = {max_listing_pages}\n"
            "max_detail_fetches = 80\n"
            "detail_workers = 4\n"
            f"query_workers = {query_workers}\n"
            "results_per_input = 10\n"
            "search_queries = []\n"
            "locations = []\n\n"
        )
    return "".join(sections)


def _email_toml(imap_host: str) -> str:
    return (
        "[mailbox]\n"
        f"host = {_toml_string(imap_host.strip())}\n"
        "port = 993\n"
        "use_ssl = true\n"
        'folder = "INBOX"\n'
        'username_env = "JOB_EMAIL_USERNAME"\n'
        'password_env = "JOB_EMAIL_APP_PASSWORD"\n'
        "lookback_days = 7\n"
        "max_messages = 50\n"
        "subject_keywords = []\n"
        "sender_allowlist = []\n"
        'state_path = "data/email_ingest_state.json"\n\n'
        "[tracks]\n"
        "config_paths = []\n"
    )


def _profile_id(value: str) -> str:
    normalized = value.strip().lower().replace("-", "_")
    if not re.fullmatch(r"[a-z][a-z0-9_]*", normalized):
        raise ValueError(
            "profile_id must start with a letter and use letters, digits, or underscores"
        )
    return normalized


def _required_text(value: str, field_name: str) -> str:
    cleaned = " ".join(value.split()).strip()
    if not cleaned:
        raise ValueError(f"{field_name} must not be empty")
    return cleaned


def _required_values(values: tuple[str, ...], field_name: str) -> tuple[str, ...]:
    cleaned = tuple(dict.fromkeys(_required_text(value, field_name) for value in values))
    if not cleaned:
        raise ValueError(f"{field_name} must contain at least one value")
    return cleaned


def _component_values(
    values: tuple[str, ...],
    supported: tuple[str, ...],
    field_name: str,
) -> tuple[str, ...]:
    cleaned = _required_values(values, field_name)
    unknown = sorted(set(cleaned) - set(supported))
    if unknown:
        raise ValueError(
            f"Unknown {field_name}: {', '.join(unknown)}; supported: {', '.join(supported)}"
        )
    return cleaned


def _optional_component_values(
    values: tuple[str, ...],
    supported: tuple[str, ...],
    field_name: str,
) -> tuple[str, ...]:
    if not values:
        return ()
    return _component_values(values, supported, field_name)


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _toml_array(values: tuple[str, ...]) -> str:
    return "[" + ", ".join(_toml_string(value) for value in values) + "]"


def _toml_bool(value: bool) -> str:
    return "true" if value else "false"
