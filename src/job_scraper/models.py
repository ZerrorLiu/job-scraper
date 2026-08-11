"""Backward-compatible imports for the domain models.

New code should import from :mod:`job_scraper.domain.models`.
"""

from job_scraper.domain.models import (
    JobHistorySnapshot,
    JobRecord,
    RawJobRecord,
    RunStats,
)

__all__ = ["JobHistorySnapshot", "JobRecord", "RawJobRecord", "RunStats"]
