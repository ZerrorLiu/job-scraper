"""Shared schema.org JobPosting (JSON-LD) extraction.

Both the LinkedIn collector and the recommendation-email detail fetcher parse
the same `<script type="application/ld+json">` JobPosting payload. They each
carried a private near-copy of this logic, which had already drifted apart in
how `addressCountry` was resolved. One implementation keeps them honest.
"""

from __future__ import annotations

import json
import re
from html import unescape

_JSON_LD_SCRIPT = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.DOTALL | re.IGNORECASE,
)


def extract_jobposting(html: str) -> dict | None:
    """Return the first JobPosting object in the document, if there is one."""
    for payload in _JSON_LD_SCRIPT.findall(html):
        try:
            data = json.loads(unescape(payload))
        except json.JSONDecodeError:
            continue
        found = find_jobposting_payload(data)
        if found is not None:
            return found
    return None


def find_jobposting_payload(value: object) -> dict | None:
    """Locate a JobPosting anywhere inside a JSON-LD document or @graph."""
    if isinstance(value, dict):
        node_type = value.get("@type")
        types = node_type if isinstance(node_type, list) else [node_type]
        if any(str(item).casefold() == "jobposting" for item in types if item):
            return value
        for child in value.values():
            found = find_jobposting_payload(child)
            if found is not None:
                return found
        return None
    if isinstance(value, list):
        for child in value:
            found = find_jobposting_payload(child)
            if found is not None:
                return found
    return None


def iter_places(value: object) -> list[dict]:
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return []


def extract_country(payload: dict) -> str:
    """Prefer a concrete job location, then a remote-applicant restriction."""
    for source in (payload.get("jobLocation"), payload.get("applicantLocationRequirements")):
        for entry in iter_places(source):
            address = entry.get("address")
            country = str((address or {}).get("addressCountry") or "").strip()
            if country:
                return country
    return ""


def extract_city(payload: dict) -> str:
    for entry in iter_places(payload.get("jobLocation")):
        address = entry.get("address")
        city = str((address or {}).get("addressLocality") or "").strip()
        if city:
            return city
    return ""


def format_place(place: dict) -> str:
    address = place.get("address") if isinstance(place, dict) else {}
    if not isinstance(address, dict):
        return ""
    city = str(address.get("addressLocality") or "").strip()
    region = str(address.get("addressRegion") or "").strip()
    country = country_display_name(str(address.get("addressCountry") or "").strip())
    return ", ".join(part for part in [city, region, country] if part)


def extract_job_locations(payload: dict) -> list[str]:
    options = [
        text for entry in iter_places(payload.get("jobLocation")) if (text := format_place(entry))
    ]
    if not options:
        parts = [
            part
            for part in [extract_city(payload), country_display_name(extract_country(payload))]
            if part
        ]
        if parts:
            options.append(", ".join(parts))
    unique: list[str] = []
    seen: set[str] = set()
    for value in options:
        key = value.casefold()
        if key not in seen:
            seen.add(key)
            unique.append(value)
    return unique


def country_display_name(value: str) -> str:
    """Expand an ISO alpha-2 code into the name used in location strings."""
    from job_scraper.domain.countries import country_display_name as _display

    return _display(value)
