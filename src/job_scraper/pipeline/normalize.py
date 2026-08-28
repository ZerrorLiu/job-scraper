from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from datetime import UTC, datetime, timedelta
from functools import cache
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from job_scraper.domain.countries import COUNTRY_ALIASES, COUNTRY_LOCATION_HINTS
from job_scraper.domain.locations import merge_locations
from job_scraper.domain.models import JobRecord, RawJobRecord
from job_scraper.domain.policies import FilterPolicy
from job_scraper.pipeline.language_filter import describe_language

MOJIBAKE_MARKERS = (
    "\u95b3",
    "\u9474",
    "\u9417",
    "\u9469",
    "\u93cc",
    "\u8292",
)


def canonicalize_url(url: str) -> str:
    parts = urlsplit(url)
    hostname = (parts.hostname or "").casefold()
    if hostname == "indeed.com" or hostname.endswith(".indeed.com"):
        job_key = dict(parse_qsl(parts.query)).get("jk", "")
        if job_key:
            return urlunsplit(
                (parts.scheme, parts.netloc, parts.path, urlencode({"jk": job_key}), "")
            )
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def repair_mojibake(value: str) -> str:
    if not value:
        return value
    if not any(marker in value for marker in MOJIBAKE_MARKERS):
        return value
    baseline_score = sum(value.count(marker) for marker in MOJIBAKE_MARKERS)
    for encoding in ("cp1252", "latin1"):
        try:
            repaired = value.encode(encoding).decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
        repaired_score = sum(repaired.count(marker) for marker in MOJIBAKE_MARKERS)
        if repaired_score < baseline_score:
            return repaired
    return value


def normalize_whitespace(value: str) -> str:
    return re.sub(r"\s+", " ", repair_mojibake(value)).strip()


def normalize_title_for_dedupe(value: str, location_raw: str = "") -> str:
    lowered = strip_title_location_suffix(normalize_whitespace(value), location_raw).lower()
    lowered = re.sub(r"\((?:m/w/d|w/m/d|f/m/d|all genders|gn)\)", "", lowered)
    lowered = re.sub(r"\b(senior|junior|lead|principal)\b", "", lowered)
    return normalize_whitespace(lowered)


def strip_title_location_suffix(title: str, location_raw: str) -> str:
    normalized_title = normalize_whitespace(title)
    match = re.match(r"^(?P<base>.+?)\s+in\s+(?P<tail>.+)$", normalized_title, flags=re.IGNORECASE)
    if not match:
        return normalized_title

    base = normalize_whitespace(match.group("base"))
    tail = normalize_whitespace(match.group("tail"))
    if not base or not tail:
        return normalized_title

    for option in location_match_variants(location_raw):
        if _location_suffix_matches(tail, option):
            return base

    if re.search(r"\((?:m/w/d|w/m/d|f/m/d|all genders|gn)\)", base, flags=re.IGNORECASE):
        return base
    return normalized_title


def city_only(location_raw: str) -> str:
    if not location_raw:
        return ""
    first = location_raw.split(",")[0]
    first = re.sub(r"\b(germany|deutschland)\b", "", first, flags=re.IGNORECASE)
    return normalize_whitespace(first)


def split_location_options(value: str) -> list[str]:
    if not value:
        return []
    options: list[str] = []
    for part in re.split(r"\s*(?:\||/|;)\s*", value):
        city = city_only(part)
        if city and city.casefold() != "multiple locations":
            options.append(city)
    unique: list[str] = []
    seen: set[str] = set()
    for option in options:
        key = option.casefold()
        if key in seen:
            continue
        seen.add(key)
        unique.append(option)
    return unique


def location_match_variants(value: str) -> list[str]:
    variants: list[str] = []
    for option in split_location_options(value):
        variants.append(option)
        folded = ascii_fold(option)
        if folded and folded.casefold() != option.casefold():
            variants.append(folded)
        if option.casefold() == "frankfurt":
            variants.append("Frankfurt (Main)")
    return unique_casefold(variants)


