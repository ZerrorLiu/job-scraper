from __future__ import annotations

from job_scraper.pipeline.engine import CandidatePipeline

# Deterministic content-quality gates (country, freshness, company identity,
# employment scope, excluded terms, role/keyword matching, requirement
# exclusion, language ratio) were removed here: keyword/regex matching on
# incomplete or messy source data was mis-rejecting real candidates (title
# wording that didn't match a configured keyword, a city name landing in a
# "country" field, a short description skewing the English-ratio heuristic).
# Every normalized candidate now reaches the feed; semantic relevance,
# location fit, freshness, and language judgment are made by the downstream
# agent screener (fine-screen), which sees the full job text and profile
# instead of a handful of regexes. See
# docs/public/specs/2026-08-28-first-class-agent-screening.md.
DEFAULT_STEPS: tuple = ()


def default_pipeline() -> CandidatePipeline:
    return CandidatePipeline(DEFAULT_STEPS)
