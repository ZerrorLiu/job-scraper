from __future__ import annotations

from typing import Protocol

from job_scraper.domain.context import EvaluationContext
from job_scraper.domain.decisions import Decision
from job_scraper.domain.models import JobRecord, RawJobRecord
from job_scraper.domain.policies import FilterPolicy


class PipelineStep(Protocol):
    name: str

    def evaluate(self, job: JobRecord, context: EvaluationContext) -> Decision: ...


class CandidateEvaluator(Protocol):
    def evaluate(self, job: JobRecord, context: EvaluationContext) -> Decision: ...


class JobNormalizer(Protocol):
    def __call__(self, raw: RawJobRecord, policy: FilterPolicy) -> JobRecord: ...
