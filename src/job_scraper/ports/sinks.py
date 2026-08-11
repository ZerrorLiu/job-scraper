from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol

from job_scraper.domain.models import JobRecord


@dataclass(frozen=True, slots=True)
class PublishContext:
    run_id: str
    profile_id: str


@dataclass(frozen=True, slots=True)
class PublishResult:
    sink_id: str
    published: int = 0
    skipped: int = 0
    errors: tuple[str, ...] = field(default_factory=tuple)


class JobSink(Protocol):
    sink_id: str

    def publish(
        self,
        jobs: Sequence[JobRecord],
        context: PublishContext,
    ) -> PublishResult: ...
