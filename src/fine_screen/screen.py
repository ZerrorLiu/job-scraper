"""Screen job postings against a resume and generate matched applications.

Jobs arrive through `job-scraper feed`, a versioned JSON document. This module
never opens job-scraper's databases, knows its table names, or reads its .env,
so the two projects can move independently as long as the document holds.

Everything personal -- resume variants, the evidence library, generated
applications, the candidate's name -- lives in a workspace directory named by
`--workspace`. See `fine_screen.workspace`.

    fine-screen --workspace ~/my-workspace --since-days 4
    fine-screen --workspace ~/my-workspace --track cpp --apply
"""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import unicodedata
from collections.abc import Iterable
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from fine_screen.agent.contract import (
    PROMPT_VERSION,
    TAILORING_PROMPT_VERSION,
    AgentContractError,
    AgentDecision,
    AgentQuotaExceeded,
    ResumeTailoring,
    TailoredProject,
    TailoringText,
    build_prompt,
    build_tailoring_prompt,
    decision_cache_key,
    load_cached_decision,
    load_cached_tailoring,
    run_agent,
    store_cached_decision,
    store_cached_tailoring,
    tailoring_cache_key,
    validate_batch,
    validate_tailoring,
)
from fine_screen.release import ReleaseError, ReleaseIdentity, verify_manifest
from fine_screen.workspace import Candidate, WorkspaceError, load_workspace

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python < 3.11 fallback
    import tomli as tomllib  # type: ignore[no-redef]

stdout_reconfigure = getattr(sys.stdout, "reconfigure", None)
if callable(stdout_reconfigure):
    stdout_reconfigure(encoding="utf-8", errors="replace")

# The private workspace and the job-scraper command are explicit runtime
# dependencies. An installed `job-scraper` needs no source checkout; operators
# that intentionally run it through `uv` may also supply a working directory.
DEFAULT_JOB_SCRAPER_ARGV = ("job-scraper",)

NOTION_VERSION = "2026-03-11"
SCREEN_PROPERTY = "Screen"
SCREEN_TAG = "Fine-screened"
SCREEN_REJECTED_TAG = "Fine-screen rejected"
SCREEN_BLOCKED_TAG = "Fine-screen blocked"
SCREEN_ERROR_TAG = "Fine-screen error"
SCREEN_OPTIONS = {
    SCREEN_TAG: "pink",
    SCREEN_REJECTED_TAG: "gray",
    SCREEN_BLOCKED_TAG: "orange",
    SCREEN_ERROR_TAG: "red",
}
LOCAL_TIMEZONE = ZoneInfo("Europe/Berlin")
_AGENT_ASSETS = Path(__file__).parent / "agent"
AGENT_SCHEMA_PATH = _AGENT_ASSETS / "screening.schema.json"
AGENT_PROMPT_PATH = _AGENT_ASSETS / "screening_prompt.md"
TAILORING_SCHEMA_PATH = _AGENT_ASSETS / "tailoring.schema.json"
TAILORING_PROMPT_PATH = _AGENT_ASSETS / "tailoring_prompt.md"
SKILLS_INSERTION_MARKER = "% FINE_SCREEN_SKILLS_INSERTION_POINT"

# Jobs arrive through `job-scraper feed`, a versioned document, rather than by
# opening Positions' SQLite files here. Reading those directly meant knowing the
# `data/jobs_<track>.db` filename convention, three table names, and which store
# held live publication state -- and a new track in Positions was silently
# invisible until this map was edited to match. The feed reports its own tracks.
FEED_SCHEMA_VERSION = 2
PROCESSING_MODES = frozenset({"core", "review", "discovery"})


@dataclass
class Job:
    id: str
    track: str
    title: str
    company: str
    location: str
    language: str
    url: str
    description_full: str
    first_seen_at: str
    notion_page_id: str
    notion_data_source_id: str
    processing_mode: str = "core"
    employment_type: str = ""


@dataclass
class WhitelistEntry:
    match: str
    phrase: str
    hours: float = 0.0
    project: str = ""
    interview_check: str = ""


@dataclass(frozen=True)
class EvidenceCard:
    evidence_id: str
    organization: str
    location: str
    role: str
    dates: str
    facts: tuple[str, ...]


@dataclass
class MatchResult:
    job: Job
    variant: str
    covered: set[str]
    addable: list[WhitelistEntry]
    true_gap: set[str]
    score: float
    core_fit: str
    daily_work: str
    rationale: str
    decision_source: str
    tailoring: ResumeTailoring | None = None


@dataclass(frozen=True)
class NotionFileBlock:
    block_id: str
    name: str


def load_whitelist(path: Path) -> list[WhitelistEntry]:
    if not path.exists():
        return []
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    return [
        WhitelistEntry(
            match=str(entry["match"]).strip().lower(),
            phrase=str(entry["phrase"]),
            hours=float(entry.get("hours", 0)),
            project=str(entry.get("project", "")).strip(),
            interview_check=str(entry.get("interview_check", "")).strip(),
        )
        for entry in data.get("skill", [])
    ]


def load_evidence_library(path: Path) -> tuple[EvidenceCard, ...]:
    """Load the curated cross-project facts that may be composed into a CV."""
    data = json.loads(path.read_text(encoding="utf-8"))
    raw_cards = data.get("cards")
    if not isinstance(raw_cards, list) or not raw_cards:
        raise ValueError("evidence library must contain a non-empty cards array")
    cards: list[EvidenceCard] = []
    identifiers: set[str] = set()
    for raw in raw_cards:
        if not isinstance(raw, dict) or set(raw) != {
            "id",
            "organization",
            "location",
            "role",
            "dates",
            "facts",
        }:
            raise ValueError("each evidence card has unsupported fields")
        evidence_id = str(raw["id"]).strip()
        facts = raw["facts"]
        if not evidence_id or evidence_id in identifiers:
            raise ValueError(f"evidence library has duplicate or empty ID: {evidence_id!r}")
        if (
            not isinstance(facts, list)
            or not facts
            or any(not isinstance(fact, str) for fact in facts)
        ):
            raise ValueError(f"evidence card {evidence_id!r} must contain factual strings")
        identifiers.add(evidence_id)
        cards.append(
            EvidenceCard(
                evidence_id=evidence_id,
                organization=str(raw["organization"]).strip(),
                location=str(raw["location"]).strip(),
                role=str(raw["role"]).strip(),
                dates=str(raw["dates"]).strip(),
                facts=tuple(fact.strip() for fact in facts if fact.strip()),
            )
        )
    return tuple(cards)


def evidence_cards_for_prompt(cards: tuple[EvidenceCard, ...]) -> list[dict[str, object]]:
    return [
        {
            "id": card.evidence_id,
            "organization": card.organization,
            "location": card.location,
            "role": card.role,
            "dates": card.dates,
            "facts": card.facts,
        }
        for card in cards
    ]


class FeedError(RuntimeError):
    """The feed could not be read, or was not the shape this screener knows."""


def job_from_feed_record(record: dict) -> Job:
    publication = record.get("publication") or {}
    processing_mode = str(record.get("processing_mode", "")).strip().lower()
    if processing_mode not in PROCESSING_MODES:
        raise FeedError(
            f"job {record.get('job_id', '')!r} has unsupported processing_mode {processing_mode!r}"
        )
    return Job(
        id=str(record.get("job_id", "")),
        track=str(record.get("profile_id", "")),
        title=str(record.get("title", "")),
        company=str(record.get("company", "")),
        location=str(record.get("location", "")),
        language=str(record.get("language", "")),
        url=str(record.get("url", "")),
        description_full=str(record.get("description", "")),
        first_seen_at=str(record.get("first_seen_at", "")),
        notion_page_id=str(publication.get("external_id", "")),
        notion_data_source_id=str(publication.get("container_id", "")),
        processing_mode=processing_mode,
        employment_type=str(record.get("employment_type", "")),
    )


def parse_feed_document(payload: str) -> tuple[list[Job], str, str]:
    """Turn a feed document into jobs, and refuse a shape we do not know.

    The version check is a hard failure rather than a warning. A screener that
    guesses at an unfamiliar document would generate real applications from
    fields it misread, and the cost of stopping is one operator message.
    """
    try:
        document = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise FeedError(f"feed output was not JSON: {exc}") from exc
    if not isinstance(document, dict):
        raise FeedError("feed output was not a document")

    version = document.get("schema_version")
    if version != FEED_SCHEMA_VERSION:
        raise FeedError(
            f"feed schema_version {version!r} is not the {FEED_SCHEMA_VERSION} this screener "
            "reads; upgrade job-scraper and this screener together"
        )

    window = document.get("window") or {}
    records = document.get("records")
    if not isinstance(records, list):
        raise FeedError("feed document has no records list")
    return (
        [job_from_feed_record(record) for record in records if isinstance(record, dict)],
        str(window.get("since", "")),
        str(window.get("until", "")),
    )


