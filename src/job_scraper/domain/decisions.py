from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class RejectionReason(StrEnum):
    NOT_TARGET_COUNTRY = "not_target_country"
    TOO_OLD = "older_than_24h"
    COMPANY_NOT_ALLOWED = "company_not_allowed"
    NOT_FULL_TIME = "not_full_time"
    EXCLUDED_KEYWORD = "excluded_keyword"
    MISSING_TARGET_KEYWORDS = "missing_target_keywords"
    EXCLUDED_REQUIREMENT = "excluded_requirement"
    NON_ENGLISH = "non_english"
    ALREADY_PROCESSED = "already_processed"
    ALREADY_SEEN = "already_seen"


@dataclass(frozen=True, slots=True)
class Decision:
    """Result of evaluating one candidate against a profile."""

    accepted: bool
    reason: RejectionReason | None = None
    step: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def accept(cls) -> Decision:
        return cls(accepted=True)

    @classmethod
    def reject(
        cls,
        reason: RejectionReason,
        *,
        step: str,
        details: dict[str, Any] | None = None,
    ) -> Decision:
        return cls(
            accepted=False,
            reason=reason,
            step=step,
            details=details or {},
        )
