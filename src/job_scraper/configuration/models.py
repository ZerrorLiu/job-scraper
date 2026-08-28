from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ProfileDefinition:
    profile_id: str
    label: str
    runtime_config: Path
    enabled: bool
    sources: tuple[str, ...]
    channels: tuple[str, ...]
    pipeline: tuple[str, ...]
    sinks: tuple[str, ...]
    base_queries: tuple[str, ...]
    locations: tuple[str, ...]
    early_career_modifiers: tuple[str, ...]
    watchlists: tuple[str, ...]
    processing_mode: str = "core"
