from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class ExternalApplicationStatus:
    external_id: str
    status: str
    title: str = ""
    company_name: str = ""
    canonical_url: str = ""


class ApplicationStatusGateway(Protocol):
    gateway_id: str

    def pull(self) -> Sequence[ExternalApplicationStatus]: ...
