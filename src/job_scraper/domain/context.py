from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from job_scraper.domain.policies import FilterPolicy


@dataclass(frozen=True, slots=True)
class EvaluationContext:
    """Immutable inputs shared by every policy step in one profile run."""

    profile_id: str
    started_at: datetime
    policy: FilterPolicy
