from __future__ import annotations

from pathlib import Path

from job_scraper.application.search_planner import build_search_plan, load_watchlists
from job_scraper.config import AppConfig, validate_source_config
from job_scraper.configuration.loader import get_config_root
from job_scraper.configuration.models import ProfileDefinition


def apply_profile_to_runtime(
    profile: ProfileDefinition,
    config: AppConfig,
    *,
    config_root: Path | None = None,
) -> None:
    missing_sources = sorted(set(profile.sources) - set(config.sources))
    if missing_sources:
        raise ValueError(
            "Profile references sources without runtime settings: " + ", ".join(missing_sources)
        )

    plan = build_search_plan(
        [profile],
        load_watchlists(get_config_root(config_root)),
        include_disabled=True,
    )
    queries = tuple(intent.query for intent in plan.for_profile(profile.profile_id))
    if not queries:
        raise ValueError(f"Profile {profile.profile_id!r} has no search queries")
    if not profile.locations:
        raise ValueError(f"Profile {profile.profile_id!r} has no search locations")

    for source_id, settings in config.sources.items():
        settings.enabled = source_id in profile.sources
        if not settings.enabled:
            continue
        settings.search_queries = list(queries)
        settings.locations = list(profile.locations)
        validate_source_config(source_id, settings)
