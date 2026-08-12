from __future__ import annotations

import os
import re
import tomllib
from pathlib import Path

from job_scraper.configuration.models import ProfileDefinition

PROFILE_ID_PATTERN = re.compile(r"[a-z][a-z0-9_]*")
PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CONFIG_ROOT = PROJECT_ROOT / "config"
ALLOWED_PROFILE_FIELDS = {
    "id",
    "label",
    "runtime_config",
    "enabled",
    "sources",
    "channels",
    "pipeline",
    "sinks",
    "base_queries",
    "locations",
    "early_career_modifiers",
    "watchlists",
}


def get_config_root(config_root: Path | None = None) -> Path:
    if config_root is not None:
        return config_root.expanduser().resolve()
    configured = os.getenv("JOB_SCRAPER_CONFIG_DIR", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return DEFAULT_CONFIG_ROOT


def available_profiles(config_root: Path | None = None) -> tuple[str, ...]:
    config_root = get_config_root(config_root)
    profiles_dir = config_root / "profiles"
    if not profiles_dir.exists():
        return ()
    return tuple(sorted(path.stem for path in profiles_dir.glob("*.toml")))


def load_profile_definition(
    profile_id: str,
    *,
    config_root: Path | None = None,
) -> ProfileDefinition:
    config_root = get_config_root(config_root)
    normalized_id = profile_id.strip().lower().replace("-", "_")
    if not PROFILE_ID_PATTERN.fullmatch(normalized_id):
        # Reject before it is used to build a filesystem path: without this,
        # a value like "../../pyproject" would resolve outside
        # config_root/profiles.
        raise ValueError(
            "profile_id must start with a letter and use letters, digits, or underscores"
        )
    profile_path = config_root / "profiles" / f"{normalized_id}.toml"
    if not profile_path.exists():
        available = ", ".join(available_profiles(config_root)) or "(none)"
        raise ValueError(f"Unknown profile {normalized_id!r}; available profiles: {available}")

    defaults = _read_section(config_root / "defaults.toml", "defaults")
    profile = _read_section(profile_path, "profile")
    local = _read_local_override(config_root / "local.toml", normalized_id)
    merged = {**defaults, **profile, **local}
    unknown = sorted(set(merged) - ALLOWED_PROFILE_FIELDS)
    if unknown:
        raise ValueError(f"Unknown profile fields for {normalized_id!r}: {', '.join(unknown)}")

    declared_id = str(merged.get("id", normalized_id)).strip().lower()
    if declared_id != normalized_id:
        raise ValueError(f"Profile id {declared_id!r} does not match filename {normalized_id!r}")

    runtime_value = str(merged.get("runtime_config", "")).strip()
    if not runtime_value:
        raise ValueError(f"Profile {normalized_id!r} must declare runtime_config")
    runtime_config = (profile_path.parent / runtime_value).resolve()
    if not runtime_config.is_file():
        raise ValueError(f"Profile {normalized_id!r} references missing config: {runtime_config}")

    return ProfileDefinition(
        profile_id=normalized_id,
        label=str(merged.get("label", normalized_id)).strip() or normalized_id,
        runtime_config=runtime_config,
        enabled=_boolean_value(merged.get("enabled", True), "enabled"),
        sources=_string_tuple(merged.get("sources", []), "sources"),
        channels=_string_tuple(merged.get("channels", []), "channels"),
        pipeline=_string_tuple(merged.get("pipeline", []), "pipeline"),
        sinks=_string_tuple(merged.get("sinks", []), "sinks"),
        base_queries=_display_string_tuple(merged.get("base_queries", []), "base_queries"),
        locations=_display_string_tuple(merged.get("locations", []), "locations"),
        early_career_modifiers=_display_string_tuple(
            merged.get("early_career_modifiers", []),
            "early_career_modifiers",
        ),
        watchlists=_string_tuple(merged.get("watchlists", []), "watchlists"),
    )


def find_profile_definition(
    runtime_config: str | Path,
    *,
    config_root: Path | None = None,
) -> ProfileDefinition | None:
    config_root = get_config_root(config_root)
    target = Path(runtime_config).resolve()
    for profile_id in available_profiles(config_root):
        profile = load_profile_definition(profile_id, config_root=config_root)
        if profile.runtime_config == target:
            return profile
    return None


def _read_section(path: Path, section: str) -> dict[str, object]:
    if not path.exists():
        return {}
    with path.open("rb") as handle:
        payload = tomllib.load(handle)
    value = payload.get(section, {})
    if not isinstance(value, dict):
        raise ValueError(f"{path}: [{section}] must be a TOML table")
    return dict(value)


def _read_local_override(path: Path, profile_id: str) -> dict[str, object]:
    if not path.exists():
        return {}
    with path.open("rb") as handle:
        payload = tomllib.load(handle)
    profiles = payload.get("profiles", {})
    if not isinstance(profiles, dict):
        raise ValueError(f"{path}: [profiles] must be a TOML table")
    value = profiles.get(profile_id, {})
    if not isinstance(value, dict):
        raise ValueError(f"{path}: [profiles.{profile_id}] must be a TOML table")
    return dict(value)


def _string_tuple(value: object, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a TOML array")
    normalized = tuple(
        str(item).strip().lower().replace("-", "_") for item in value if str(item).strip()
    )
    if len(normalized) != len(set(normalized)):
        raise ValueError(f"{field_name} contains duplicate component ids")
    return normalized


def _display_string_tuple(value: object, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a TOML array")
    normalized = tuple(str(item).strip() for item in value if str(item).strip())
    keys = [item.casefold() for item in normalized]
    if len(keys) != len(set(keys)):
        raise ValueError(f"{field_name} contains duplicate values")
    return normalized


def _boolean_value(value: object, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{field_name} must be a boolean")
    return value
