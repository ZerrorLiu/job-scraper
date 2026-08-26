from __future__ import annotations

import tomllib
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


class SearchProfile(Protocol):
    """The slice of a profile the planner needs.

    Structural rather than concrete so the application layer does not have to
    import the configuration layer that happens to load these values today.
    """

    @property
    def profile_id(self) -> str: ...

    @property
    def enabled(self) -> bool: ...

    @property
    def base_queries(self) -> tuple[str, ...]: ...

    @property
    def early_career_modifiers(self) -> tuple[str, ...]: ...

    @property
    def watchlists(self) -> tuple[str, ...]: ...


@dataclass(frozen=True, slots=True)
class WatchlistDefinition:
    watchlist_id: str
    label: str
    queries: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SearchIntent:
    query: str
    profile_ids: tuple[str, ...]
    watchlist_id: str = ""


@dataclass(frozen=True, slots=True)
class SearchPlan:
    intents: tuple[SearchIntent, ...]

    @property
    def queries(self) -> tuple[str, ...]:
        return tuple(intent.query for intent in self.intents)

    def for_profile(self, profile_id: str) -> tuple[SearchIntent, ...]:
        return tuple(intent for intent in self.intents if profile_id in intent.profile_ids)


@dataclass(slots=True)
class _MutableIntent:
    query: str
    profile_ids: list[str]
    watchlist_id: str = ""


def build_search_plan(
    profiles: Sequence[SearchProfile],
    watchlists: dict[str, WatchlistDefinition] | None = None,
    *,
    include_disabled: bool = False,
) -> SearchPlan:
    watchlists = watchlists or {}
    merged: dict[str, _MutableIntent] = {}
    for profile in profiles:
        if not profile.enabled and not include_disabled:
            continue
        for query in expand_profile_queries(profile):
            _merge_intent(merged, query, profile.profile_id)
        for watchlist_id in profile.watchlists:
            try:
                watchlist = watchlists[watchlist_id]
            except KeyError as exc:
                raise ValueError(
                    f"Profile {profile.profile_id!r} references unknown watchlist {watchlist_id!r}"
                ) from exc
            for query in watchlist.queries:
                _merge_intent(
                    merged,
                    query,
                    profile.profile_id,
                    watchlist_id=watchlist_id,
                )
    return SearchPlan(
        intents=tuple(
            SearchIntent(
                query=value.query,
                profile_ids=tuple(value.profile_ids),
                watchlist_id=value.watchlist_id,
            )
            for value in merged.values()
        )
    )


def expand_profile_queries(profile: SearchProfile) -> tuple[str, ...]:
    queries: list[str] = []
    for base_query in profile.base_queries:
        queries.append(base_query)
        for modifier in profile.early_career_modifiers:
            queries.append(f"{modifier} {base_query}")
    return _unique_display_values(queries)


def load_watchlists(config_root: Path) -> dict[str, WatchlistDefinition]:
    path = config_root / "watchlists.toml"
    if not path.exists():
        return {}
    with path.open("rb") as handle:
        raw = tomllib.load(handle)
    section = raw.get("watchlists", {})
    if not isinstance(section, dict):
        raise ValueError(f"{path}: [watchlists] must be a TOML table")
    result: dict[str, WatchlistDefinition] = {}
    for watchlist_id, value in section.items():
        if not isinstance(value, dict):
            raise ValueError(f"{path}: [watchlists.{watchlist_id}] must be a TOML table")
        normalized_id = _normalized_id(watchlist_id)
        companies = _string_values(value.get("companies", []), "companies")
        queries = _string_values(value.get("queries", []), "queries")
        if companies and queries:
            raise ValueError(
                f"{path}: watchlist {normalized_id!r} must use companies or queries, not both"
            )
        rendered_queries = tuple(f'"{company}"' for company in companies) if companies else queries
        if not rendered_queries:
            raise ValueError(f"{path}: watchlist {normalized_id!r} has no queries")
        result[normalized_id] = WatchlistDefinition(
            watchlist_id=normalized_id,
            label=str(value.get("label", watchlist_id)).strip() or watchlist_id,
            queries=_unique_display_values(rendered_queries),
        )
    return result


def _merge_intent(
    merged: dict[str, _MutableIntent],
    query: str,
    profile_id: str,
    *,
    watchlist_id: str = "",
) -> None:
    cleaned = " ".join(query.split()).strip()
    if not cleaned:
        return
    key = cleaned.casefold()
    entry = merged.setdefault(
        key,
        _MutableIntent(
            query=cleaned,
            profile_ids=[],
            watchlist_id=watchlist_id,
        ),
    )
    if profile_id not in entry.profile_ids:
        entry.profile_ids.append(profile_id)
    if watchlist_id and not entry.watchlist_id:
        entry.watchlist_id = watchlist_id


def _string_values(value: object, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a TOML array")
    return tuple(str(item).strip() for item in value if str(item).strip())


def _unique_display_values(values: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    unique: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = " ".join(value.split()).strip()
        key = cleaned.casefold()
        if not cleaned or key in seen:
            continue
        seen.add(key)
        unique.append(cleaned)
    return tuple(unique)


def _normalized_id(value: str) -> str:
    return str(value).strip().lower().replace("-", "_")
