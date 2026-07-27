from __future__ import annotations

from dataclasses import dataclass

from job_scraper.application.application_runtime import ApplicationRuntime
from job_scraper.browser.chrome_cdp import PageInspection, inspect_application_page
from job_scraper.ports.repositories import ApplicationJobReader


class ApplicationInspectionError(RuntimeError):
    """Raised when an accepted job cannot be inspected safely."""


@dataclass(frozen=True, slots=True)
class ApplicationInspectionReport:
    canonical_job_id: str
    title: str
    company_name: str
    inspection: PageInspection


def inspect_accepted_job(
    reader: ApplicationJobReader,
    runtime: ApplicationRuntime,
    canonical_job_id: str,
    *,
    follow_apply: bool = False,
) -> ApplicationInspectionReport:
    job = reader.get_accepted_application_job(canonical_job_id)
    if job is None:
        raise ApplicationInspectionError("Job is not an accepted job with a usable description")
    if job.status in {"submitted", "submitted_confirmed", "submission_unknown", "applied"}:
        raise ApplicationInspectionError(
            f"Job cannot be inspected for application: status={job.status}"
        )
    inspection = inspect_application_page(
        runtime,
        job.application_url or job.source_url,
        follow_apply=follow_apply,
    )
    return ApplicationInspectionReport(
        canonical_job_id=job.canonical_job_id,
        title=job.title,
        company_name=job.company_name,
        inspection=inspection,
    )
