from __future__ import annotations

import hashlib
import re
import unicodedata

from job_scraper.domain.models import JobRecord


def canonical_identity(job: JobRecord) -> str:
    title = _normalized_title(job.title, job.location_raw)
    company = _normalized(job.company_name)
    # location_raw carries the full location list (e.g. "Berlin | Munich");
    # city collapses to the lossy literal "Multiple locations" for display
    # when there are 2+ options, which would make distinct multi-city
    # postings hash to the same identity if used here instead.
    location = _normalized(job.location_raw or job.city or job.country)
    if not any((title, company, location)):
        return job.dedupe_key
    return "|".join((title, company, location))


def canonical_job_id(job: JobRecord) -> str:
    return _stable_id("job", canonical_identity(job))


def source_posting_id(job: JobRecord) -> str:
    source_reference = job.source_job_id or job.canonical_url or job.source_url or job.dedupe_key
    return _stable_id("posting", job.source, source_reference)


def stable_id(namespace: str, *parts: str) -> str:
    return _stable_id(namespace, *parts)


def _stable_id(namespace: str, *parts: str) -> str:
    payload = "\x1f".join((namespace, *(_normalized(part) for part in parts)))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _normalized(value: str) -> str:
    return " ".join((value or "").casefold().split())


def _normalized_title(value: str, location: str) -> str:
    title = " ".join((value or "").split())
    match = re.match(r"^(?P<base>.+?)\s+in\s+(?P<tail>.+)$", title, flags=re.IGNORECASE)
    if match:
        base = match.group("base").strip()
        tail = _location_key(match.group("tail"))
        location_key = _location_key(location.split(",")[0])
        has_gender_marker = re.search(
            r"\((?:m/w/d|w/m/d|f/m/d|all genders|gn)\)",
            base,
            flags=re.IGNORECASE,
        )
        if (
            location_key and (tail.startswith(location_key) or location_key.startswith(tail))
        ) or has_gender_marker:
            title = base
    title = re.sub(
        r"\((?:m/w/d|w/m/d|f/m/d|all genders|gn)\)",
        "",
        title,
        flags=re.IGNORECASE,
    )
    title = re.sub(
        r"\b(senior|junior|lead|principal)\b",
        "",
        title,
        flags=re.IGNORECASE,
    )
    return _normalized(title)


def _location_key(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_value = "".join(char for char in normalized if not unicodedata.combining(char))
    return _normalized(re.sub(r"[^a-z0-9]+", " ", ascii_value.casefold()))
