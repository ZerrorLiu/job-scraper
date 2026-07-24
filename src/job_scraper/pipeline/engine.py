from __future__ import annotations

from collections.abc import Sequence

from job_scraper.domain.decisions import Decision
from job_scraper.domain.models import JobRecord
from job_scraper.pipeline.context import EvaluationContext
from job_scraper.ports.processors import PipelineStep


class CandidatePipeline:
    """Evaluate candidates through ordered, independently replaceable steps."""

    def __init__(self, steps: Sequence[PipelineStep]) -> None:
        self._steps = tuple(steps)

    @property
    def step_names(self) -> tuple[str, ...]:
        return tuple(step.name for step in self._steps)

    def evaluate(self, job: JobRecord, context: EvaluationContext) -> Decision:
        for step in self._steps:
            decision = step.evaluate(job, context)
            if not decision.accepted:
                return decision
        return Decision.accept()
