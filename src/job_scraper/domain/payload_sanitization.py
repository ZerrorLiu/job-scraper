from __future__ import annotations

from collections.abc import Mapping
from typing import Any

COMPANY_PAYLOAD_CONTAINERS = {"company", "employer", "hiringorganization", "organization"}
COMPANY_URL_PAYLOAD_KEYS = {
    "companyurl",
    "companywebsite",
    "employerurl",
    "employerwebsite",
    "organizationurl",
    "sameas",
    "url",
    "website",
    "websiteurl",
}


def sanitize_job_payload(
    value: Mapping[str, Any], *, inside_company: bool = False
) -> dict[str, Any]:
    """Remove company and application destinations while preserving job metadata."""
    cleaned: dict[str, Any] = {}
    for key, child in value.items():
        normalized_key = "".join(character for character in str(key).lower() if character.isalnum())
        if "application" in normalized_key or "apply" in normalized_key:
            continue
        if normalized_key in COMPANY_URL_PAYLOAD_KEYS and (
            inside_company or normalized_key != "url"
        ):
            continue
        cleaned[str(key)] = _sanitize_value(
            child,
            inside_company=inside_company or normalized_key in COMPANY_PAYLOAD_CONTAINERS,
        )
    return cleaned


def _sanitize_value(value: object, *, inside_company: bool) -> object:
    if isinstance(value, Mapping):
        return sanitize_job_payload(value, inside_company=inside_company)
    if isinstance(value, list):
        return [_sanitize_value(child, inside_company=inside_company) for child in value]
    if isinstance(value, tuple):
        return tuple(_sanitize_value(child, inside_company=inside_company) for child in value)
    return value
