from __future__ import annotations

from dataclasses import dataclass, field

from job_scraper.domain.identity import canonical_identity
from job_scraper.domain.locations import merge_locations
from job_scraper.domain.models import JobRecord


@dataclass(slots=True)
class AcceptedJob:
    job: JobRecord
    job_id: str
    linked_job_ids: list[str] = field(default_factory=list)


def merge_accepted_job(
    accepted_jobs: dict[str, AcceptedJob],
    job: JobRecord,
    job_id: str,
) -> bool:
    identity = canonical_identity(job)
    existing_entry = accepted_jobs.get(identity)
    if existing_entry is None:
        add_source_provenance(job, job)
        accepted_jobs[identity] = AcceptedJob(
            job=job,
            job_id=job_id,
            linked_job_ids=[job_id],
        )
        return False

    existing_entry.linked_job_ids = unique_list([*accepted_database_ids(existing_entry), job_id])
    existing = existing_entry.job
    merged_location, merged_city, merged_options = merge_locations(
        existing.location_raw,
        job.location_raw,
        existing.city,
        job.city,
    )
    existing.location_raw = merged_location or existing.location_raw or job.location_raw
    existing.city = merged_city or existing.city or job.city

    existing_queries = normalize_list(existing.raw_payload.get("queries", []))
    incoming_query = str(job.raw_payload.get("query", "")).strip()
    if existing.raw_payload.get("query"):
        existing_queries.append(str(existing.raw_payload["query"]).strip())
    if incoming_query:
        existing_queries.append(incoming_query)
    merged_queries = unique_list(existing_queries)
    if merged_queries:
        existing.raw_payload["queries"] = merged_queries
    if merged_options:
        existing.raw_payload["location_options"] = merged_options

    add_source_provenance(existing, job)
    if len(job.job_description) > len(existing.job_description):
        existing.job_description = job.job_description
    if existing.description_language in {"Unknown", "N/A", ""} and job.description_language:
        existing.description_language = job.description_language
    return True


def accepted_database_ids(accepted: AcceptedJob) -> list[str]:
    return unique_list([accepted.job_id, *accepted.linked_job_ids])


def add_source_provenance(target: JobRecord, incoming: JobRecord) -> None:
    platforms = normalize_list(target.raw_payload.get("source_platforms", []))
    platforms.extend([target.source, incoming.source])
    target.raw_payload["source_platforms"] = unique_list(platforms)

    acquisition_sources = normalize_list(target.raw_payload.get("acquisition_sources", []))
    acquisition_sources.extend([target.source, incoming.source])
    target.raw_payload["acquisition_sources"] = unique_list(acquisition_sources)

    urls = normalize_list(target.raw_payload.get("source_urls", []))
    urls.extend([target.source_url, incoming.source_url])
    target.raw_payload["source_urls"] = unique_list(urls)


def normalize_list(values: object) -> list[str]:
    if isinstance(values, list):
        return [str(value).strip() for value in values if str(value).strip()]
    if isinstance(values, tuple):
        return [str(value).strip() for value in values if str(value).strip()]
    return []


def unique_list(values: list[str]) -> list[str]:
    unique: list[str] = []
    seen: set[str] = set()
    for value in values:
        key = value.casefold()
        if key in seen:
            continue
        seen.add(key)
        unique.append(value)
    return unique