def read_feed(
    positions_root: Path | None,
    job_scraper_argv: list[str],
    tracks: list[str] | None,
    window_args: list[str],
    *,
    published_only: bool,
) -> str:
    """Ask Positions for its screening feed.

    Dry runs deliberately include unpublished jobs so screening can happen
    before the display sink. Apply remains compatibility-safe until Positions
    owns the final publication step, and therefore requests published jobs.
    """
    argv = [*job_scraper_argv, "feed", *window_args]
    if published_only:
        argv.append("--published-only")
    for track in tracks or []:
        argv += ["--profile", track]
    completed = subprocess.run(
        argv,
        cwd=positions_root,
        shell=False,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if completed.returncode != 0:
        raise FeedError(
            f"`job-scraper feed` failed with exit code {completed.returncode}: "
            f"{completed.stderr.strip() or '(no stderr)'}"
        )
    return completed.stdout


def persist_screening_results(
    positions_root: Path | None, job_scraper_argv: list[str], result_path: Path
) -> None:
    completed = subprocess.run(
        [*job_scraper_argv, "db", "import-screening", str(result_path)],
        cwd=positions_root,
        shell=False,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if completed.returncode != 0:
        raise FeedError(
            "screening result persistence failed before artifacts/publication: "
            f"{completed.stderr.strip() or completed.stdout.strip() or '(no details)'}"
        )
    print(completed.stdout.strip())


def publish_finalized_results(
    positions_root: Path | None,
    job_scraper_argv: list[str],
    result_path: Path,
    *,
    expected_count: int,
) -> None:
    completed = subprocess.run(
        [
            *job_scraper_argv,
            "db",
            "publish-screening",
            str(result_path),
            "--expect-job-count",
            str(expected_count),
        ],
        cwd=positions_root,
        shell=False,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if completed.returncode != 0:
        details = "\n".join(
            part for part in (completed.stdout.strip(), completed.stderr.strip()) if part
        )
        raise FeedError(
            f"finalized publication failed after artifact validation: {details or '(no details)'}"
        )
    print(completed.stdout.strip())


def refresh_job_publications(
    jobs: list[Job],
    *,
    positions_root: Path | None,
    job_scraper_argv: list[str],
    tracks: list[str],
    window_args: list[str],
) -> None:
    payload = read_feed(positions_root, job_scraper_argv, tracks, window_args, published_only=True)
    refreshed, _since, _until = parse_feed_document(payload)
    publications = {job.id: job for job in refreshed if job.notion_page_id}
    missing = sorted(job.id for job in jobs if job.id not in publications)
    if missing:
        raise FeedError(f"{len(missing)} finalized jobs have no published Notion page")
    for job in jobs:
        publication = publications[job.id]
        job.notion_page_id = publication.notion_page_id
        job.notion_data_source_id = publication.notion_data_source_id


def load_variants(cv_root: Path) -> dict[str, str]:
    variants = {}
    for path in sorted((cv_root / "resume" / "variants").glob("*.tex")):
        template = path.read_text(encoding="utf-8")
        variants[path.stem] = _replace_skills_marker(template, replacement=None)
    return variants


def whitelist_for_prompt(entries: list[WhitelistEntry]) -> list[dict[str, object]]:
    return [
        {
            "match": entry.match,
            "resume_phrase": entry.phrase,
            "hours": entry.hours,
            "project": entry.project,
            "interview_check": entry.interview_check,
        }
        for entry in entries
    ]


def job_for_prompt(job: Job) -> dict[str, str]:
    return {
        "job_id": job.id,
        "company": job.company,
        "title": job.title,
        "location_raw": job.location,
        "employment_type": job.employment_type,
        "posted_age_hours": _posted_age_hours(job.first_seen_at),
        "language": job.language,
        "description_full": job.description_full,
    }


def _posted_age_hours(first_seen_at: str) -> str:
    """A coarse freshness bucket, not a precise age.

    This value feeds `decision_cache_key`, so a continuously-changing exact
    hour count would invalidate the cache on every single re-run of the same
    job -- defeating the whole point of caching a decision. Bucketing means
    the cache key only changes when a job crosses a bucket boundary, which is
    the only time the agent's freshness judgment could plausibly change.
    Returns an empty string when the timestamp cannot be parsed, so the agent
    sees an honestly missing value rather than a fabricated one.
    """
    try:
        parsed = datetime.fromisoformat(first_seen_at.replace("Z", "+00:00"))
    except ValueError:
        return ""
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    age_hours = max((datetime.now(UTC) - parsed).total_seconds() / 3600, 0)
    if age_hours < 24:
        return "<24h"
    if age_hours < 72:
        return "1-3d"
    if age_hours < 168:
        return "3-7d"
    if age_hours < 720:
        return "1-4w"
    return ">4w"


def match_result_from_decision(
    job: Job,
    decision: AgentDecision,
    whitelist_map: dict[str, WhitelistEntry],
) -> MatchResult | None:
    if decision.variant is None:
        return None
    return MatchResult(
        job=job,
        variant=decision.variant,
        covered=set(decision.covered),
        addable=[whitelist_map[value] for value in decision.addable],
        true_gap=set(decision.true_gap),
        score=decision.score,
        core_fit=decision.core_fit,
        daily_work=decision.daily_work,
        rationale=decision.rationale,
        decision_source=decision.source,
    )


def screen_jobs_with_agent(
    jobs: list[Job],
    *,
    variants: dict[str, str],
    whitelist: list[WhitelistEntry],
    provider: str,
    model: str | None,
    custom_argv: list[str] | None,
    batch_size: int,
    workers: int,
    max_agent_calls: int,
    timeout_seconds: int,
    cache_root: Path,
    refresh_cache: bool,
    agent_cwd: Path,
    factual_profile: str = "",
    decision_source: str = "agent",
) -> tuple[list[MatchResult], dict[str, AgentDecision], list[str], int]:
    schema_text = AGENT_SCHEMA_PATH.read_text(encoding="utf-8")
    template_text = AGENT_PROMPT_PATH.read_text(encoding="utf-8")
    allowlist_payload = whitelist_for_prompt(whitelist)
    whitelist_map = {entry.match: entry for entry in whitelist}
    allowed_variants = set(variants)
    allowed_addable = set(whitelist_map)
    job_map = {job.id: job for job in jobs}
    decisions: dict[str, AgentDecision] = {}
    cache_keys: dict[str, str] = {}
    pending: list[Job] = []

    for job in jobs:
        prompt_job = job_for_prompt(job)
        cache_key = decision_cache_key(
            schema_text=schema_text,
            template_text=template_text,
            variants=variants,
            allowlist=allowlist_payload,
            job=prompt_job,
            factual_profile=factual_profile,
        )
        cache_keys[job.id] = cache_key
        cache_path = cache_root / f"{cache_key}.json"
        cached = None
        if not refresh_cache:
            cached = load_cached_decision(
                cache_path,
                allowed_variants=allowed_variants,
                allowed_addable=allowed_addable,
                requested_job_id=job.id,
            )
        if cached is None:
            pending.append(job)
        else:
            decisions[job.id] = cached

    errors: list[str] = []
    total_batches = (len(pending) + batch_size - 1) // batch_size
    print(
        f"Agent cache: {len(decisions)} hits, {len(pending)} pending in {total_batches} batch(es).",
        flush=True,
    )
    all_batches = [
        pending[start : start + batch_size] for start in range(0, len(pending), batch_size)
    ]
    scheduled_batches = all_batches[:max_agent_calls]
    for batch in all_batches[max_agent_calls:]:
        errors.extend(f"{job.id}: agent call budget exhausted" for job in batch)
    failed_batches: list[tuple[list[Job], AgentContractError]] = []

    def evaluate_batch(label: str, batch: list[Job]) -> list[AgentDecision]:
        print(
            f"  [agent] {label}; {len(batch)} job(s)",
            flush=True,
        )
        prompt_jobs = [job_for_prompt(job) for job in batch]
        prompt = build_prompt(
            template_text, variants, allowlist_payload, prompt_jobs, factual_profile
        )
        payload = run_agent(
            provider=provider,
            prompt=prompt,
            schema_path=AGENT_SCHEMA_PATH,
            cwd=agent_cwd,
            timeout_seconds=timeout_seconds,
            model=model,
            custom_argv=custom_argv,
        )
        return [
            replace(decision, source=decision_source)
            for decision in validate_batch(
                payload,
                requested_job_ids=[job.id for job in batch],
                allowed_variants=allowed_variants,
                allowed_addable=allowed_addable,
            )
        ]

    calls = len(scheduled_batches)
    worker_count = min(workers, max(1, len(scheduled_batches)))
    executor = ThreadPoolExecutor(max_workers=worker_count)
    quota_exhausted = False
    try:
        batch_iterator = iter(enumerate(scheduled_batches, start=1))
        futures: dict[Future[list[AgentDecision]], list[Job]] = {}

        def submit_next_batch() -> bool:
            try:
                index, batch = next(batch_iterator)
            except StopIteration:
                return False
            future = executor.submit(evaluate_batch, f"batch {index}/{total_batches}", batch)
            futures[future] = batch
            return True

        for _ in range(worker_count):
            submit_next_batch()

        while futures:
            completed, _ = wait(futures, return_when=FIRST_COMPLETED)
            for future in completed:
                batch = futures.pop(future)
                try:
                    batch_decisions = future.result()
                except AgentQuotaExceeded:
                    quota_exhausted = True
                    for pending_future in futures:
                        pending_future.cancel()
                    raise
                except AgentContractError as exc:
                    print(
                        f"    batch validation failed: {exc}; queued for individual retry",
                        flush=True,
                    )
                    failed_batches.append((batch, exc))
                else:
                    for decision in batch_decisions:
                        decisions[decision.job_id] = decision
                        store_cached_decision(
                            cache_root / f"{cache_keys[decision.job_id]}.json",
                            cache_keys[decision.job_id],
                            decision,
                        )
                submit_next_batch()
    finally:
        executor.shutdown(wait=not quota_exhausted, cancel_futures=quota_exhausted)

    for batch, batch_error in failed_batches:
        if len(batch) == 1:
            errors.append(f"{batch[0].id}: {batch_error}")
            continue
        for job in batch:
            if calls >= max_agent_calls:
                errors.append(f"{job.id}: agent call budget exhausted after batch failure")
                continue
            calls += 1
            print(
                f"    [agent] individual retry call {calls}/{max_agent_calls}",
                flush=True,
            )
            try:
                batch_decisions = evaluate_batch("individual retry", [job])
            except AgentQuotaExceeded:
                raise
            except AgentContractError as single_exc:
                errors.append(f"{job.id}: {single_exc}")
                continue
            for decision in batch_decisions:
                decisions[decision.job_id] = decision
                store_cached_decision(
                    cache_root / f"{cache_keys[decision.job_id]}.json",
                    cache_keys[decision.job_id],
                    decision,
                )

    results: list[MatchResult] = []
    for job in jobs:
        decision = decisions.get(job.id)
        if decision is None:
            continue
        result = match_result_from_decision(job_map[decision.job_id], decision, whitelist_map)
        if result is not None:
            results.append(result)
    return results, decisions, errors, calls


def tailor_matches_with_agent(
    matches: list[MatchResult],
    *,
    cv_root: Path,
    provider: str,
    model: str | None,
    custom_argv: list[str] | None,
    workers: int,
    max_agent_calls: int,
    timeout_seconds: int,
    cache_root: Path,
    refresh_cache: bool,
) -> tuple[dict[str, ResumeTailoring], dict[str, str], int]:
    """Create one evidence-bound, job-specific tailoring plan per selected job."""
    schema_text = TAILORING_SCHEMA_PATH.read_text(encoding="utf-8")
    template_text = TAILORING_PROMPT_PATH.read_text(encoding="utf-8")
    factual_profile = (cv_root / "shared" / "profile-notes.md").read_text(encoding="utf-8")
    evidence_cards = load_evidence_library(cv_root / "shared" / "evidence-library.json")
    evidence_payload = evidence_cards_for_prompt(evidence_cards)
    evidence_ids = tuple(card.evidence_id for card in evidence_cards)
    evidence_sources = tuple("\n".join(card.facts) for card in evidence_cards)
    plans: dict[str, ResumeTailoring] = {}
    errors: dict[str, str] = {}
    pending: list[
        tuple[MatchResult, str, list[dict[str, str]], tuple[str, ...], tuple[str, ...]]
    ] = []

    for match in matches:
        base_path = cv_root / "resume" / "variants" / f"{match.variant}.tex"
        base_tex = base_path.read_text(encoding="utf-8")
        catalog, _ = experience_catalog(base_tex)
        experience_ids = tuple(item["experience_id"] for item in catalog)
        factual_sources = (base_tex, factual_profile, *evidence_sources)
        decision = AgentDecision(
            job_id=match.job.id,
            variant=match.variant,
            core_fit=match.core_fit,
            daily_work=match.daily_work,
            covered=tuple(sorted(match.covered)),
            addable=tuple(entry.match for entry in match.addable),
            true_gap=tuple(sorted(match.true_gap)),
            score=match.score,
            rationale=match.rationale,
            source=match.decision_source,
        )
        cache_key = tailoring_cache_key(
            schema_text=schema_text,
            template_text=template_text,
            factual_profile=factual_profile,
            base_variant=base_tex,
            job=job_for_prompt(match.job),
            decision=decision,
            experience_ids=experience_ids,
            evidence_cards=evidence_payload,
        )
        cache_path = cache_root / f"{cache_key}.json"
        cached = None
        if not refresh_cache:
            cached = load_cached_tailoring(
                cache_path,
                requested_job_id=match.job.id,
                allowed_variant=match.variant,
                experience_ids=experience_ids,
                factual_sources=factual_sources,
                evidence_ids=evidence_ids,
                job_text=f"{match.job.title}\n{match.job.description_full}",
            )
        if cached is not None:
            plans[match.job.id] = cached
            continue
        pending.append((match, base_tex, catalog, experience_ids, factual_sources))

    print(
        f"Tailoring cache: {len(plans)} hits, {len(pending)} selected job(s) pending.",
        flush=True,
    )
    for match, *_ in pending[max_agent_calls:]:
        errors[match.job.id] = "tailoring agent call budget exhausted"
    scheduled = pending[:max_agent_calls]

    def tailor_one(
        item: tuple[MatchResult, str, list[dict[str, str]], tuple[str, ...], tuple[str, ...]],
    ) -> tuple[str, ResumeTailoring]:
        match, base_tex, catalog, experience_ids, factual_sources = item
        print(f"  [tailoring] {match.job.company} -- {match.job.title}", flush=True)
        decision = AgentDecision(
            job_id=match.job.id,
            variant=match.variant,
            core_fit=match.core_fit,
            daily_work=match.daily_work,
            covered=tuple(sorted(match.covered)),
            addable=tuple(entry.match for entry in match.addable),
            true_gap=tuple(sorted(match.true_gap)),
            score=match.score,
            rationale=match.rationale,
            source=match.decision_source,
        )
        prompt = build_tailoring_prompt(
            template_text,
            job=job_for_prompt(match.job),
            decision=decision,
            base_variant=_replace_skills_marker(base_tex, replacement=None),
            factual_profile=factual_profile,
            experiences=catalog,
            evidence_cards=evidence_payload,
        )
        payload = run_agent(
            provider=provider,
            prompt=prompt,
            schema_path=TAILORING_SCHEMA_PATH,
            cwd=cv_root,
            timeout_seconds=timeout_seconds,
            model=model,
            custom_argv=custom_argv,
        )
        tailoring = validate_tailoring(
            payload,
            requested_job_id=match.job.id,
            allowed_variant=match.variant,
            experience_ids=experience_ids,
            factual_sources=factual_sources,
            evidence_ids=evidence_ids,
            job_text=f"{match.job.title}\n{match.job.description_full}",
        )
        cache_key = tailoring_cache_key(
            schema_text=schema_text,
            template_text=template_text,
            factual_profile=factual_profile,
            base_variant=base_tex,
            job=job_for_prompt(match.job),
            decision=decision,
            experience_ids=experience_ids,
            evidence_cards=evidence_payload,
        )
        store_cached_tailoring(cache_root / f"{cache_key}.json", cache_key, tailoring)
        return match.job.id, tailoring

    worker_count = min(workers, max(1, len(scheduled)))
    executor = ThreadPoolExecutor(max_workers=worker_count)
    quota_exhausted = False
    try:
        item_iterator = iter(scheduled)
        futures: dict[Future[tuple[str, ResumeTailoring]], str] = {}

        def submit_next_tailoring() -> bool:
            try:
                item = next(item_iterator)
            except StopIteration:
                return False
            futures[executor.submit(tailor_one, item)] = item[0].job.id
            return True

        for _ in range(worker_count):
            submit_next_tailoring()

        while futures:
            completed, _ = wait(futures, return_when=FIRST_COMPLETED)
            for future in completed:
                job_id = futures.pop(future)
                try:
                    tailored_job_id, tailoring = future.result()
                except AgentQuotaExceeded:
                    quota_exhausted = True
                    for pending_future in futures:
                        pending_future.cancel()
                    raise
                except (AgentContractError, OSError, ValueError) as exc:
                    errors[job_id] = str(exc)
                else:
                    plans[tailored_job_id] = tailoring
                submit_next_tailoring()
    finally:
        executor.shutdown(wait=not quota_exhausted, cancel_futures=quota_exhausted)
    return plans, errors, len(scheduled)


def slugify(*parts: str) -> str:
    text = " ".join(p for p in parts if p).lower()
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    text = re.sub(r"-+", "-", text)
    return text[:60] or "job"


def application_slug(job: Job) -> str:
    base = slugify(job.company, job.title)[:49].rstrip("-") or "job"
    return f"{base}-{stable_job_hash(job)}"


def stable_job_hash(job: Job) -> str:
    return hashlib.sha256(stable_job_identity(job).encode()).hexdigest()[:10]


def filename_component(value: str, *, limit: int, fallback: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).strip()
    characters: list[str] = []
    pending_separator = False
    for character in normalized:
        if character.isalnum():
            if pending_separator and characters:
                characters.append("-")
            characters.append(character)
            pending_separator = False
        else:
            pending_separator = True
    component = "".join(characters).strip("-")[:limit].rstrip("-")
    return component or fallback


def fine_screen_pdf_name(job: Job, candidate: Candidate) -> str:
    company = filename_component(job.company, limit=32, fallback="Company")
    role = filename_component(job.title, limit=48, fallback="Role")
    return f"{candidate.file_slug}_CV_{company}_{role}.pdf"


def legacy_fine_screen_pdf_names(job: Job, candidate: Candidate) -> set[str]:
    """Names an earlier scheme produced, for recognition only.

    One per historical slug. These are never written -- they exist so that
    cleanup and replacement still find files generated before the current
    scheme, and archive them instead of orphaning them in the external
    workspace.
    """
    company = filename_component(job.company, limit=32, fallback="Company")
    role = filename_component(job.title, limit=48, fallback="Role")
    return {
        f"{company}__{role}__{stable_job_hash(job)}__{slug}_CV.pdf"
        for slug in candidate.legacy_file_slugs
    }


def fine_screen_batch_directory(cv_root: Path, batch_date: date | None = None) -> Path:
    """Return the dated output folder for one Fine Screen batch."""
    effective_date = batch_date or datetime.now(LOCAL_TIMEZONE).date()
    return cv_root / "CV" / "Fine-Screened" / effective_date.isoformat()


def fine_screen_latest_link(cv_root: Path) -> Path:
    return cv_root / "CV" / "Fine-Screened" / "latest"


def _is_directory_link(path: Path) -> bool:
    if path.is_symlink():
        return True
    attributes = getattr(path.lstat(), "st_file_attributes", 0)
    return bool(attributes & 0x400)  # FILE_ATTRIBUTE_REPARSE_POINT (Windows junction)


def fine_screen_pdf_path(
    cv_root: Path, job: Job, candidate: Candidate, batch_date: date | None = None
) -> Path:
    return fine_screen_batch_directory(cv_root, batch_date) / fine_screen_pdf_name(job, candidate)


def update_fine_screen_latest_link(cv_root: Path, batch_date: date) -> Path:
    """Point ``latest`` at a complete dated batch without copying the PDFs."""
    target = fine_screen_batch_directory(cv_root, batch_date)
    if not target.is_dir():
        raise RuntimeError(f"cannot point latest at a missing Fine Screen batch: {target}")
    latest = fine_screen_latest_link(cv_root)
    if latest.exists() or latest.is_symlink():
        if not _is_directory_link(latest):
            raise RuntimeError(f"refusing to replace non-link latest path: {latest}")
        if latest.is_symlink():
            latest.unlink()
        else:
            os.rmdir(latest)
    try:
        os.symlink(target, latest, target_is_directory=True)
    except OSError as exc:
        if os.name != "nt" or getattr(exc, "winerror", None) != 1314:
            raise

        def powershell_literal(path: Path) -> str:
            return "'" + str(path).replace("'", "''") + "'"

        command = (
            "$ErrorActionPreference = 'Stop'; "
            "New-Item -ItemType Junction -Path "
            f"{powershell_literal(latest)} -Target {powershell_literal(target)} | Out-Null"
        )
        encoded_command = base64.b64encode(command.encode("utf-16le")).decode("ascii")
        completed = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded_command],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            shell=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                "could not create latest Fine Screen junction: "
                f"{completed.stdout.strip()} {completed.stderr.strip()}"
            ) from exc
    return latest


def pdf_filename_collisions(
    jobs: Iterable[Job], candidate: Candidate
) -> dict[str, tuple[str, ...]]:
    """Return human-readable PDF names that would overwrite another selected job."""
    job_ids_by_name: dict[str, list[str]] = {}
    for job in jobs:
        job_ids_by_name.setdefault(fine_screen_pdf_name(job, candidate), []).append(job.id)
    return {name: tuple(job_ids) for name, job_ids in job_ids_by_name.items() if len(job_ids) > 1}


def generated_cv_names(job: Job, candidate: Candidate) -> set[str]:
    """Every name this screener may have written for one job, ever.

    Cleanup matches against the whole set, not just the current scheme, because
    a file left behind under an old name would never be archived again.
    """
    company = filename_component(job.company, limit=32, fallback="Company")
    role = filename_component(job.title, limit=48, fallback="Role")
    names = {fine_screen_pdf_name(job, candidate)}
    names |= legacy_fine_screen_pdf_names(job, candidate)
    for slug in candidate.legacy_file_slugs:
        names |= {
            f"{slug}_CV_{slugify(job.company, job.title)}.pdf",
            f"{slug}_CV_{application_slug(job)}.pdf",
            f"{slug}_CV_{company}_{role}.pdf",
            f"{application_slug(job)}__{slug}_CV.pdf",
        }
    return names


def notion_filename_key(name: str) -> str:
    return name.encode("ascii", errors="ignore").decode().casefold()


def generated_filename_matches(actual: str, expected_names: set[str]) -> bool:
    if actual in expected_names:
        return True
    actual_key = notion_filename_key(actual)
    return any(notion_filename_key(expected) == actual_key for expected in expected_names)


def _replace_skills_marker(tex: str, replacement: str | None) -> str:
    lines = tex.splitlines()
    marker_indexes = [
        index for index, line in enumerate(lines) if line.strip() == SKILLS_INSERTION_MARKER
    ]
    if len(marker_indexes) != 1:
        raise ValueError(
            "resume variant must contain exactly one skills insertion marker "
            f"({SKILLS_INSERTION_MARKER}); found {len(marker_indexes)}"
        )
    marker_index = marker_indexes[0]
    if replacement is None:
        del lines[marker_index]
    else:
        lines[marker_index] = replacement
    trailing_newline = "\n" if tex.endswith(("\n", "\r")) else ""
    return "\n".join(lines) + trailing_newline


def insert_skills_line(tex: str, addable: list[WhitelistEntry]) -> str:
    # The whitelist remains an interview-preparation aid and job-notes input.
    # JD coverage belongs inside evidence-led skill bullets, never in a synthetic line.
    del addable
    return _replace_skills_marker(tex, None)


_SECTION_PATTERN = re.compile(
    r"(?ms)^(?P<header>\\section\{(?P<title>[^}]*)\}\s*)(?P<body>.*?)(?=^\\section\{|^\\end\{document\})"
)
_EXPERIENCE_BLOCK_PATTERN = re.compile(
    r"(?ms)(?P<block>^\s*\\resumeSubheading\s*\n\s*\{(?P<employer>[^}]*)\}.*?\\resumeItemListEnd)"
)
_ITEM_LIST_PATTERN = re.compile(
    r"(?ms)(?P<prefix>\\resumeItemListStart\s*)(?P<body>.*?)(?P<suffix>\\resumeItemListEnd)"
)


def latex_escape_plain_text(text: str) -> str:
    replacements = {
        "\\": r"\textbackslash{}",
        "&": r"\&",
        "%": r"\%",
        "$": r"\$",
        "#": r"\#",
        "_": r"\_",
        "{": r"\{",
        "}": r"\}",
        "~": r"\textasciitilde{}",
        "^": r"\textasciicircum{}",
    }
    return "".join(replacements.get(character, character) for character in text)


def _replace_section_body(tex: str, titles: set[str], body: str) -> str:
    matches = [match for match in _SECTION_PATTERN.finditer(tex) if match.group("title") in titles]
    if len(matches) != 1:
        raise ValueError(
            f"expected exactly one section matching {sorted(titles)}; found {len(matches)}"
        )
    match = matches[0]
    return (
        tex[: match.start()] + match.group("header") + body.rstrip() + "\n\n" + tex[match.end() :]
    )


def _remove_sections(tex: str, titles: set[str]) -> str:
    matches = [match for match in _SECTION_PATTERN.finditer(tex) if match.group("title") in titles]
    for match in reversed(matches):
        tex = tex[: match.start()] + tex[match.end() :]
    return tex


def _trim_section_bullets(body: str, maximum: int) -> str:
    kept: list[str] = []
    bullet_count = 0
    for line in body.splitlines():
        if r"\resumeItemListStart" in line:
            bullet_count = 0
        if r"\resumeBullet{" in line:
            bullet_count += 1
            if bullet_count > maximum:
                continue
        kept.append(line)
    return "\n".join(kept)


def _drop_ai_noncore_experience(body: str) -> str:
    """Keep the visual-research, device-integration, and delivery evidence for compact AI CVs."""
    retained = "\n\n".join(
        block.group("block")
        for block in _EXPERIENCE_BLOCK_PATTERN.finditer(body)
        if block.group("employer").strip()
        not in {"Maxidepot", "Shanghai Kingstar Fintech Co., Ltd."}
    )
    return "  \\resumeSubHeadingListStart\n\n" + retained + "\n\n  \\resumeSubHeadingListEnd"


def compact_fine_screen_resume(tex: str) -> str:
    """Apply a truthful, readable one-page layout to generated Fine Screen CVs."""
    is_ai_variant = r"\newcommand{\resumeHeadline}{KI-Ingenieur}" in tex
    compact_degree_line = (
        r"\par\noindent{\scriptsize MSc Artificial Intelligence and Big Data Computing, "
        r"The Hong Kong Polytechnic University \textbar\ BSc Computer Science and Technology}"
        "\n\\vspace{1pt}"
    )
    tex = re.sub(
        r"(?:\\par)?\\noindent\{\\scriptsize MSc Artificial Intelligence and Big Data Computing, The Hong Kong Polytechnic University \\textbar\\ BSc Computer Science and Technology\}\s*\\vspace\{1pt\}\s*",
        "",
        tex,
    )
    tex = tex.replace(r"\resumeHeader", r"\resumeHeader" + "\n" + compact_degree_line, 1)
    tex = _remove_sections(
        tex, {"Education", "Additional Experience", "Ausbildung", "Weitere Erfahrung"}
    )
    if is_ai_variant:
        tex = _remove_sections(tex, {"Publikation", "Publication"})
        tex = re.sub(
            r"\n\s*\\item \\textbf\{(?:Experimente|Datenqualität):\}[^\n]*",
            "",
            tex,
        )
    tex = tex.replace(
        r"\documentclass[a4paper,11pt]{article}", r"\documentclass[a4paper,10pt]{article}", 1
    )
    if r"\addtolength{\textheight}{0.25in}" not in tex:
        tex = tex.replace(
            r"\begin{document}", r"\begin{document}" + "\n\\addtolength{\\textheight}{0.25in}", 1
        )
    for title in ("Experience", "Berufserfahrung"):
        matches = [
            match for match in _SECTION_PATTERN.finditer(tex) if match.group("title") == title
        ]
        if len(matches) == 1:
            body = matches[0].group("body")
            if is_ai_variant:
                body = _drop_ai_noncore_experience(body)
            tex = _replace_section_body(tex, {title}, _trim_section_bullets(body, 2))
    if is_ai_variant:
        for title in ("Summary", "Profile", "Zusammenfassung", "Profil"):
            matches = [
                match for match in _SECTION_PATTERN.finditer(tex) if match.group("title") == title
            ]
            if len(matches) == 1:
                tex = _replace_section_body(
                    tex, {title}, _trim_section_bullets(matches[0].group("body"), 1)
                )
    for title in ("Projects", "Projekte"):
        matches = [
            match for match in _SECTION_PATTERN.finditer(tex) if match.group("title") == title
        ]
        if len(matches) == 1:
            tex = _replace_section_body(
                tex, {title}, _trim_section_bullets(matches[0].group("body"), 1)
            )
    tex = re.sub(r"\n\s*\\item \\textbf\{Currently building fluency:\}[^\n]*", "", tex)
    return tex


def compact_cpp_one_page_resume(tex: str) -> str:
    """Backward-compatible entry point for the C++ reflow script."""
    return compact_fine_screen_resume(tex)


def experience_catalog(tex: str) -> tuple[list[dict[str, str]], dict[str, str]]:
    matches = [
        match
        for match in _SECTION_PATTERN.finditer(tex)
        if match.group("title") in {"Experience", "Berufserfahrung"}
    ]
    if len(matches) != 1:
        raise ValueError(
            "resume variant must contain exactly one Experience/Berufserfahrung section"
        )
    blocks = list(_EXPERIENCE_BLOCK_PATTERN.finditer(matches[0].group("body")))
    if not blocks:
        raise ValueError("Experience/Berufserfahrung section must contain resumeSubheading blocks")
    catalog: list[dict[str, str]] = []
    by_id: dict[str, str] = {}
    for index, block in enumerate(blocks, start=1):
        experience_id = f"experience-{index}"
        employer = block.group("employer").strip()
        catalog.append({"experience_id": experience_id, "employer": employer})
        by_id[experience_id] = block.group("block")
    return catalog, by_id


def _render_bullets(bullets: tuple[TailoringText, ...]) -> str:
    return "\n".join(
        f"        \\resumeBullet{{{latex_escape_plain_text(bullet.text)}}}" for bullet in bullets
    )


def _replace_experience_bullets(block: str, bullets: tuple[TailoringText, ...]) -> str:
    matches = list(_ITEM_LIST_PATTERN.finditer(block))
    if len(matches) != 1:
        raise ValueError("each experience block must contain exactly one resumeItemList")
    match = matches[0]
    return (
        block[: match.start()]
        + match.group("prefix")
        + "\n"
        + _render_bullets(bullets)
        + "\n      "
        + match.group("suffix")
        + block[match.end() :]
    )


def _render_projects(projects: tuple[TailoredProject, ...], cards: tuple[EvidenceCard, ...]) -> str:
    cards_by_id = {card.evidence_id: card for card in cards}
    blocks: list[str] = []
    for project in projects:
        card = cards_by_id.get(project.evidence_id)
        if card is None:
            raise ValueError(
                f"tailoring project has no matching evidence card: {project.evidence_id}"
            )
        blocks.append(
            "    \\resumeSubheading\n"
            f"      {{{latex_escape_plain_text(card.organization)}}}{{{latex_escape_plain_text(card.location)}}}\n"
            f"      {{{latex_escape_plain_text(card.role)}}}{{{latex_escape_plain_text(card.dates)}}}\n"
            "      \\resumeItemListStart\n"
            f"{_render_bullets(project.bullets)}\n"
            "      \\resumeItemListEnd"
        )
    return (
        "  \\resumeSubHeadingListStart\n\n"
        + "\n\n".join(blocks)
        + "\n\n  \\resumeSubHeadingListEnd"
    )


def _insert_projects_section(tex: str, projects_body: str) -> str:
    """Insert a Projects section before education when a variant has no project slot."""
    german = any(
        section.group("title") in {"Ausbildung", "Berufserfahrung", "Kenntnisse"}
        for section in _SECTION_PATTERN.finditer(tex)
    )
    title = "Projekte" if german else "Projects"
    section = f"\\section{{{title}}}\n{projects_body}\n\n"
    education_sections = [
        match
        for match in _SECTION_PATTERN.finditer(tex)
        if match.group("title") in {"Education", "Ausbildung"}
    ]
    if len(education_sections) == 1:
        match = education_sections[0]
        return tex[: match.start()] + section + tex[match.start() :]
    return tex.replace(r"\end{document}", section + r"\end{document}", 1)


def render_tailored_resume(
    base_tex: str,
    tailoring: ResumeTailoring,
    evidence_cards: tuple[EvidenceCard, ...] = (),
) -> str:
    if (
        r"\documentclass" not in base_tex
        or base_tex.count(r"\begin{document}") != 1
        or base_tex.count(r"\end{document}") != 1
    ):
        raise ValueError("base resume must be a complete LaTeX document")
    catalog, blocks = experience_catalog(base_tex)
    experience_ids = tuple(item["experience_id"] for item in catalog)
    if len(tailoring.experience_order) != len(experience_ids) or set(
        tailoring.experience_order
    ) != set(experience_ids):
        raise ValueError("tailoring experience order does not match the base resume")
    bullets_by_id = {item.experience_id: item.bullets for item in tailoring.experiences}
    if set(bullets_by_id) != set(experience_ids):
        raise ValueError("tailoring experiences do not match the base resume")
    summary_body = (
        "  \\resumeItemListStart\n" + _render_bullets(tailoring.summary) + "\n  \\resumeItemListEnd"
    )
    ramp_up_by_label: dict[str, list[str]] = {}
    for group in tailoring.ramp_up:
        ramp_up_by_label.setdefault(group.label.casefold(), []).extend(group.terms)
    skill_lines = [" \\resumeSubHeadingListStart"]
    for group in tailoring.skills:
        terms = ramp_up_by_label.get(group.label.casefold(), [])
        additions = [term for term in terms if term.casefold() not in group.text.casefold()]
        text = ", ".join([group.text, *additions])
        skill_lines.append(
            f"   \\item \\textbf{{{latex_escape_plain_text(group.label)}:}} "
            f"{latex_escape_plain_text(text)}"
        )
    skill_lines.extend([f"{SKILLS_INSERTION_MARKER}", " \\resumeSubHeadingListEnd"])
    ordered_blocks = [
        _replace_experience_bullets(blocks[experience_id], bullets_by_id[experience_id])
        for experience_id in tailoring.experience_order
    ]
    experience_body = "  \\resumeSubHeadingListStart\n\n" + "\n\n".join(ordered_blocks)
    experience_body += "\n\n  \\resumeSubHeadingListEnd"
    rendered = _replace_section_body(
        base_tex, {"Summary", "Profile", "Profil", "Zusammenfassung"}, summary_body
    )
    rendered = _replace_section_body(rendered, {"Skills", "Kenntnisse"}, "\n".join(skill_lines))
    rendered = _replace_section_body(rendered, {"Experience", "Berufserfahrung"}, experience_body)
    project_titles = {"Projects", "Projekte"}
    if tailoring.projects:
        projects_body = _render_projects(tailoring.projects, evidence_cards)
        if any(
            section.group("title") in project_titles
            for section in _SECTION_PATTERN.finditer(rendered)
        ):
            rendered = _replace_section_body(rendered, project_titles, projects_body)
        else:
            rendered = _insert_projects_section(rendered, projects_body)
    if r"\newcommand{\resumeHeadline}{KI-Ingenieur}" in rendered:
        rendered = rendered.replace(
            r"\begin{document}", r"\begin{document}" + "\n\\addtolength{\\topmargin}{0.45in}", 1
        )
    return rendered


def build_job_notes(company: str, role: str, match: MatchResult) -> str:
    job = match.job
    covered_line = ", ".join(sorted(match.covered)) or "(none)"
    gap_lines = "\n".join(f"- {kw}" for kw in sorted(match.true_gap)) or "- (none)"
    addable_lines = (
        "\n".join(
            (
                f'- **{e.match}** -> resume: "{e.phrase}" (~{e.hours:g}h)\n'
                f"  - Project: {e.project or '(define before interview)'}\n"
                f"  - Interview-ready check: {e.interview_check or '(define before interview)'}"
            )
            for e in match.addable
        )
        or "- (none -- fill in shared/quick-learn-skills.toml to enable this)"
    )
    ramp_up_lines = (
        "\n".join(
            f"- **{group.label}**: {', '.join(group.terms)}"
            for group in (match.tailoring.ramp_up if match.tailoring else ())
        )
        or "- (none)"
    )
    notion_url = (
        f"https://www.notion.so/{job.notion_page_id.replace('-', '')}"
        if job.notion_page_id
        else "N/A"
    )
    return f"""# {company} - {role}

## Job

- URL: {job.url}
- Location: {job.location or "N/A"}
- Language: {job.language or "N/A"}
- Application date: {datetime.now().strftime("%Y-%m-%d")}
- Resume variant: {match.variant} (fine-screen score {match.score:.2f})
- Core fit: {match.core_fit}
- Decision source: {match.decision_source}
- Notion page: {notion_url}
- Track: {job.track}

## What the job actually does

{match.daily_work}

## Agent rationale

{match.rationale}

## Strongest matches

-
-
-

## Gaps to handle honestly

{gap_lines}

## Company-specific motivation

-

## Fine-screen detail (auto-generated -- review before building/sending)

- Already covered by resume: {covered_line}
- JD keywords intentionally surfaced for your review (not claimed as prior employment):
{ramp_up_lines}
- Added to this resume from the quick-learn whitelist (start the project immediately and learn before the interview):
{addable_lines}
"""


def deepen_includes(tex: str) -> str:
    """resume/variants/applications/<slug>.tex sits one directory deeper than
    resume/variants/<direction>.tex, so its \\input paths need one extra
    '../' to still resolve to shared/ and resume/shared/. contact.tex also
    hardcodes \\candidatePhoto as a path relative to resume/variants/, so it
    resolves to a nonexistent file at the deeper path and \\IfFileExists
    silently drops the photo -- override it after the input so the header
    still finds assets/profile-photo.jpg."""
    tex = tex.replace(r"\input{../../shared/contact.tex}", r"\input{../../../shared/contact.tex}")
    tex = tex.replace(
        r"\input{../shared/resume-style.tex}", r"\input{../../shared/resume-style.tex}"
    )
    tex = tex.replace(
        r"\input{../../../shared/contact.tex}",
        r"\input{../../../shared/contact.tex}"
        "\n"
        r"\renewcommand{\candidatePhoto}{../../../assets/profile-photo.jpg}",
    )
    return tex


def find_tectonic(cv_root: Path) -> Path | None:
    bundled = cv_root / "vendor" / "tectonic" / "tectonic.exe"
    if bundled.exists():
        return bundled
    found = shutil.which("tectonic")
    return Path(found) if found else None


def build_pdf(tectonic: Path, source: Path, build_dir: Path, destination: Path) -> bool:
    build_dir.mkdir(parents=True, exist_ok=True)
    destination.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [str(tectonic), "--keep-logs", "--outdir", str(build_dir), source.name],
        cwd=source.parent,
        capture_output=True,
        text=True,
    )
    built_pdf = build_dir / (source.stem + ".pdf")
    if result.returncode != 0 or not built_pdf.exists():
        return False
    shutil.copyfile(built_pdf, destination)
    return True


