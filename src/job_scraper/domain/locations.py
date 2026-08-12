from __future__ import annotations

import re


def merge_locations(*values: str) -> tuple[str, str, list[str]]:
    """Combine normalized location strings without leaking adapter concerns."""

    options: list[str] = []
    for value in values:
        options.extend(_split_location_options(value))
    unique: list[str] = []
    seen: set[str] = set()
    for option in options:
        key = option.casefold()
        if key in seen:
            continue
        seen.add(key)
        unique.append(option)
    if not unique:
        return "", "", []
    if len(unique) == 1:
        return unique[0], unique[0], unique
    ordered = sorted(unique, key=str.casefold)
    return " | ".join(ordered), "Multiple locations", unique


def _split_location_options(value: str) -> list[str]:
    if not value:
        return []
    options: list[str] = []
    for part in re.split(r"\s*(?:\||/|;)\s*", value):
        first = part.split(",")[0]
        first = re.sub(r"\b(germany|deutschland)\b", "", first, flags=re.IGNORECASE)
        city = " ".join(first.split()).strip()
        if city and city.casefold() != "multiple locations":
            options.append(city)
    return options
