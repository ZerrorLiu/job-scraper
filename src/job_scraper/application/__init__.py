"""Use cases that orchestrate domain policies through ports."""

from job_scraper.application.aggregation import AcceptedJob
from job_scraper.application.process_candidate import (
    CandidateProcessingContext,
    CandidateProcessingResult,
    ProcessJobCandidate,
)

__all__ = [
    "AcceptedJob",
    "CandidateProcessingContext",
    "CandidateProcessingResult",
    "ProcessJobCandidate",
]
