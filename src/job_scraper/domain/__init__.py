"""Core business types with no infrastructure dependencies."""

from job_scraper.domain.context import EvaluationContext
from job_scraper.domain.decisions import Decision, RejectionReason
from job_scraper.domain.locations import merge_locations
from job_scraper.domain.models import (
    CanonicalJob,
    JobHistorySnapshot,
    JobRecord,
    ProfileMatch,
    RawJobRecord,
    RunStats,
    SearchWindow,
    SourceObservation,
    SourcePosting,
)
from job_scraper.domain.policies import FilterPolicy, FreshnessPolicy, TargetRule

__all__ = [
    "CanonicalJob",
    "Decision",
    "EvaluationContext",
    "FilterPolicy",
    "FreshnessPolicy",
    "JobHistorySnapshot",
    "JobRecord",
    "ProfileMatch",
    "RawJobRecord",
    "RejectionReason",
    "RunStats",
    "SearchWindow",
    "SourceObservation",
    "SourcePosting",
    "TargetRule",
    "merge_locations",
]
