from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol

from job_scraper.domain.models import RawJobRecord, SearchWindow


@dataclass(frozen=True, slots=True)
class SourceCapabilities:
    acquisition_mode: str
    platform: str
    supports_pagination: bool = True
    supports_upstream_freshness: bool = False
    requires_credentials: bool = False
    is_metered: bool = False


class JobSource(Protocol):
    source_name: str
    capabilities: SourceCapabilities

    def validate_runtime(self) -> None: ...

    def collect(self, window: SearchWindow) -> Iterable[RawJobRecord]: ...
