from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol

from job_scraper.domain.models import RawJobRecord


class JobChannel(Protocol):
    channel_id: str

    def validate_runtime(self) -> None: ...

    def read(self) -> Iterable[RawJobRecord]: ...
