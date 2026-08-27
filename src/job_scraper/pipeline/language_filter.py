from __future__ import annotations

import re
import unicodedata
from functools import lru_cache

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


def language_hint_counts(text: str) -> tuple[int, int]:
    """Count English and German hint words in one pass over the tokens."""
    english_hits = 0
    german_hits = 0
    for token in tokenize_words(text):
        if token in ENGLISH_HINTS:
            english_hits += 1
        elif token in GERMAN_HINTS:
            german_hits += 1
    return english_hits, german_hits


def _ratios(english_hits: int, german_hits: int) -> tuple[float, float]:
    total = english_hits + german_hits
    if total < MINIMUM_LANGUAGE_HINT_HITS:
        return 0.0, 0.0
    return round(english_hits / total, 4), round(german_hits / total, 4)


def english_ratio(text: str) -> float:
    return _ratios(*language_hint_counts(text))[0]


def german_ratio(text: str) -> float:
    return _ratios(*language_hint_counts(text))[1]


def describe_language(text: str) -> tuple[str, float]:
    """Return the language label and English ratio from a single tokenization.

    Callers need both together, and scanning the whole description once per
    value meant a long posting was tokenized four times over.
    """
    cleaned = " ".join(text.split())
    if not cleaned:
        return "Unknown", 0.0
    en, de = _ratios(*language_hint_counts(cleaned))
    if en >= 0.75 and de <= 0.25:
        return "English", en
    if de >= 0.6 and de > en:
        return "German", en
    if en == 0 and de == 0:
        return "Unknown", en
    return "Mixed", en


def classify_description_language(text: str) -> str:
    return describe_language(text)[0]


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
    """Decide whether a description's language passes the policy.

    The three keys are one interacting contract, not three independent
    knobs: a non-empty `allowed_languages` decides the verdict by label
    membership alone, full stop. `english_threshold` governs only the empty
    branch below. Consulting it for a membership hit would gate a single
    label (English) behind a bar the other admitted labels never faced,
    which rejects a more-English description while a less-English one
    admitted through a different label passes -- see
    `docs/public/specs/2026-08-27-description-language-policy-defect.md`.
    `config.load_config` refuses to load a profile that sets both, so this
    function never has to choose between the two at runtime.
    """
    normalized_language = " ".join((language or "").split()).strip().casefold()
    normalized_allowed = {
        " ".join(value.split()).strip().casefold() for value in allowed_languages if value.strip()
    }
    if normalized_allowed:
        return normalized_language in normalized_allowed
    return not require_english or is_english_job(language, english_score, english_threshold)


def matches_requirement_patterns(text: str, patterns: tuple[str, ...]) -> bool:
    """True when any excluded requirement phrase appears in the text.

    One alternation over one normalized copy of the text, rather than
    re-normalizing each phrase and scanning the whole description once per
    phrase. The phrases come from configuration and do not change between
    candidates, so the compiled form is cached against them.
    """
    regex = _requirement_pattern(patterns)
    if regex is None:
        return False
    return regex.search(normalize_language_text(text)) is not None


@lru_cache(maxsize=256)
def _requirement_pattern(patterns: tuple[str, ...]) -> re.Pattern[str] | None:
    normalized = sorted(
        {text for pattern in patterns if (text := normalize_language_text(pattern))},
        key=len,
        reverse=True,
    )
    if not normalized:
        return None
    alternation = "|".join(re.escape(text).replace(r"\ ", r"\s+") for text in normalized)
    return re.compile(rf"(?<![a-z0-9])(?:{alternation})(?![a-z0-9])")


def normalize_language_text(text: str) -> str:
    """Casefold, strip diacritics, and collapse whitespace.

    Job descriptions are overwhelmingly ASCII, and for ASCII the NFKD pass and
    the combining-mark filter are both no-ops -- but the filter still walks the
    string one character at a time in Python, which made this the second
    largest cost in evaluating a candidate. Skipping both for text that has no
    non-ASCII character leaves the result identical.
    """
    lowered = text.lower()
    if lowered.isascii():
        return " ".join(lowered.split())
    normalized = unicodedata.normalize("NFKD", lowered)
    without_marks = "".join(char for char in normalized if not unicodedata.combining(char))
    return " ".join(without_marks.split())
