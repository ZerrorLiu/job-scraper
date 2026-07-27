from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit


def resolve_external_application_url(source_url: str, candidate_url: object) -> str:
    """Return a safe, explicit external application URL or an empty string."""

    candidate = str(candidate_url or "").strip()
    if not _is_public_https_url(candidate) or _same_destination(source_url, candidate):
        return ""
    return candidate


def _is_public_https_url(value: str) -> bool:
    parsed = urlsplit(value)
    hostname = (parsed.hostname or "").casefold()
    if parsed.scheme.casefold() != "https" or not hostname:
        return False
    if parsed.username or parsed.password:
        return False
    if hostname == "localhost" or hostname.endswith(".local"):
        return False
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        return True
    return address.is_global and not address.is_multicast


def _same_destination(left: str, right: str) -> bool:
    first = urlsplit(left.strip())
    second = urlsplit(right.strip())
    return (
        first.scheme.casefold(),
        first.netloc.casefold(),
        first.path.rstrip("/") or "/",
    ) == (
        second.scheme.casefold(),
        second.netloc.casefold(),
        second.path.rstrip("/") or "/",
    )