def ascii_fold(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    return "".join(char for char in normalized if not unicodedata.combining(char))


def unique_casefold(values: list[str]) -> list[str]:
    unique: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = normalize_whitespace(value)
        if not cleaned:
            continue
        key = cleaned.casefold()
        if key in seen:
            continue
        seen.add(key)
        unique.append(cleaned)
    return unique


def _location_suffix_matches(tail: str, option: str) -> bool:
    tail_key = _normalize_location_key(tail)
    option_key = _normalize_location_key(option)
    return bool(option_key) and (
        tail_key == option_key or tail_key.startswith(option_key) or option_key.startswith(tail_key)
    )


def _normalize_location_key(value: str) -> str:
    cleaned = ascii_fold(normalize_whitespace(value)).lower()
    cleaned = re.sub(r"[^a-z0-9]+", " ", cleaned)
    return normalize_whitespace(cleaned)


def infer_remote_type(text: str) -> str:
    lowered = text.lower()
    if "hybrid" in lowered:
        return "hybrid"
    if "remote" in lowered:
        return "remote"
    if "on-site" in lowered or "onsite" in lowered:
        return "onsite"
    return "unknown"


def infer_seniority(text: str) -> str:
    lowered = text.lower()
    if any(token in lowered for token in ("lead", "principal", "staff")):
        return "lead"
    if "senior" in lowered:
        return "senior"
    if any(token in lowered for token in ("junior", "graduate", "entry")):
        return "junior"
    return "mid"


def extract_keywords(text: str, include_keywords: list[str]) -> list[str]:
    lowered = text.lower()
    return [keyword for keyword in include_keywords if keyword in lowered]


def extract_tech_stack(text: str, configured_terms: list[str]) -> list[str]:
    lowered = text.lower()
    return [keyword for keyword in configured_terms if keyword in lowered]


def description_signature(value: str) -> str:
    normalized = normalize_whitespace(value).lower()
    if not normalized:
        return ""
    return hashlib.sha256(normalized[:500].encode("utf-8")).hexdigest()[:16]


def build_dedupe_key(
    title: str,
    company: str,
    location_raw: str,
    source_job_id: str,
    description: str = "",
    source: str = "",
) -> str:
    descriptor = description_signature(description) or source_job_id.strip().lower()
    basis = "|".join(
        [
            normalize_whitespace(source).lower(),
            normalize_title_for_dedupe(title, location_raw),
            normalize_whitespace(company).lower(),
            descriptor,
        ]
    )
    if not basis.strip("|"):
        basis = source_job_id.strip().lower()
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()


def looks_like_germany(location_raw: str, country: str) -> bool:
    return looks_like_target_countries(location_raw, country, "DE")


def looks_like_target_countries(
    location_raw: str,
    country: str,
    target_country_filter: str,
    raw_country: object = None,
) -> bool:
    target_codes = parse_country_codes(target_country_filter)
    if not target_codes:
        return True

    location_country_code = known_location_country(location_raw)
    if location_country_code:
        return location_country_code in target_codes

    raw_country_code = country_to_code(str(raw_country or ""))
    if raw_country_code and raw_country_code in target_codes:
        return True

    country_code = country_to_code(country)
    if country_code and country_code not in target_codes:
        return False

    normalized_location = normalize_location_key(location_raw)
    if not normalized_location:
        return bool(country_code and country_code in target_codes)

    return any(location_matches_country(normalized_location, code) for code in target_codes)


def parse_country_codes(value: str) -> list[str]:
    raw = normalize_whitespace(value)
    if not raw:
        return []
    parts = [part for part in re.split(r"\s*[,;|/]\s*", raw) if part]
    if len(parts) == 1:
        parts = [raw]
    codes: list[str] = []
    for part in parts:
        code = country_to_code(part)
        if code and code not in codes:
            codes.append(code)
    return codes


def country_to_code(value: str) -> str:
    cleaned = normalize_location_key(value)
    if not cleaned:
        return ""
    upper = cleaned.upper()
    if upper in COUNTRY_ALIASES:
        return upper
    for code, aliases in COUNTRY_ALIASES.items():
        if cleaned in aliases:
            return code
    return ""


def location_matches_country(normalized_location: str, country_code: str) -> bool:
    pattern = _country_hint_pattern(country_code)
    return pattern is not None and pattern.search(normalized_location) is not None


@cache
def _country_hint_pattern(country_code: str) -> re.Pattern[str] | None:
    """One compiled alternation per country, built once and reused.

    The hint sets are constants, so normalizing and escaping each of them on
    every call -- for every country, for every location segment -- was pure
    repeated work. On a location that matches nothing this dominated the whole
    normalization step.
    """
    hints = sorted(
        {
            normalized
            for hint in COUNTRY_LOCATION_HINTS.get(country_code, set())
            if (normalized := normalize_location_key(hint))
        },
        key=len,
        reverse=True,
    )
    if not hints:
        return None
    alternation = "|".join(re.escape(hint) for hint in hints)
    return re.compile(rf"(?<![a-z0-9])(?:{alternation})(?![a-z0-9])")


def known_location_country(location_raw: str) -> str:
    exact_location_country = country_to_code(location_raw)
    if exact_location_country:
        return exact_location_country
    # A country/region designator is most often the last comma-separated
    # segment (e.g. "Vienna, VA, USA"). Check segments from last to first so
    # an explicit signal there wins over an earlier, unrelated city-name
    # hint (e.g. "Vienna" alone would otherwise match Austria first).
    segments = location_raw.split(",")
    for segment in reversed(segments):
        normalized_segment = normalize_location_key(segment)
        if not normalized_segment:
            continue
        for country_code in COUNTRY_LOCATION_HINTS:
            if location_matches_country(normalized_segment, country_code):
                return country_code
    normalized_location = normalize_location_key(location_raw)
    for country_code in COUNTRY_LOCATION_HINTS:
        if location_matches_country(normalized_location, country_code):
            return country_code
    return ""


def parse_relative_posted_at(posted_at_text: str, now: datetime) -> datetime | None:
    lowered = posted_at_text.lower().strip()
    if not lowered:
        return None
    try:
        parsed = datetime.fromisoformat(posted_at_text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed
    except ValueError:
        pass
    if any(token in lowered for token in ("today", "just posted", "heute", "gerade eben")):
        return now
    if any(token in lowered for token in ("yesterday", "gestern")):
        return now - timedelta(days=1)
    match = re.search(r"(\d+)", lowered)
    if not match:
        return None
    value = int(match.group(1))
    if any(token in lowered for token in ("hour", "hours", "std", "stunde", "stunden", " h")):
        return now - timedelta(hours=value)
    if any(token in lowered for token in ("day", "days", "tag", "tage", "t+", " t", "d+")):
        return now - timedelta(days=value)
    return None


def was_posted_within_hours(posted_at: datetime | None, now: datetime, max_age_hours: int) -> bool:
    if posted_at is None:
        return False
    age = now - posted_at.astimezone(UTC)
    return age <= timedelta(hours=max_age_hours)


def normalize_candidate(raw: RawJobRecord, policy: FilterPolicy) -> JobRecord:
    title = normalize_whitespace(raw.title)
    description = normalize_whitespace(raw.job_description)
    location = normalize_whitespace(raw.location_raw)
    salary_text = normalize_whitespace(raw.salary_text)
    combined_text = " ".join([title, description, location])
    dedupe_key = build_dedupe_key(
        title,
        raw.company_name,
        location,
        raw.source_job_id,
        description=description,
        source=raw.source,
    )
    language, ratio = describe_language(description or title)
    posted_at = parse_relative_posted_at(raw.posted_at_text, raw.scraped_at.astimezone(UTC))
    merged_location, city, location_options = merge_locations(location)
    payload = dict(raw.raw_payload)
    if location_options:
        payload["location_options"] = location_options
    target_countries = ",".join(policy.countries)
    country = infer_country(payload.get("location_country"), location, target_countries)
    if not country:
        country = infer_search_location_country(payload.get("search_location"), target_countries)

    return JobRecord(
        source=raw.source,
        source_job_id=raw.source_job_id,
        source_url=raw.source_url,
        canonical_url=canonicalize_url(raw.canonical_url or raw.source_url),
        title=title,
        company_name=normalize_whitespace(raw.company_name),
        location_raw=merged_location or location,
        country=country,
        city=city,
        region="",
        remote_type=raw.remote_type
        if raw.remote_type != "unknown"
        else infer_remote_type(combined_text),
        employment_type=raw.employment_type,
        seniority=raw.seniority if raw.seniority != "unknown" else infer_seniority(combined_text),
        posted_at=posted_at,
        first_seen_at=raw.scraped_at,
        scraped_at=raw.scraped_at,
        job_description=description,
        description_language=language,
        english_ratio=ratio,
        keyword_hits=extract_keywords(combined_text, list(policy.signals)),
        tech_stack=extract_tech_stack(
            combined_text,
            unique_casefold([*policy.signals, *policy.acceptance_terms]),
        ),
        salary_text=salary_text,
        salary_min=None,
        salary_max=None,
        salary_currency="EUR" if "\u20ac" in salary_text else None,
        dedupe_key=dedupe_key,
        raw_payload=payload,
    )


def infer_country(raw_country: object, location_raw: str, default_country: str) -> str:
    normalized_country = normalize_whitespace(str(raw_country or ""))
    if normalized_country:
        country_code = country_to_code(normalized_country)
        if country_code:
            return country_code
        return normalized_country
    return known_location_country(location_raw)


def infer_search_location_country(search_location: object, target_country_filter: str) -> str:
    normalized_location = normalize_location_key(str(search_location or ""))
    if not normalized_location:
        return ""
    for country_code in parse_country_codes(target_country_filter):
        if location_matches_country(normalized_location, country_code):
            return country_code
    return ""


def normalize_location_key(value: str) -> str:
    folded = ascii_fold(normalize_whitespace(value)).casefold()
    return normalize_whitespace(re.sub(r"[^a-z0-9]+", " ", folded))


def serialize_payload(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=True, sort_keys=True)