def archive_file(path: Path, cv_root: Path, archive_root: Path) -> None:
    if not path.is_file():
        return
    resolved_root = cv_root.resolve()
    resolved_path = path.resolve()
    try:
        relative = resolved_path.relative_to(resolved_root)
    except ValueError as exc:
        raise RuntimeError(
            f"refusing to archive file outside CV workspace: {resolved_path}"
        ) from exc
    destination = archive_root / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(resolved_path), str(destination))


def create_application(
    cv_root: Path,
    slug: str,
    company: str,
    role: str,
    location: str,
    match: MatchResult,
    tailoring: ResumeTailoring,
    candidate: Candidate,
    *,
    replace_existing: bool,
    archive_root: Path,
    batch_date: date | None = None,
) -> tuple[Path, bool] | None:
    """Generates only the tailored CV, not a cover letter -- most companies in
    this pipeline's job mix don't ask for one, so cover.tex/cover.pdf are no
    longer produced. job-notes.md is still written for a quick per-application
    reference (strongest matches, gaps) even without a cover letter to draft."""
    app_dir = cv_root / "cover-letter" / "applications" / slug
    notes_path = app_dir / "job-notes.md"
    resume_path = cv_root / "resume" / "variants" / "applications" / f"{slug}.tex"
    pdf_path = fine_screen_pdf_path(cv_root, match.job, candidate, batch_date)
    notes_content = build_job_notes(company, role, match)
    base_variant_tex = (cv_root / "resume" / "variants" / f"{match.variant}.tex").read_text(
        encoding="utf-8"
    )
    evidence_cards = load_evidence_library(cv_root / "shared" / "evidence-library.json")
    resume_tex = deepen_includes(
        insert_skills_line(
            render_tailored_resume(base_variant_tex, tailoring, evidence_cards), match.addable
        )
    )
    current_outputs_match = bool(
        notes_path.is_file()
        and resume_path.is_file()
        and pdf_path.is_file()
        and notes_path.read_text(encoding="utf-8") == notes_content
        and resume_path.read_text(encoding="utf-8") == resume_tex
    )
    if current_outputs_match and not replace_existing:
        return None

    if replace_existing or notes_path.exists() or resume_path.exists() or pdf_path.exists():
        for path in (notes_path, resume_path, pdf_path):
            archive_file(path, cv_root, archive_root)

    app_dir.mkdir(parents=True, exist_ok=True)
    notes_path.write_text(notes_content, encoding="utf-8")
    resume_path.parent.mkdir(parents=True, exist_ok=True)
    resume_path.write_text(resume_tex, encoding="utf-8")

    resume_pdf_ok = False
    tectonic = find_tectonic(cv_root)
    if tectonic:
        resume_pdf_ok = build_pdf(
            tectonic,
            resume_path,
            cv_root / ".build" / "resumes" / "applications" / slug,
            pdf_path,
        )
    return app_dir, resume_pdf_ok


