from __future__ import annotations

from job_scraper.domain.decisions import Decision, RejectionReason
from job_scraper.domain.models import JobRecord
from job_scraper.pipeline.context import EvaluationContext
from job_scraper.pipeline.engine import CandidatePipeline
from job_scraper.pipeline.language_filter import (
    is_allowed_description_language,
    matches_requirement_patterns,
)
from job_scraper.pipeline.normalize import (
    looks_like_target_countries,
    was_posted_within_hours,
)
from job_scraper.pipeline.role_filter import (
    company_matches_allowlist,
    has_excluded_keyword,
    is_full_time_role,
    text_matches_target,
)


class CountryStep:
    name = "country"

    def evaluate(self, job: JobRecord, context: EvaluationContext) -> Decision:
        countries = context.policy.countries
        country_filter = ",".join(countries)
        raw_country = job.raw_payload.get("location_country")
        if looks_like_target_countries(
            job.location_raw,
            job.country,
            country_filter,
            raw_country=raw_country,
        ):
            return Decision.accept()

        search_location = str(job.raw_payload.get("search_location") or "")
        if job.country in countries and looks_like_target_countries(
            search_location,
            "",
            country_filter,
        ):
            return Decision.accept()
        if not _known_value(job.country) and not _known_value(raw_country):
            return Decision.accept()

        return Decision.reject(RejectionReason.NOT_TARGET_COUNTRY, step=self.name)


class FreshnessStep:
    name = "freshness"

    def evaluate(self, job: JobRecord, context: EvaluationContext) -> Decision:
        freshness = context.policy.freshness
        if freshness.max_age_hours <= 0 or _uses_first_seen_freshness(job):
            return Decision.accept()
        if job.posted_at is None and not freshness.require_posted_at:
            return Decision.accept()
        if was_posted_within_hours(
            job.posted_at,
            context.started_at,
            freshness.max_age_hours,
        ):
            return Decision.accept()
        return Decision.reject(
            RejectionReason.TOO_OLD,
            step=self.name,
            details={"max_age_hours": freshness.max_age_hours},
        )


class CompanyStep:
    name = "company"

    def evaluate(self, job: JobRecord, context: EvaluationContext) -> Decision:
        if company_matches_allowlist(
            job.company_name,
            list(context.policy.allowed_companies),
        ):
            return Decision.accept()
        return Decision.reject(RejectionReason.COMPANY_NOT_ALLOWED, step=self.name)


class EmploymentScopeStep:
    name = "employment_scope"

    def evaluate(self, job: JobRecord, context: EvaluationContext) -> Decision:
        if not context.policy.full_time_only or is_full_time_role(
            job.title,
            job.job_description,
            job.employment_type,
            allow_part_time=context.policy.allow_part_time,
            allow_temporary=context.policy.allow_temporary,
        ):
            return Decision.accept()
        return Decision.reject(RejectionReason.NOT_FULL_TIME, step=self.name)


class ExcludedTermsStep:
    name = "excluded_terms"

    def evaluate(self, job: JobRecord, context: EvaluationContext) -> Decision:
        if not has_excluded_keyword(
            job.title,
            job.job_description,
            job.employment_type,
            list(context.policy.excluded_terms),
        ):
            return Decision.accept()
        return Decision.reject(RejectionReason.EXCLUDED_KEYWORD, step=self.name)


class RoleStep:
    name = "role"

    def evaluate(self, job: JobRecord, context: EvaluationContext) -> Decision:
        if text_matches_target(
            job.title,
            job.job_description,
            list(context.policy.acceptance_terms),
            context.policy.acceptance_scope,
            target_rules=list(context.policy.acceptance_rules),
        ):
            return Decision.accept()
        return Decision.reject(
            RejectionReason.MISSING_TARGET_KEYWORDS,
            step=self.name,
        )


class RequirementExclusionStep:
    name = "requirement_exclusion"

    def evaluate(self, job: JobRecord, context: EvaluationContext) -> Decision:
        patterns = context.policy.excluded_requirement_patterns
        if not patterns:
            return Decision.accept()
        text = job.job_description
        if not text or not matches_requirement_patterns(text, patterns):
            return Decision.accept()
        return Decision.reject(RejectionReason.EXCLUDED_REQUIREMENT, step=self.name)


class LanguageStep:
    name = "language"

    def evaluate(self, job: JobRecord, context: EvaluationContext) -> Decision:
        if is_allowed_description_language(
            job.description_language,
            job.english_ratio,
            context.policy.minimum_english_ratio,
            require_english=context.policy.require_english,
            allowed_languages=context.policy.allowed_description_languages,
        ):
            return Decision.accept()
        return Decision.reject(RejectionReason.NON_ENGLISH, step=self.name)


DEFAULT_STEPS = (
    CountryStep(),
    FreshnessStep(),
    CompanyStep(),
    EmploymentScopeStep(),
    ExcludedTermsStep(),
    RoleStep(),
    RequirementExclusionStep(),
    LanguageStep(),
)


def default_pipeline() -> CandidatePipeline:
    return CandidatePipeline(DEFAULT_STEPS)


def _uses_first_seen_freshness(job: JobRecord) -> bool:
    return str(job.raw_payload.get("freshness_basis", "")).strip().lower() == "first_seen"


def _known_value(value: object) -> bool:
    return str(value or "").strip().casefold() not in {"", "n/a", "unknown", "none", "null"}
