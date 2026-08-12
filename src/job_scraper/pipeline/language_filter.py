from __future__ import annotations

import re
import unicodedata

ENGLISH_HINTS = {
    "about",
    "and",
    "benefits",
    "build",
    "customer",
    "customers",
    "data",
    "deliver",
    "delivery",
    "design",
    "develop",
    "developer",
    "engineer",
    "engineering",
    "english",
    "experience",
    "for",
    "join",
    "platform",
    "position",
    "product",
    "products",
    "qualification",
    "qualifications",
    "requirements",
    "responsibilities",
    "role",
    "scientist",
    "skills",
    "software",
    "solution",
    "solutions",
    "system",
    "systems",
    "team",
    "the",
    "we",
    "will",
    "with",
    "you",
    "your",
}

GERMAN_HINTS = {
    "anforderung",
    "anforderungen",
    "aufgaben",
    "bewerbung",
    "das",
    "dein",
    "deine",
    "der",
    "deutsch",
    "deutschkenntnisse",
    "die",
    "entwickler",
    "entwicklung",
    "erfahrung",
    "ingenieur",
    "kenntnisse",
    "mit",
    "profil",
    "qualifikationen",
    "sie",
    "stellenbeschreibung",
    "und",
    "unternehmen",
    "wir",
}


def tokenize_words(text: str) -> list[str]:
    normalized = normalize_language_text(text)
    return re.findall(r"[a-z']+", normalized)


# Minimum combined hint-word hits before the ratio is trusted. Below this,
# there is too little evidence to distinguish "confidently English" from
# "just happened to contain a couple of English tech nouns" (e.g. a German
# title like "Werkstudent Data Engineer" scoring 1.0 from two hits).
MINIMUM_LANGUAGE_HINT_HITS = 3


def english_ratio(text: str) -> float:
    tokens = tokenize_words(text)
    if not tokens:
        return 0.0
    english_hits = sum(1 for token in tokens if token in ENGLISH_HINTS)
    german_hits = sum(1 for token in tokens if token in GERMAN_HINTS)
    if english_hits + german_hits < MINIMUM_LANGUAGE_HINT_HITS:
        return 0.0
    score = english_hits / max(english_hits + german_hits, 1)
    return round(score, 4)


def german_ratio(text: str) -> float:
    tokens = tokenize_words(text)
    if not tokens:
        return 0.0
    english_hits = sum(1 for token in tokens if token in ENGLISH_HINTS)
    german_hits = sum(1 for token in tokens if token in GERMAN_HINTS)
    if english_hits + german_hits < MINIMUM_LANGUAGE_HINT_HITS:
        return 0.0
    score = german_hits / max(english_hits + german_hits, 1)
    return round(score, 4)


def classify_description_language(text: str) -> str:
    cleaned = " ".join(text.split())
    if not cleaned:
        return "Unknown"
    en = english_ratio(cleaned)
    de = german_ratio(cleaned)
    if en >= 0.75 and de <= 0.25:
        return "English"
    if de >= 0.6 and de > en:
        return "German"
    if en == 0 and de == 0:
        return "Unknown"
    return "Mixed"


def is_english_job(language: str, ratio: float, threshold: float) -> bool:
    normalized_language = " ".join((language or "").split()).strip().lower()
    return normalized_language == "english" and ratio >= threshold


def is_allowed_description_language(
    language: str,
    english_score: float,
    english_threshold: float,
    *,
    require_english: bool,
    allowed_languages: tuple[str, ...],
) -> bool:
    normalized_language = " ".join((language or "").split()).strip().casefold()
    normalized_allowed = {
        " ".join(value.split()).strip().casefold() for value in allowed_languages if value.strip()
    }
    if normalized_allowed:
        if normalized_language not in normalized_allowed:
            return False
        if normalized_language == "english":
            return english_score >= english_threshold
        return True
    return not require_english or is_english_job(language, english_score, english_threshold)


def matches_requirement_patterns(text: str, patterns: tuple[str, ...]) -> bool:
    lowered = normalize_language_text(text)
    for pattern in patterns:
        normalized_pattern = normalize_language_text(pattern)
        if not normalized_pattern:
            continue
        escaped = re.escape(normalized_pattern).replace(r"\ ", r"\s+")
        regex = rf"(?<![a-z0-9]){escaped}(?![a-z0-9])"
        if re.search(regex, lowered) is not None:
            return True
    return False


def normalize_language_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text.lower())
    without_marks = "".join(char for char in normalized if not unicodedata.combining(char))
    return " ".join(without_marks.split())