def notion_request(token: str, endpoint: str, method: str, body: dict | None = None) -> dict:
    payload = json.dumps(body).encode("utf-8") if body is not None else None
    last_error: Exception | None = None
    for attempt in range(4):
        request = Request(endpoint, method=method, data=payload)
        request.add_header("Authorization", f"Bearer {token}")
        request.add_header("Content-Type", "application/json")
        request.add_header("Notion-Version", NOTION_VERSION)
        try:
            with urlopen(request, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            details = exc.read().decode("utf-8", errors="replace")
            if exc.code in {429, 500, 502, 503, 504} and attempt < 3:
                time.sleep(attempt + 1)
                last_error = RuntimeError(f"Notion API {exc.code}: {details}")
                continue
            raise RuntimeError(f"Notion API {exc.code}: {details}") from exc
        except (URLError, ConnectionError) as exc:
            last_error = exc
            if attempt < 3:
                time.sleep(attempt + 1)
                continue
            raise
    raise RuntimeError(f"Notion request failed after retries: {last_error}")


def list_page_file_blocks(token: str, page_id: str) -> list[NotionFileBlock]:
    """Existing 'file' blocks on the page, by filename -- lets attach_application_pdfs
    skip re-uploading a PDF that's already attached instead of duplicating it on rerun."""
    blocks: list[NotionFileBlock] = []
    cursor = ""
    while True:
        suffix = f"&start_cursor={cursor}" if cursor else ""
        response = notion_request(
            token,
            f"https://api.notion.com/v1/blocks/{page_id}/children?page_size=100{suffix}",
            "GET",
        )
        for block in response.get("results", []):
            if block.get("type") != "file":
                continue
            name = (block.get("file") or {}).get("name") or ""
            block_id = block.get("id") or ""
            if name and block_id:
                blocks.append(NotionFileBlock(str(block_id), str(name)))
        if not response.get("has_more"):
            return blocks
        cursor = str(response.get("next_cursor") or "")
        if not cursor:
            return blocks


def list_page_file_block_names(token: str, page_id: str) -> set[str]:
    return {block.name for block in list_page_file_blocks(token, page_id)}


def fine_screened_page_ids(token: str, data_source_ids: set[str]) -> set[str]:
    page_ids: set[str] = set()
    for data_source_id in sorted(value for value in data_source_ids if value):
        cursor = ""
        while True:
            body: dict[str, object] = {
                "page_size": 100,
                "filter": {
                    "property": SCREEN_PROPERTY,
                    "select": {"equals": SCREEN_TAG},
                },
            }
            if cursor:
                body["start_cursor"] = cursor
            try:
                response = notion_request(
                    token,
                    f"https://api.notion.com/v1/data_sources/{data_source_id}/query",
                    "POST",
                    body,
                )
            except RuntimeError as exc:
                if f"Could not find property with name or id: {SCREEN_PROPERTY}" in str(exc):
                    break
                raise
            page_ids.update(
                str(page["id"]) for page in response.get("results", []) if page.get("id")
            )
            if not response.get("has_more"):
                break
            cursor = str(response.get("next_cursor") or "")
            if not cursor:
                break
    return page_ids


def inspect_existing_fine_screened(
    token: str, jobs: list[Job], candidate: Candidate
) -> tuple[list[Job], int]:
    page_ids = fine_screened_page_ids(
        token,
        {job.notion_data_source_id for job in jobs},
    )
    tagged_jobs = [job for job in jobs if job.notion_page_id in page_ids]
    attachment_count = 0
    for job in tagged_jobs:
        expected_names = generated_cv_names(job, candidate)
        attachment_count += sum(
            generated_filename_matches(block.name, expected_names)
            for block in list_page_file_blocks(token, job.notion_page_id)
        )
    return tagged_jobs, attachment_count


def clear_screen_tag(token: str, page_id: str) -> None:
    notion_request(
        token,
        f"https://api.notion.com/v1/pages/{page_id}",
        "PATCH",
        {"properties": {SCREEN_PROPERTY: {"select": None}}},
    )


def clear_fine_screened_jobs(
    token: str, tagged_jobs: list[Job], candidate: Candidate
) -> tuple[int, int]:
    archived_blocks = 0
    for job in tagged_jobs:
        expected_names = generated_cv_names(job, candidate)
        blocks = [
            block
            for block in list_page_file_blocks(token, job.notion_page_id)
            if generated_filename_matches(block.name, expected_names)
        ]
        for block in blocks:
            notion_request(
                token,
                f"https://api.notion.com/v1/blocks/{block.block_id}",
                "DELETE",
            )
            archived_blocks += 1
        clear_screen_tag(token, job.notion_page_id)
    return len(tagged_jobs), archived_blocks


def reset_existing_fine_screened(
    token: str, jobs: list[Job], candidate: Candidate
) -> tuple[int, int]:
    tagged_jobs, _attachment_count = inspect_existing_fine_screened(token, jobs, candidate)
    return clear_fine_screened_jobs(token, tagged_jobs, candidate)


def clear_unselected_fine_screened(
    token: str,
    jobs: list[Job],
    selected_job_ids: set[str],
    candidate: Candidate,
) -> tuple[int, int]:
    tagged_jobs, _attachment_count = inspect_existing_fine_screened(token, jobs, candidate)
    stale_jobs = [job for job in tagged_jobs if job.id not in selected_job_ids]
    return clear_fine_screened_jobs(token, stale_jobs, candidate)


def notion_create_file_upload(token: str, filename: str) -> tuple[str, str]:
    response = notion_request(
        token, "https://api.notion.com/v1/file_uploads", "POST", {"filename": filename}
    )
    return str(response["id"]), str(response["upload_url"])


def notion_send_file_bytes(token: str, upload_url: str, filename: str, data: bytes) -> None:
    boundary = "FineScreenBoundary7f3a9c2e"
    body = (
        (
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\n'
            f"Content-Type: application/pdf\r\n\r\n"
        ).encode()
        + data
        + f"\r\n--{boundary}--\r\n".encode()
    )
    last_error: Exception | None = None
    for attempt in range(4):
        request = Request(upload_url, method="POST", data=body)
        request.add_header("Authorization", f"Bearer {token}")
        request.add_header("Notion-Version", NOTION_VERSION)
        request.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
        try:
            with urlopen(request, timeout=60) as response:
                response.read()
                return
        except (HTTPError, URLError, ConnectionError, OSError) as exc:
            last_error = exc
            if attempt < 3:
                time.sleep(attempt + 1)
                continue
            raise RuntimeError(f"Notion file upload failed after retries: {last_error}") from exc


def notion_attach_file_block(token: str, page_id: str, upload_id: str, caption: str) -> None:
    notion_request(
        token,
        f"https://api.notion.com/v1/blocks/{page_id}/children",
        "PATCH",
        {
            "children": [
                {
                    "object": "block",
                    "type": "file",
                    "file": {
                        "type": "file_upload",
                        "file_upload": {"id": upload_id},
                        "caption": [{"type": "text", "text": {"content": caption}}]
                        if caption
                        else [],
                    },
                }
            ]
        },
    )


def attach_one_pdf(
    token: str, page_id: str, path: Path, existing_names: set[str], caption: str
) -> bool:
    if generated_filename_matches(path.name, existing_names):
        return True
    if not path.exists():
        return False
    try:
        upload_id, upload_url = notion_create_file_upload(token, path.name)
        notion_send_file_bytes(token, upload_url, path.name, path.read_bytes())
        notion_attach_file_block(token, page_id, upload_id, caption)
        return True
    except (RuntimeError, HTTPError, URLError, OSError) as exc:
        print(f"    Notion attach failed for {path.name}: {exc}")
        return False


def attach_application_pdfs(
    token: str,
    page_id: str,
    cv_root: Path,
    job: Job,
    candidate: Candidate,
    *,
    replace_existing: bool = False,
    batch_date: date | None = None,
) -> bool:
    """Attach the generated CV directly onto the job's Notion page, so opening
    the Fine-screened entry shows the matching file immediately instead of
    requiring a manual search through CV/ by filename."""
    cv_pdf = fine_screen_pdf_path(cv_root, job, candidate, batch_date)
    blocks = list_page_file_blocks(token, page_id)
    if replace_existing:
        replace_names = generated_cv_names(job, candidate)
        old_blocks = [
            block for block in blocks if generated_filename_matches(block.name, replace_names)
        ]
        if not attach_one_pdf(token, page_id, cv_pdf, set(), "CV"):
            return False
        for block in old_blocks:
            notion_request(
                token,
                f"https://api.notion.com/v1/blocks/{block.block_id}",
                "DELETE",
            )
        return True
    return attach_one_pdf(token, page_id, cv_pdf, {block.name for block in blocks}, "CV")


def ensure_screen_property(token: str, data_source_id: str, ensured: set[str]) -> None:
    if not data_source_id or data_source_id in ensured:
        return
    notion_request(
        token,
        f"https://api.notion.com/v1/data_sources/{data_source_id}",
        "PATCH",
        {
            "properties": {
                SCREEN_PROPERTY: {
                    "select": {
                        "options": [
                            {"name": name, "color": color} for name, color in SCREEN_OPTIONS.items()
                        ]
                    }
                }
            }
        },
    )
    ensured.add(data_source_id)


def tag_notion_page(token: str, page_id: str, tag: str = SCREEN_TAG) -> None:
    if tag not in SCREEN_OPTIONS:
        raise ValueError(f"unsupported Fine Screen status: {tag}")
    notion_request(
        token,
        f"https://api.notion.com/v1/pages/{page_id}",
        "PATCH",
        {"properties": {SCREEN_PROPERTY: {"select": {"name": tag}}}},
    )


def finalized_screen_tag(
    job_id: str,
    *,
    selected_ids: set[str],
    errors: dict[str, str],
    selection_blocks: dict[str, str],
) -> str:
    if job_id in errors:
        return SCREEN_ERROR_TAG
    if job_id in selection_blocks:
        return SCREEN_BLOCKED_TAG
    if job_id in selected_ids:
        return SCREEN_TAG
    return SCREEN_REJECTED_TAG


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be positive")
    return parsed


def iso_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected YYYY-MM-DD") from exc


def resolve_date_window(args: argparse.Namespace) -> tuple[list[str], str]:
    """Translate this screener's date options into feed options plus a label.

    The window itself is the feed's to compute -- it knows each profile's
    timezone, and duplicating the arithmetic here is how the two sides drift.
    What stays local is the label, because it names this screener's report
    files.

    `--since-date` without `--until-date` has always meant "through today", so
    that end is made explicit rather than left to the feed, whose own default
    for a bare start date is that single day.
    """
    if args.since_date:
        until_date = args.until_date or datetime.now(LOCAL_TIMEZONE).date()
        if until_date < args.since_date:
            raise ValueError("--until-date must be on or after --since-date")
        return (
            [
                "--since-date",
                args.since_date.isoformat(),
                "--until-date",
                until_date.isoformat(),
            ],
            f"{args.since_date.isoformat()}_{until_date.isoformat()}",
        )
    if args.until_date:
        raise ValueError("--until-date requires --since-date")
    since_days = args.since_days or 4
    return ["--since-days", str(since_days)], f"last-{since_days}-days"


def parse_custom_argv(value: str | None) -> list[str] | None:
    if value is None:
        return None
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError("--agent-command-json must be a JSON argv array") from exc
    if (
        not isinstance(parsed, list)
        or not parsed
        or not all(isinstance(item, str) and item for item in parsed)
    ):
        raise ValueError("--agent-command-json must be a non-empty JSON array of strings")
    return parsed


def parse_job_scraper_argv(value: str | None) -> list[str]:
    if value is None:
        return list(DEFAULT_JOB_SCRAPER_ARGV)
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError("--job-scraper-command-json must be a JSON argv array") from exc
    if (
        not isinstance(parsed, list)
        or not parsed
        or not all(isinstance(item, str) and item for item in parsed)
    ):
        raise ValueError("--job-scraper-command-json must be a non-empty JSON array of strings")
    return parsed


def run_positions_pull(
    positions_root: Path | None, job_scraper_argv: list[str], pull_args: list[str]
) -> int:
    argv = [*job_scraper_argv, "run", *pull_args]
    location = str(positions_root) if positions_root is not None else "installed command"
    print(f"Running Positions acquisition from {location} ...")
    completed = subprocess.run(argv, cwd=positions_root, shell=False, check=False)
    if completed.returncode != 0:
        print(
            f"ERROR Positions acquisition exited {completed.returncode}; fine-screen not started."
        )
    return completed.returncode


def stable_job_identity(job: Job) -> str:
    if job.url:
        linkedin_job_id = re.search(
            r"linkedin\.com/.*?(\d{8,})(?:[/?&#]|$)", job.url, re.IGNORECASE
        )
        if linkedin_job_id:
            return f"linkedin:{linkedin_job_id.group(1)}"
        return f"url:{job.url.strip().casefold()}"
    return "fallback:" + "|".join(
        value.strip().casefold() for value in (job.company, job.title, job.location)
    )


_ROLE_LIKE_COMPANY = re.compile(
    r"^(?:(?:senior|junior|staff|principal|lead|graduate|founding)\s+)?"
    r"(?:software|ai|data|machine learning|it|systems?|backend|frontend|full[- ]?stack|"
    r"devops|qa|test|embedded|cloud|platform|application)\s+"
    r"(?:engineer|developer|administrator|architect|scientist|manager)"
    r"(?:\s+(?:ii|iii|iv|\d+))?$",
    re.IGNORECASE,
)


def company_metadata_issue(job: Job) -> str:
    company = job.company.strip()
    folded = company.casefold()
    if not company or folded in {"unknown", "unknown company", "n/a", "none"}:
        return "missing company identity"
    if folded.startswith(("http://", "https://", "www.")):
        return "company field contains a URL"
    if re.match(r"^\d{4}\s+intake\b", company, re.IGNORECASE):
        return "company field contains an intake label"
    if _ROLE_LIKE_COMPANY.fullmatch(company):
        return "company field looks like a job title"
    return ""


def selection_issue(job: Job) -> str:
    metadata_issue = company_metadata_issue(job)
    if metadata_issue:
        return metadata_issue
    if not job.description_full.strip():
        return "missing job description"
    return ""


def selection_block_reason(job: Job, agent_errors: dict[str, str]) -> str:
    """Return why a job must not enter the generation selection.

    A failed or budget-exhausted judgment excludes that specific job. It must
    not prevent independently validated matches from being generated.
    """

    return agent_errors.get(job.id) or selection_issue(job)


_CPP_TITLE_SIGNAL = re.compile(r"(?:c\+\+|\bqt\b|\bembedded\s+c\b)", re.IGNORECASE)


def decision_needs_refinement(job: Job, decision: AgentDecision, min_score: float) -> bool:
    if "refined" in decision.source:
        return False
    is_current_candidate = bool(
        decision.variant and decision.score >= min_score and not selection_issue(job)
    )
    is_near_miss = max(0.30, min_score - 0.05) <= decision.score < min_score
    if is_current_candidate or is_near_miss:
        return True
    return bool(
        _CPP_TITLE_SIGNAL.search(job.title)
        and (decision.variant is None or not decision.variant.startswith("cpp-"))
    )


def summarize_decision_sources(
    decisions: dict[str, AgentDecision],
) -> dict[str, int]:
    summary = {
        "fresh_base": 0,
        "fresh_refined": 0,
        "cache_hits": 0,
        "deduplicated": 0,
        "other": 0,
    }
    for decision in decisions.values():
        source = decision.source
        if source.startswith("deduplicated:"):
            summary["deduplicated"] += 1
        elif source == "agent":
            summary["fresh_base"] += 1
        elif source == "agent-refined":
            summary["fresh_refined"] += 1
        elif source.startswith("cache:"):
            summary["cache_hits"] += 1
        else:
            summary["other"] += 1
    return summary


def is_selection_candidate(match: MatchResult, min_score: float) -> bool:
    return match.score >= min_score and not selection_issue(match.job)


def deduplicate_jobs(jobs: list[Job]) -> tuple[list[Job], dict[str, str]]:
    unique: list[Job] = []
    primary_by_identity: dict[str, str] = {}
    duplicate_of: dict[str, str] = {}
    for job in jobs:
        identity = stable_job_identity(job)
        primary_id = primary_by_identity.get(identity)
        if primary_id is None:
            primary_by_identity[identity] = job.id
            unique.append(job)
        else:
            duplicate_of[job.id] = primary_id
    return unique, duplicate_of


def write_agent_report(
    path: Path,
    jobs: list[Job],
    decisions: dict[str, AgentDecision],
    selected_ids: set[str],
    errors: dict[str, str],
    selection_blocks: dict[str, str],
    tailorings: dict[str, ResumeTailoring] | None = None,
    release_identity: ReleaseIdentity | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "selected",
                "status",
                "processing_mode",
                "score",
                "core_fit",
                "company",
                "title",
                "variant",
                "covered",
                "addable",
                "true_gap",
                "daily_work",
                "rationale",
                "decision_source",
                "tailoring_source",
                "first_seen_at",
                "url",
                "selection_block",
                "error",
                "fine_screen_revision",
                "workspace_revision",
                "workspace_manifest_sha256",
            ]
        )
        for job in jobs:
            decision = decisions.get(job.id)
            status = "error"
            if job.id not in errors:
                if job.processing_mode == "discovery":
                    status = "retained"
                elif decision is None or decision.variant is None:
                    status = "no_match"
                elif job.id in selection_blocks:
                    status = "metadata_blocked"
                else:
                    status = "evaluated"
            writer.writerow(
                [
                    job.id in selected_ids,
                    status,
                    job.processing_mode,
                    f"{decision.score:.2f}" if decision else "",
                    decision.core_fit if decision else "",
                    job.company,
                    job.title,
                    decision.variant or "" if decision else "",
                    "; ".join(decision.covered) if decision else "",
                    "; ".join(decision.addable) if decision else "",
                    "; ".join(decision.true_gap) if decision else "",
                    decision.daily_work if decision else "",
                    decision.rationale if decision else "",
                    decision.source if decision else "",
                    tailorings[job.id].source if tailorings and job.id in tailorings else "",
                    job.first_seen_at,
                    job.url,
                    selection_blocks.get(job.id, ""),
                    errors.get(job.id, ""),
                    release_identity.code_revision if release_identity else "",
                    release_identity.workspace_revision if release_identity else "",
                    release_identity.workspace_manifest_sha256 if release_identity else "",
                ]
            )


def write_screening_result_document(
    path: Path,
    jobs: list[Job],
    decisions: dict[str, AgentDecision],
    selected_ids: set[str],
    errors: dict[str, str],
    selection_blocks: dict[str, str],
    tailorings: dict[str, ResumeTailoring],
) -> None:
    """Write the versioned handoff that Positions persists before publication."""

    records: list[dict[str, object]] = []
    for job in jobs:
        decision = decisions.get(job.id)
        if job.id in errors:
            status = "error"
        elif job.processing_mode == "discovery":
            status = "retained"
        elif decision is None or decision.variant is None:
            status = "no_match"
        elif job.id in selection_blocks:
            status = "metadata_blocked"
        else:
            status = "evaluated"
        if job.processing_mode != "core":
            tailoring_status = "not_applicable"
        elif job.id in tailorings:
            tailoring_status = "ready"
        elif job.id in errors:
            tailoring_status = "error"
        else:
            tailoring_status = "not_selected"
        records.append(
            {
                "job_id": job.id,
                "profile_id": job.track,
                "processing_mode": job.processing_mode,
                "status": status,
                "selected": job.id in selected_ids,
                "score": decision.score if decision else None,
                "core_fit": decision.core_fit if decision else "",
                "variant": decision.variant or "" if decision else "",
                "true_gap": list(decision.true_gap) if decision else [],
                "rationale": decision.rationale if decision else "",
                "decision_source": decision.source if decision else "",
                "tailoring_status": tailoring_status,
            }
        )
    document = {
        "schema_version": 1,
        "contract_version": f"screening:{PROMPT_VERSION};tailoring:{TAILORING_PROMPT_VERSION}",
        "generated_at": datetime.now().astimezone().isoformat(),
        "record_count": len(records),
        "records": records,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument(
        "--workspace",
        type=Path,
        default=Path.cwd(),
        help="The private workspace holding resume templates, the skills whitelist, and "
        "every generated application. Must contain workspace.toml. Defaults to the "
        "current directory.",
    )
    parser.add_argument(
        "--positions-root",
        type=Path,
        help="Optional working directory for the job-scraper command. Omit it when "
        "job-scraper is installed and configured independently.",
    )
    parser.add_argument(
        "--job-scraper-command-json",
        help='JSON argv prefix used to invoke job-scraper; defaults to ["job-scraper"]. '
        'A source checkout may use ["uv", "run", "job-scraper"] with '
        "--positions-root.",
    )
    parser.add_argument(
        "--release-manifest",
        type=Path,
        help="A manifest written by fine-screen-release for this exact code and private workspace.",
    )
    parser.add_argument(
        "--require-release-manifest",
        action="store_true",
        help="Fail closed unless --release-manifest verifies before screening starts.",
    )
    # No static `choices`: Positions owns the track list now, so a new profile
    # there must not need an edit here. Unknown names are reported against the
    # feed's own tracks once it has been read.
    parser.add_argument("--track", action="append", default=None)
    parser.add_argument(
        "--job-id",
        action="append",
        default=None,
        help="Update only these explicit feed job IDs; repeat for a bounded review slice.",
    )
    parser.add_argument(
        "--expect-job-count",
        type=positive_int,
        help="Fail closed unless the explicit --job-id slice resolves to this count.",
    )
    dates = parser.add_mutually_exclusive_group()
    dates.add_argument("--since-days", type=positive_int)
    dates.add_argument("--since-date", type=iso_date)
    parser.add_argument("--until-date", type=iso_date)
    parser.add_argument("--min-score", type=float, default=0.70)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="Generate drafts and reconcile Notion.")
    parser.add_argument(
        "--skip-tailoring",
        action="store_true",
        help="Screen and report, but do not ask the agent for resume tailoring plans. "
        "Implies no drafts and no Notion writes. Use when you want the screening "
        "verdicts without spending agent budget on resumes.",
    )
    parser.add_argument(
        "--persist-results",
        action="store_true",
        help="Persist the validated result handoff in Positions during a dry run. "
        "Apply mode always persists before generating artifacts or updating Notion.",
    )
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="Deprecated explicit alias for the safe default.",
    )
    parser.add_argument(
        "--replace-existing",
        action="store_true",
        help="Archive and replace selected generated drafts.",
    )
    parser.add_argument(
        "--reset-existing",
        action="store_true",
        help="With --apply, clear old bounded Fine-screened tags/attachments first.",
    )
    parser.add_argument(
        "--inspect-existing",
        action="store_true",
        help="Read-only count of old bounded Fine-screened tags/attachments.",
    )
    parser.add_argument(
        "--pull-first",
        action="store_true",
        help="Run `uv run job-scraper run` before screening.",
    )
    parser.add_argument(
        "--pull-arg",
        action="append",
        default=[],
        help="Argument passed to job-scraper run; repeat as --pull-arg=VALUE.",
    )
    parser.add_argument("--agent-provider", choices=("codex", "claude", "command"), default="codex")
    parser.add_argument("--agent-model")
    parser.add_argument(
        "--agent-command-json",
        help="For provider=command: JSON argv array; prompt is sent on stdin.",
    )
    parser.add_argument(
        "--trust-agent-command",
        action="store_true",
        help="Acknowledge that a custom provider is not sandboxed by this script.",
    )
    parser.add_argument("--agent-batch-size", type=positive_int, default=4)
    parser.add_argument("--agent-workers", type=positive_int, default=3)
    parser.add_argument("--max-agent-calls", type=positive_int, default=100)
    parser.add_argument("--max-refinement-calls", type=positive_int, default=100)
    parser.add_argument(
        "--max-tailoring-calls",
        type=positive_int,
        default=100,
        help="Maximum one-job agent calls that create evidence-bound resume tailoring plans.",
    )
    parser.add_argument("--agent-timeout-seconds", type=positive_int, default=300)
    parser.add_argument("--refresh-agent-cache", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        job_scraper_argv = parse_job_scraper_argv(args.job_scraper_command_json)
    except ValueError as exc:
        print(f"ERROR {exc}", file=sys.stderr)
        return 2
    positions_root = args.positions_root.resolve() if args.positions_root is not None else None
    try:
        workspace = load_workspace(args.workspace)
        workspace.require_inputs()
    except WorkspaceError as exc:
        print(f"ERROR {exc}", file=sys.stderr)
        return 2
    if args.require_release_manifest and args.release_manifest is None:
        print("ERROR --require-release-manifest requires --release-manifest", file=sys.stderr)
        return 2
    release_identity: ReleaseIdentity | None = None
    if args.release_manifest is not None:
        try:
            release_identity = verify_manifest(workspace, args.release_manifest)
        except ReleaseError as exc:
            print(f"ERROR release manifest verification failed: {exc}", file=sys.stderr)
            return 2
    candidate = workspace.candidate
    try:
        feed_window_args, date_label = resolve_date_window(args)
        custom_argv = parse_custom_argv(args.agent_command_json)
    except ValueError as exc:
        print(f"ERROR {exc}", file=sys.stderr)
        return 2
    if args.agent_provider == "command" and custom_argv is None:
        print(
            "ERROR --agent-provider command requires --agent-command-json",
            file=sys.stderr,
        )
        return 2
    if args.agent_provider == "command" and not args.trust_agent_command:
        print(
            "ERROR custom providers require --trust-agent-command because they are not sandboxed",
            file=sys.stderr,
        )
        return 2
    if args.agent_batch_size > 8:
        print(
            "ERROR --agent-batch-size cannot exceed the schema maximum of 8",
            file=sys.stderr,
        )
        return 2
    if args.agent_workers > 8:
        print("ERROR --agent-workers cannot exceed 8", file=sys.stderr)
        return 2
    if not 0 <= args.min_score <= 1:
        print("ERROR --min-score must be between 0 and 1", file=sys.stderr)
        return 2
    if args.apply and args.skip_tailoring:
        # Without a tailoring plan every selected job is dropped, so this would
        # "succeed" having generated nothing. Refusing says so.
        print(
            "ERROR --apply needs tailoring plans; --skip-tailoring would generate nothing",
            file=sys.stderr,
        )
        return 2
    if args.reset_existing and not args.apply:
        print("ERROR --reset-existing requires --apply", file=sys.stderr)
        return 2
    if args.replace_existing and not args.apply:
        print("ERROR --replace-existing requires --apply", file=sys.stderr)
        return 2
    if args.expect_job_count is not None and not args.job_id:
        print("ERROR --expect-job-count requires --job-id", file=sys.stderr)
        return 2
    if args.pull_first:
        status = run_positions_pull(positions_root, job_scraper_argv, args.pull_arg)
        if status != 0:
            return status

    try:
        feed_payload = read_feed(
            positions_root,
            job_scraper_argv,
            args.track,
            feed_window_args,
            published_only=args.inspect_existing and not args.apply,
        )
        feed_jobs, feed_since, feed_until = parse_feed_document(feed_payload)
    except FeedError as exc:
        print(f"ERROR {exc}", file=sys.stderr)
        return 2

    # The feed reports which tracks it actually carries, so a new Positions
    # profile shows up here without this script being edited. `--track` still
    # narrows, and is passed through so the filtering happens at the source.
    jobs_by_track: dict[str, list[Job]] = {}
    for job in feed_jobs:
        jobs_by_track.setdefault(job.track, []).append(job)
    tracks = args.track or sorted(jobs_by_track)

    # A misspelled --track used to be caught by argparse `choices`. It cannot be
    # now, and silently screening nothing is the worse failure, so check it here.
    # An empty feed is not evidence of a bad name -- a quiet day looks the same.
    if args.track and feed_jobs:
        unknown = sorted(set(args.track) - set(jobs_by_track))
        if unknown:
            print(
                f"ERROR unknown track(s): {', '.join(unknown)}; "
                f"the feed carries: {', '.join(sorted(jobs_by_track)) or '(none)'}",
                file=sys.stderr,
            )
            return 2
    for track in tracks:
        jobs_by_track.setdefault(track, [])

    whitelist = load_whitelist(workspace.skills_whitelist_path)
    whitelist_map = {entry.match: entry for entry in whitelist}
    try:
        variants = load_variants(workspace.root)
    except (OSError, ValueError) as exc:
        print(f"ERROR invalid resume variant template: {exc}", file=sys.stderr)
        return 2
    token = os.environ.get("NOTION_INTEGRATION_TOKEN", "")
    if not variants:
        print(
            f"ERROR no resume variants found under {workspace.resume_variants_dir}",
            file=sys.stderr,
        )
        return 2
    if (args.apply or args.reset_existing or args.inspect_existing) and not token:
        print(
            "ERROR --apply/--reset-existing/--inspect-existing requires NOTION_INTEGRATION_TOKEN",
            file=sys.stderr,
        )
        return 2

    all_jobs = [job for track in tracks for job in jobs_by_track[track]]
    explicit_job_ids = set(args.job_id or [])
    if explicit_job_ids:
        available_ids = {job.id for job in all_jobs}
        missing_ids = sorted(explicit_job_ids - available_ids)
        if missing_ids:
            print(
                f"ERROR explicit job IDs are absent from the bounded feed: {', '.join(missing_ids)}",
                file=sys.stderr,
            )
            return 2
        for track in tracks:
            jobs_by_track[track] = [
                job for job in jobs_by_track[track] if job.id in explicit_job_ids
            ]
        all_jobs = [job for track in tracks for job in jobs_by_track[track]]
        if args.expect_job_count is not None and len(all_jobs) != args.expect_job_count:
            print(
                f"ERROR explicit job slice resolved to {len(all_jobs)} jobs; "
                f"expected {args.expect_job_count}",
                file=sys.stderr,
            )
            return 2
    # `discovery` jobs get a decision too now that hard pre-feed gating is
    # gone -- otherwise they would reach Notion/CSV with zero relevance
    # judgment at all. They still never reach tailoring (see the
    # `processing_mode != "core"` guards below).
    agent_jobs = [job for job in all_jobs if job.processing_mode in {"core", "review", "discovery"}]
    unique_jobs, duplicate_of = deduplicate_jobs(agent_jobs)
    print(
        "Positions boundary: "
        + (str(positions_root) if positions_root is not None else "installed job-scraper")
    )
    print(f"Feed window UTC: {feed_since} <= first_seen_at < {feed_until}")
    print(f"Resume variants: {', '.join(sorted(variants))}")
    print(f"Quick-learn whitelist entries: {len(whitelist)}")
    print(
        f"Agent provider: {args.agent_provider}{f' ({args.agent_model})' if args.agent_model else ''}"
    )
    mode_counts = {
        mode: sum(job.processing_mode == mode for job in all_jobs)
        for mode in sorted(PROCESSING_MODES)
    }
    print(
        f"Jobs: {len(all_jobs)} rows; {len(unique_jobs)} unique agent decisions; "
        + ", ".join(f"{mode}={count}" for mode, count in mode_counts.items())
    )
    print(f"Mode: {'APPLY' if args.apply else 'DRY-RUN'}; Notion token available: {bool(token)}")
    print()

    report_root = workspace.reports_dir
    cache_root = report_root / "agent-cache" / "v1"
    factual_profile = (workspace.root / "shared" / "profile-notes.md").read_text(encoding="utf-8")
    try:
        _base_results, decisions, error_lines, base_calls = screen_jobs_with_agent(
            unique_jobs,
            variants=variants,
            whitelist=whitelist,
            provider=args.agent_provider,
            model=args.agent_model,
            custom_argv=custom_argv,
            batch_size=args.agent_batch_size,
            workers=args.agent_workers,
            max_agent_calls=args.max_agent_calls,
            timeout_seconds=args.agent_timeout_seconds,
            cache_root=cache_root,
            agent_cwd=workspace.root,
            refresh_cache=args.refresh_agent_cache,
            factual_profile=factual_profile,
        )
    except (AgentContractError, OSError) as exc:
        print(f"ERROR agent screening failed before reports: {exc}", file=sys.stderr)
        return 1

    refinement_jobs = [
        job
        for job in unique_jobs
        if (decision := decisions.get(job.id))
        and decision_needs_refinement(job, decision, args.min_score)
    ]
    refinement_calls = 0
    if refinement_jobs:
        print(f"Refining {len(refinement_jobs)} threshold/family-sensitive job(s) individually.")
        try:
            _refined_results, refined_decisions, refinement_errors, refinement_calls = (
                screen_jobs_with_agent(
                    refinement_jobs,
                    variants=variants,
                    whitelist=whitelist,
                    provider=args.agent_provider,
                    model=args.agent_model,
                    custom_argv=custom_argv,
                    batch_size=1,
                    workers=args.agent_workers,
                    max_agent_calls=args.max_refinement_calls,
                    timeout_seconds=args.agent_timeout_seconds,
                    cache_root=cache_root,
                    agent_cwd=workspace.root,
                    refresh_cache=True,
                    factual_profile=factual_profile,
                    decision_source="agent-refined",
                )
            )
        except (AgentContractError, OSError) as exc:
            print(f"ERROR agent refinement failed before reports: {exc}", file=sys.stderr)
            return 1
        decisions.update(refined_decisions)
        error_lines.extend(refinement_errors)

    calls = base_calls + refinement_calls
    job_by_id = {job.id: job for job in agent_jobs}
    results_by_id: dict[str, MatchResult] = {}
    for job in unique_jobs:
        decision = decisions.get(job.id)
        if decision is None:
            continue
        result = match_result_from_decision(job, decision, whitelist_map)
        if result is not None:
            results_by_id[job.id] = result
    for duplicate_id, primary_id in duplicate_of.items():
        primary = decisions.get(primary_id)
        if primary is None:
            continue
        duplicate_decision = AgentDecision(
            job_id=duplicate_id,
            variant=primary.variant,
            core_fit=primary.core_fit,
            daily_work=primary.daily_work,
            covered=primary.covered,
            addable=primary.addable,
            true_gap=primary.true_gap,
            score=primary.score,
            rationale=primary.rationale,
            source=f"deduplicated:{primary.source}",
        )
        decisions[duplicate_id] = duplicate_decision
        duplicate_result = match_result_from_decision(
            job_by_id[duplicate_id], duplicate_decision, whitelist_map
        )
        if duplicate_result is not None:
            results_by_id[duplicate_id] = duplicate_result

    error_map: dict[str, str] = {}
    for line in error_lines:
        job_id, _, message = line.partition(": ")
        error_map[job_id] = message
    selection_blocks = {
        job.id: issue
        for job in all_jobs
        if job.processing_mode != "discovery" and (issue := selection_block_reason(job, error_map))
    }
    selected_by_track: dict[str, list[MatchResult]] = {}
    selected_ids: set[str] = set()
    for track in tracks:
        candidates = [
            results_by_id[job.id]
            for job in jobs_by_track[track]
            if job.id in results_by_id
            and job.id not in selection_blocks
            and is_selection_candidate(results_by_id[job.id], args.min_score)
        ]
        candidates.sort(key=lambda match: (-match.score, match.job.first_seen_at, match.job.id))
        selected = candidates
        selected_by_track[track] = selected
        selected_ids.update(match.job.id for match in selected)
        track_jobs = jobs_by_track[track]
        if track_jobs and all(job.processing_mode == "discovery" for job in track_jobs):
            print(f"[{track}] {len(track_jobs)} retained; resume generation skipped")
        else:
            print(
                f"[{track}] {len(track_jobs)} evaluated, {len(candidates)} passed, "
                f"{len(selected)} selected, "
                f"{sum(job.id in selection_blocks for job in track_jobs)} blocked"
            )
        for match in selected:
            print(
                f"  {match.score:.2f} {match.job.company} -- {match.job.title} "
                f"[{match.variant}; {match.core_fit}; {match.decision_source}]"
            )

    selected_matches_by_id = {
        match.job.id: match for matches in selected_by_track.values() for match in matches
    }
    pdf_collisions = pdf_filename_collisions(
        (match.job for match in selected_matches_by_id.values()), candidate
    )
    if pdf_collisions:
        details = "; ".join(
            f"{name} ({', '.join(job_ids)})" for name, job_ids in sorted(pdf_collisions.items())
        )
        print(f"ERROR selected jobs would share a PDF filename: {details}", file=sys.stderr)
        return 1
    if args.skip_tailoring:
        # Screening verdicts are already decided; tailoring is a second, separate
        # round of paid agent calls whose only product is a resume. Skipping it
        # keeps the reports honest -- they simply carry no tailoring plan.
        tailoring_by_id: dict[str, ResumeTailoring] = {}
        tailoring_errors: dict[str, str] = {}
        tailoring_calls = 0
    else:
        try:
            core_matches = [
                match
                for match in selected_matches_by_id.values()
                if match.job.processing_mode == "core"
            ]
            tailoring_by_id, tailoring_errors, tailoring_calls = tailor_matches_with_agent(
                core_matches,
                cv_root=workspace.root,
                provider=args.agent_provider,
                model=args.agent_model,
                custom_argv=custom_argv,
                workers=args.agent_workers,
                max_agent_calls=args.max_tailoring_calls,
                timeout_seconds=args.agent_timeout_seconds,
                cache_root=report_root / "tailoring-cache" / "v1",
                refresh_cache=args.refresh_agent_cache,
            )
        except (AgentContractError, OSError, ValueError) as exc:
            print(f"ERROR agent tailoring failed before reports: {exc}", file=sys.stderr)
            return 1
    error_map.update(tailoring_errors)
    selected_ids.clear()
    for track, matches in selected_by_track.items():
        tailored_matches: list[MatchResult] = []
        for match in matches:
            tailoring = tailoring_by_id.get(match.job.id)
            # A missing plan drops the job only when tailoring was actually
            # attempted -- there it means the agent failed or was rejected. Under
            # --skip-tailoring nothing was asked for, so dropping the job would
            # report zero selections for a screen that really did select.
            if (
                tailoring is None
                and not args.skip_tailoring
                and match.job.processing_mode == "core"
            ):
                continue
            match.tailoring = tailoring
            tailored_matches.append(match)
            if tailoring is not None:
                selected_ids.add(match.job.id)
        selected_by_track[track] = tailored_matches
        write_agent_report(
            report_root / f"{track}-{date_label}-agent.csv",
            jobs_by_track[track],
            decisions,
            {match.job.id for match in selected_by_track[track]},
            error_map,
            selection_blocks,
            tailoring_by_id,
            release_identity,
        )
    reported_selected_ids = {
        match.job.id for matches in selected_by_track.values() for match in matches
    }
    track_scope = "-".join(slugify(track) for track in tracks) or "no-tracks"
    slice_scope = ""
    if explicit_job_ids:
        digest = hashlib.sha256("\n".join(sorted(explicit_job_ids)).encode()).hexdigest()[:10]
        slice_scope = f"-slice-{digest}"
    result_path = report_root / (f"screening-{track_scope}-{date_label}{slice_scope}-results.json")
    write_screening_result_document(
        result_path,
        all_jobs,
        decisions,
        reported_selected_ids,
        error_map,
        selection_blocks,
        tailoring_by_id,
    )
    if args.apply or args.persist_results:
        try:
            persist_screening_results(positions_root, job_scraper_argv, result_path)
        except FeedError as exc:
            print(f"ERROR {exc}", file=sys.stderr)
            return 1
    source_counts = summarize_decision_sources(decisions)
    print(
        f"Agent invocations this run: {calls}; "
        f"fresh decisions: {source_counts['fresh_base']} base, "
        f"{source_counts['fresh_refined']} refined; "
        f"cache hits: {source_counts['cache_hits']}; "
        f"deduplicated decisions: {source_counts['deduplicated']}; "
        f"other sources: {source_counts['other']}"
    )
    print(
        f"Tailoring agent invocations this run: {tailoring_calls}; valid plans: {len(tailoring_by_id)}"
    )
    print(f"Reports: {report_root}")
    print(f"Durable result handoff: {result_path}")

    if args.inspect_existing:
        tagged_jobs, attachment_count = inspect_existing_fine_screened(token, all_jobs, candidate)
        print(
            f"Existing bounded Notion state: {len(tagged_jobs)} Fine-screened pages, "
            f"{attachment_count} matching generated CV attachments"
        )

    selected_errors = {job_id: error_map[job_id] for job_id in selected_ids if job_id in error_map}
    if selected_errors:
        print(
            f"ERROR {len(selected_errors)} selected jobs have no valid decision or tailoring plan; "
            "apply is blocked.",
            file=sys.stderr,
        )
        return 1
    if not args.apply:
        if args.persist_results:
            print(
                "Dry-run complete. Durable screening state persisted; no drafts or Notion changed."
            )
        else:
            print("Dry-run complete. No generated drafts or external state changed.")
        return 0

    archive_root = workspace.archive_dir / datetime.now().strftime("%Y%m%dT%H%M%S")
    batch_date = datetime.now(LOCAL_TIMEZONE).date()
    generated = 0
    failed_pdfs = 0
    prepared: list[tuple[MatchResult, str, bool]] = []
    for track in tracks:
        for match in selected_by_track[track]:
            job = match.job
            tailoring = match.tailoring
            if job.processing_mode != "core":
                continue
            if tailoring is None:  # Defensive: selected matches require a validated plan above.
                raise RuntimeError(f"selected job has no tailoring plan: {job.id}")
            slug = application_slug(job)
            legacy_slug = slugify(job.company, job.title)
            if args.replace_existing and legacy_slug != slug:
                stale = [
                    workspace.applications_dir / legacy_slug / "job-notes.md",
                    workspace.generated_variants_dir / f"{legacy_slug}.tex",
                ]
                for legacy in candidate.legacy_file_slugs:
                    stale += [
                        workspace.root / "CV" / f"{legacy}_CV_{legacy_slug}.pdf",
                        workspace.root / "CV" / f"{legacy}_CV_{slug}.pdf",
                        workspace.output_pdf_dir / f"{slug}__{legacy}_CV.pdf",
                    ]
                stale += [
                    workspace.output_pdf_dir / name
                    for name in legacy_fine_screen_pdf_names(job, candidate)
                ]
                for path in stale:
                    archive_file(path, workspace.root, archive_root)
            created = create_application(
                workspace.root,
                slug,
                job.company,
                job.title,
                job.location,
                match,
                tailoring,
                candidate,
                replace_existing=args.replace_existing,
                archive_root=archive_root,
                batch_date=batch_date,
            )
            if created is None:
                print(f"  [skip existing] {slug}")
                resume_pdf_ok = fine_screen_pdf_path(
                    workspace.root, job, candidate, batch_date
                ).is_file()
            else:
                app_dir, resume_pdf_ok = created
                generated += 1
                print(
                    f"  generated: {app_dir.relative_to(workspace.root)}; "
                    f"PDF {'ok' if resume_pdf_ok else 'FAILED'}"
                )
            if not resume_pdf_ok:
                failed_pdfs += 1
                continue
            prepared.append((match, slug, created is not None))

    if failed_pdfs:
        print(
            f"Apply stopped before Notion changes: {generated} drafts generated/replaced, "
            f"{failed_pdfs} PDF failures."
        )
        return 1

    try:
        publish_finalized_results(
            positions_root,
            job_scraper_argv,
            result_path,
            expected_count=len(all_jobs),
        )
        refresh_job_publications(
            all_jobs,
            positions_root=positions_root,
            job_scraper_argv=job_scraper_argv,
            tracks=tracks,
            window_args=feed_window_args,
        )
    except FeedError as exc:
        print(f"ERROR {exc}", file=sys.stderr)
        return 1

    if args.reset_existing:
        reset_pages, reset_attachments = reset_existing_fine_screened(token, all_jobs, candidate)
        print(
            f"Reset bounded Notion state: {reset_pages} tags cleared, "
            f"{reset_attachments} generated CV blocks archived"
        )
    elif token and not explicit_job_ids:
        stale_pages, stale_attachments = clear_unselected_fine_screened(
            token, all_jobs, selected_ids, candidate
        )
        print(
            f"Reconciled stale bounded Notion state: {stale_pages} tags cleared, "
            f"{stale_attachments} generated CV blocks archived"
        )
    elif token:
        print("Explicit job slice: preserved all unrelated Notion Screen state.")

    ensured_properties: set[str] = set()
    notion_failures = 0
    prepared_ids = {match.job.id for match, _slug, _generated_now in prepared}
    if token:
        for job in all_jobs:
            if job.id in prepared_ids or not job.notion_page_id:
                continue
            try:
                ensure_screen_property(token, job.notion_data_source_id, ensured_properties)
                tag_notion_page(
                    token,
                    job.notion_page_id,
                    finalized_screen_tag(
                        job.id,
                        selected_ids=selected_ids,
                        errors=error_map,
                        selection_blocks=selection_blocks,
                    ),
                )
            except (RuntimeError, ValueError, HTTPError, URLError, OSError) as exc:
                notion_failures += 1
                print(f"    Notion status reconcile failed for {job.id}: {exc}")
    for match, slug, generated_now in prepared:
        job = match.job
        if token and job.notion_page_id:
            try:
                ensure_screen_property(token, job.notion_data_source_id, ensured_properties)
                if not attach_application_pdfs(
                    token,
                    job.notion_page_id,
                    workspace.root,
                    job,
                    candidate,
                    replace_existing=generated_now,
                    batch_date=batch_date,
                ):
                    raise RuntimeError("generated CV attachment failed")
                tag_notion_page(
                    token,
                    job.notion_page_id,
                    finalized_screen_tag(
                        job.id,
                        selected_ids=selected_ids,
                        errors=error_map,
                        selection_blocks=selection_blocks,
                    ),
                )
            except (RuntimeError, ValueError, HTTPError, URLError, OSError) as exc:
                notion_failures += 1
                print(f"    Notion reconcile failed for {slug}: {exc}")

    if prepared and not failed_pdfs and not notion_failures:
        latest = update_fine_screen_latest_link(workspace.root, batch_date)
        print(f"Updated latest Fine Screen link: {latest}")

    print(
        f"Apply complete: {generated} drafts generated/replaced, {failed_pdfs} PDF failures, "
        f"{notion_failures} Notion failures."
    )
    return 1 if failed_pdfs or notion_failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
