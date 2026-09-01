from __future__ import annotations

import csv
import json
from datetime import date
from pathlib import Path

import pytest

from fine_screen import screen as fine_screen
from fine_screen.agent.contract import (
    AgentDecision,
    RampUpSkillGroup,
    TailoredExperience,
    TailoredProject,
    TailoringSkillGroup,
)
from fine_screen.screen import Job, MatchResult, NotionFileBlock, WhitelistEntry


def _write_evidence_library(root: Path) -> None:
    shared = root / "shared"
    shared.mkdir(parents=True, exist_ok=True)
    (shared / "evidence-library.json").write_text(
        """{
  "cards": [
    {
      "id": "project-1",
      "organization": "Example Project",
      "location": "Remote",
      "role": "Project",
      "dates": "2026",
      "facts": ["PyTorch evidence for a factual project."]
    }
  ]
}
""",
        encoding="utf-8",
    )


def _job(
    job_id: str = "fictional-1",
    *,
    track: str = "cpp",
    url: str = "https://example.test/1",
) -> Job:
    return Job(
        id=job_id,
        track=track,
        title="Device Integration Engineer",
        company="Example Devices",
        location="Fictional City",
        language="English",
        url=url,
        description_full="Build C++ device integrations using MQTT.",
        first_seen_at="2026-08-20T08:00:00+00:00",
        notion_page_id=f"page-{job_id}",
        notion_data_source_id="data-source-1",
    )


def _match(job: Job) -> MatchResult:
    return MatchResult(
        job=job,
        variant="systems",
        covered={"C++"},
        addable=[
            WhitelistEntry(
                match="mqtt",
                phrase="MQTT (active project practice)",
                hours=6,
                project="Publish simulated telemetry.",
                interview_check="Explain QoS and reconnect behavior.",
            )
        ],
        true_gap=set(),
        score=0.82,
        core_fit="moderate",
        daily_work="Build device integrations.",
        rationale="The core work matches existing systems experience.",
        decision_source="agent",
    )


def _decision(job_id: str, source: str) -> AgentDecision:
    return AgentDecision(
        job_id=job_id,
        variant=None,
        core_fit="none",
        daily_work="N/A",
        covered=(),
        addable=(),
        true_gap=(),
        score=0.10,
        rationale="Fictional decision.",
        source=source,
    )


def test_exact_local_date_window_is_passed_through_to_the_feed() -> None:
    """The window arithmetic belongs to the feed; this side only forwards it.

    job-scraper owns each profile's timezone, so converting local dates to a UTC
    span here would be a second implementation free to drift. What is still this
    screener's job is the label, which names its report files.
    """
    args = fine_screen.parse_args(["--since-date", "2026-08-20", "--until-date", "2026-08-24"])

    window_args, label = fine_screen.resolve_date_window(args)

    assert window_args == [
        "--since-date",
        "2026-08-20",
        "--until-date",
        "2026-08-24",
    ]
    assert label == "2026-08-20_2026-08-24"


def test_release_manifest_flag_requires_a_manifest_path() -> None:
    args = fine_screen.parse_args(["--require-release-manifest"])

    assert args.require_release_manifest is True
    assert args.release_manifest is None


def test_a_bare_since_date_still_means_through_today() -> None:
    """Long-standing behavior here; the feed's own default is a single day."""
    args = fine_screen.parse_args(["--since-date", "2026-08-20"])
    today = fine_screen.datetime.now(fine_screen.LOCAL_TIMEZONE).date().isoformat()

    window_args, label = fine_screen.resolve_date_window(args)

    assert window_args == ["--since-date", "2026-08-20", "--until-date", today]
    assert label == f"2026-08-20_{today}"


def test_since_days_is_forwarded_as_a_day_count() -> None:
    args = fine_screen.parse_args(["--since-days", "3"])

    window_args, label = fine_screen.resolve_date_window(args)

    assert window_args == ["--since-days", "3"]
    assert label == "last-3-days"


def test_deduplicate_jobs_preserves_tracks_without_repeating_agent_work() -> None:
    first = _job("one", track="ai")
    duplicate = _job("two", track="cpp")

    unique, duplicate_of = fine_screen.deduplicate_jobs([first, duplicate])

    assert unique == [first]
    assert duplicate_of == {"two": "one"}


def test_deduplicate_jobs_normalizes_linkedin_job_id_across_url_forms() -> None:
    first = _job(
        "one",
        url="https://de.linkedin.com/jobs/view/backend-role-at-example-4453416425",
    )
    duplicate = _job("two", url="https://www.linkedin.com/comm/jobs/view/4453416425/")

    unique, duplicate_of = fine_screen.deduplicate_jobs([first, duplicate])

    assert unique == [first]
    assert duplicate_of == {"two": "one"}


def test_application_slug_distinguishes_same_company_and_title_jobs() -> None:
    first = _job("one", url="https://example.test/jobs/one")
    second = _job("two", url="https://example.test/jobs/two")

    assert fine_screen.application_slug(first) != fine_screen.application_slug(second)
    assert fine_screen.application_slug(first).startswith(
        "example-devices-device-integration-engineer-"
    )


def test_pdf_name_starts_with_candidate_and_uses_company_and_role(
    candidate, tmp_path: Path
) -> None:
    job = _job("readable-name")

    path = fine_screen.fine_screen_pdf_path(tmp_path, job, candidate, date(2026, 8, 26))

    assert path.parent == tmp_path / "CV" / "Fine-Screened" / "2026-08-26"
    assert path.name == (
        f"{candidate.file_slug}_CV_Example-Devices_Device-Integration-Engineer.pdf"
    )


def test_latest_link_points_at_the_requested_dated_batch(tmp_path: Path) -> None:
    batch_date = date(2026, 8, 26)
    target = fine_screen.fine_screen_batch_directory(tmp_path, batch_date)
    target.mkdir(parents=True)

    latest = fine_screen.update_fine_screen_latest_link(tmp_path, batch_date)

    assert latest.resolve() == target.resolve()

    next_date = date(2026, 8, 27)
    next_target = fine_screen.fine_screen_batch_directory(tmp_path, next_date)
    next_target.mkdir()
    assert (
        fine_screen.update_fine_screen_latest_link(tmp_path, next_date).resolve()
        == next_target.resolve()
    )


def test_pdf_filename_collision_is_detected_without_adding_a_hash(
    candidate,
) -> None:
    first = _job("one", url="https://example.test/jobs/one")
    second = _job("two", url="https://example.test/jobs/two")

    collisions = fine_screen.pdf_filename_collisions([first, second], candidate)

    assert collisions == {
        f"{candidate.file_slug}_CV_Example-Devices_Device-Integration-Engineer.pdf": (
            "one",
            "two",
        )
    }


def test_pdf_name_preserves_unicode_and_reserves_company_and_role_space(
    candidate,
) -> None:
    job = _job("unicode-name")
    job.company = "虚构欧洲汽车技术有限公司"
    job.title = "智能座舱产品管理工程师" * 5

    name = fine_screen.fine_screen_pdf_name(job, candidate)

    assert name.startswith(
        f"{candidate.file_slug}_CV_虚构欧洲汽车技术有限公司_智能座舱产品管理工程师"
    )
    assert name.endswith(".pdf")
    assert len(name.removesuffix(".pdf").rsplit("_", maxsplit=1)[-1]) <= 48


def test_notion_stripped_unicode_filename_matches_stable_generated_name(
    candidate,
) -> None:
    job = _job("unicode-notion")
    job.company = "Beispielcafé GmbH"
    expected = fine_screen.fine_screen_pdf_name(job, candidate)
    notion_name = expected.encode("ascii", errors="ignore").decode()

    assert notion_name != expected
    assert fine_screen.generated_filename_matches(notion_name, {expected})


def test_insert_skills_line_requires_exactly_one_explicit_marker() -> None:
    addable = [WhitelistEntry(match="mqtt", phrase="MQTT (active project practice)")]
    marked = f"before\n{fine_screen.SKILLS_INSERTION_MARKER}\nafter\n"

    rendered = fine_screen.insert_skills_line(marked, addable)

    assert fine_screen.SKILLS_INSERTION_MARKER not in rendered
    assert "Currently building fluency:" not in rendered
    with pytest.raises(ValueError, match="exactly one skills insertion marker"):
        fine_screen.insert_skills_line("before\nafter\n", addable)
    with pytest.raises(ValueError, match="exactly one skills insertion marker"):
        fine_screen.insert_skills_line(marked + fine_screen.SKILLS_INSERTION_MARKER, addable)


def test_every_resume_variant_has_exactly_one_skills_insertion_marker(workspace_root) -> None:
    """The template we ship must satisfy the contract it documents."""
    paths = sorted((workspace_root / "resume" / "variants").glob("*.tex"))
    variants = fine_screen.load_variants(workspace_root)

    assert paths
    assert all(
        path.read_text(encoding="utf-8").count(fine_screen.SKILLS_INSERTION_MARKER) == 1
        for path in paths
    )
    assert all(fine_screen.SKILLS_INSERTION_MARKER not in text for text in variants.values())


def test_replace_existing_archives_only_generated_files_and_preserves_manual_cover(
    candidate, monkeypatch, tmp_path: Path
) -> None:
    job = _job()
    match = _match(job)
    variant_root = tmp_path / "resume" / "variants"
    application_root = variant_root / "applications"
    app_dir = (
        tmp_path / "cover-letter" / "applications" / "example-devices-device-integration-engineer"
    )
    pdf_path = fine_screen.fine_screen_pdf_path(tmp_path, job, candidate)
    variant_root.mkdir(parents=True)
    application_root.mkdir()
    app_dir.mkdir(parents=True)
    _write_evidence_library(tmp_path)
    pdf_path.parent.mkdir(parents=True)
    (variant_root / "systems.tex").write_text(
        f"resume source\n{fine_screen.SKILLS_INSERTION_MARKER}\n", encoding="utf-8"
    )
    resume_path = application_root / "example-devices-device-integration-engineer.tex"
    notes_path = app_dir / "job-notes.md"
    manual_cover = app_dir / "cover.tex"
    resume_path.write_text("old resume", encoding="utf-8")
    notes_path.write_text("old notes", encoding="utf-8")
    manual_cover.write_text("manual cover", encoding="utf-8")
    pdf_path.write_bytes(b"old pdf")
    archive_root = tmp_path / "archive" / "run"
    monkeypatch.setattr(fine_screen, "find_tectonic", lambda _root: None)
    monkeypatch.setattr(
        fine_screen,
        "render_tailored_resume",
        lambda *_args: f"tailored resume\n{fine_screen.SKILLS_INSERTION_MARKER}\n",
    )

    result = fine_screen.create_application(
        tmp_path,
        "example-devices-device-integration-engineer",
        job.company,
        job.title,
        job.location,
        match,
        fine_screen.ResumeTailoring(
            job_id=job.id,
            summary=(),
            skills=(),
            experience_order=(),
            experiences=(),
        ),
        candidate,
        replace_existing=True,
        archive_root=archive_root,
    )

    assert result is not None
    assert manual_cover.read_text(encoding="utf-8") == "manual cover"
    assert (archive_root / resume_path.relative_to(tmp_path)).read_text(
        encoding="utf-8"
    ) == "old resume"
    assert (archive_root / notes_path.relative_to(tmp_path)).read_text(
        encoding="utf-8"
    ) == "old notes"
    assert (archive_root / pdf_path.relative_to(tmp_path)).read_bytes() == b"old pdf"


def test_agent_decisions_are_cached_without_reinvoking_provider(
    monkeypatch, tmp_path: Path
) -> None:
    job = _job()
    whitelist = [WhitelistEntry("mqtt", "MQTT fundamentals", 6, "project", "check")]
    calls = 0

    def fake_run_agent(**_kwargs: object) -> object:
        nonlocal calls
        calls += 1
        return {
            "decisions": [
                {
                    "job_id": job.id,
                    "variant": "systems",
                    "core_fit": "moderate",
                    "daily_work": "Build device integrations.",
                    "covered": ["C++"],
                    "addable": ["mqtt"],
                    "true_gap": [],
                    "score": 0.80,
                    "rationale": "The core systems work is evidenced.",
                }
            ]
        }

    monkeypatch.setattr(fine_screen, "run_agent", fake_run_agent)
    kwargs = {
        "variants": {"systems": "fictional resume"},
        "whitelist": whitelist,
        "provider": "codex",
        "model": None,
        "custom_argv": None,
        "batch_size": 4,
        "workers": 1,
        "max_agent_calls": 10,
        "timeout_seconds": 30,
        "cache_root": tmp_path / "cache",
        "refresh_cache": False,
        "agent_cwd": tmp_path,
    }

    first_results, first_decisions, first_errors, first_calls = fine_screen.screen_jobs_with_agent(
        [job], **kwargs
    )
    second_results, second_decisions, second_errors, second_calls = (
        fine_screen.screen_jobs_with_agent([job], **kwargs)
    )

    assert calls == 1
    assert first_calls == 1 and second_calls == 0
    assert not first_errors and not second_errors
    assert first_results[0].variant == second_results[0].variant == "systems"
    assert first_decisions[job.id].source == "agent"
    assert second_decisions[job.id].source == "cache:base"


def test_quota_error_stops_queued_agent_batches(monkeypatch, tmp_path: Path) -> None:
    jobs = [_job("quota-one"), _job("quota-two")]
    calls = 0

    def quota_exhausted(**_kwargs: object) -> object:
        nonlocal calls
        calls += 1
        raise fine_screen.AgentQuotaExceeded("agent quota unavailable")

    monkeypatch.setattr(fine_screen, "run_agent", quota_exhausted)

    with pytest.raises(fine_screen.AgentQuotaExceeded):
        fine_screen.screen_jobs_with_agent(
            jobs,
            variants={"systems": "fictional resume"},
            whitelist=[],
            provider="codex",
            model=None,
            custom_argv=None,
            batch_size=1,
            workers=1,
            max_agent_calls=2,
            timeout_seconds=30,
            cache_root=tmp_path / "cache",
            refresh_cache=False,
            agent_cwd=tmp_path,
        )

    assert calls == 1


def test_selected_job_tailoring_is_cached_and_attached_to_a_job(
    tmp_path: Path, monkeypatch
) -> None:
    job = _job("tailoring-cache")
    match = _match(job)
    base = r"""\section{Summary}
\resumeItemListStart
\resumeBullet{PyTorch evidence.}
\resumeItemListEnd
\section{Skills}
\resumeSubHeadingListStart
\item \textbf{ML:} PyTorch
% FINE_SCREEN_SKILLS_INSERTION_POINT
\resumeSubHeadingListEnd
\section{Experience}
\resumeSubHeadingListStart
\resumeSubheading
{Visual Lab}{City}
{Researcher}{2025}
\resumeItemListStart
\resumeBullet{RGB-D point clouds with PyTorch.}
  \resumeItemListEnd
  \resumeSubHeadingListEnd
\end{document}
"""
    variants = tmp_path / "resume" / "variants"
    variants.mkdir(parents=True)
    (variants / "systems.tex").write_text(base, encoding="utf-8")
    shared = tmp_path / "shared"
    shared.mkdir()
    (shared / "profile-notes.md").write_text("RGB-D point clouds with PyTorch.", encoding="utf-8")
    _write_evidence_library(tmp_path)
    calls = 0

    def fake_run_agent(**_kwargs: object) -> object:
        nonlocal calls
        calls += 1
        return {
            "job_id": job.id,
            "summary": [{"text": "PyTorch researcher.", "evidence": ["PyTorch evidence."]}],
            "skills": [
                {"label": "ML", "text": "PyTorch", "evidence": ["PyTorch evidence."]},
                {
                    "label": "Vision",
                    "text": "RGB-D point clouds",
                    "evidence": ["RGB-D point clouds"],
                },
            ],
            "experience_order": ["experience-1"],
            "experiences": [
                {
                    "experience_id": "experience-1",
                    "bullets": [
                        {
                            "text": "Built RGB-D point-cloud pipelines.",
                            "evidence": ["RGB-D point clouds with PyTorch."],
                        }
                    ],
                }
            ],
            "projects": [],
            "ramp_up": [],
        }

    monkeypatch.setattr(fine_screen, "run_agent", fake_run_agent)
    kwargs = {
        "cv_root": tmp_path,
        "provider": "codex",
        "model": None,
        "custom_argv": None,
        "workers": 1,
        "max_agent_calls": 5,
        "timeout_seconds": 30,
        "cache_root": tmp_path / "cache",
        "refresh_cache": False,
    }

    first, first_errors, first_calls = fine_screen.tailor_matches_with_agent([match], **kwargs)
    second, second_errors, second_calls = fine_screen.tailor_matches_with_agent([match], **kwargs)

    assert not first_errors and not second_errors
    assert first_calls == 1 and second_calls == 0 and calls == 1
    assert first[job.id].source == "agent"
    assert second[job.id].source == "cache"


def test_report_contains_non_selected_and_no_match_jobs(tmp_path: Path) -> None:
    selected = _job("selected")
    rejected = _job("rejected", url="https://example.test/2")
    decisions = {
        "selected": AgentDecision(
            job_id="selected",
            variant="systems",
            core_fit="moderate",
            daily_work="Build integrations.",
            covered=("C++",),
            addable=(),
            true_gap=(),
            score=0.80,
            rationale="Credible fit.",
        ),
        "rejected": AgentDecision(
            job_id="rejected",
            variant=None,
            core_fit="none",
            daily_work="Own regulated certification.",
            covered=(),
            addable=(),
            true_gap=("regulated certification ownership",),
            score=0.20,
            rationale="No matching experience.",
        ),
    }
    path = tmp_path / "report.csv"

    fine_screen.write_agent_report(path, [selected, rejected], decisions, {"selected"}, {}, {})

    rows = list(csv.DictReader(path.open(encoding="utf-8")))
    assert len(rows) == 2
    assert rows[0]["selected"] == "True"
    assert rows[1]["status"] == "no_match"
    assert rows[1]["true_gap"] == "regulated certification ownership"


def test_job_notes_preserve_true_gaps_for_interview_preparation() -> None:
    match = _match(_job("audit-gaps"))
    match.true_gap = {"regulated certification", "senior ownership"}

    notes = fine_screen.build_job_notes(match.job.company, match.job.title, match)

    assert "- regulated certification" in notes
    assert "- senior ownership" in notes


def test_tailoring_renderer_reorders_experience_and_escapes_agent_text() -> None:
    base = r"""\documentclass{article}
\newcommand{\resumeHeadline}{KI-Ingenieur}
\newcommand{\resumeItemListStart}{\begin{itemize}}
\newcommand{\resumeItemListEnd}{\end{itemize}}
\newcommand{\resumeSubHeadingListStart}{\begin{itemize}}
\newcommand{\resumeSubHeadingListEnd}{\end{itemize}}
\newcommand{\resumeSubheading}[4]{\item \textbf{#1}}
\newcommand{\resumeBullet}[1]{\item #1}
\begin{document}
\section{Summary}
\resumeItemListStart
\resumeBullet{Old summary.}
\resumeItemListEnd
\section{Skills}
\resumeSubHeadingListStart
\item \textbf{Old:} Skills
% FINE_SCREEN_SKILLS_INSERTION_POINT
\resumeSubHeadingListEnd
\section{Experience}
\resumeSubHeadingListStart
\resumeSubheading
{First Lab}{City}
{Researcher}{2025}
\resumeItemListStart
\resumeBullet{Old lab bullet.}
\resumeItemListEnd
\resumeSubheading
{Second Company}{City}
{Engineer}{2024}
\resumeItemListStart
\resumeBullet{Old company bullet.}
\resumeItemListEnd
\resumeSubHeadingListEnd
\section{Projects}
\resumeSubHeadingListStart
\resumeSubheading
{Static project}{City}
{Project}{2025}
\resumeItemListStart
\resumeBullet{Static project bullet.}
\resumeItemListEnd
\resumeSubHeadingListEnd
\end{document}
"""
    tailoring = fine_screen.ResumeTailoring(
        job_id="fictional-1",
        summary=(
            fine_screen.TailoringText(
                text="Computer vision engineer using RGB-D & C++.",
                evidence=("RGB-D point clouds",),
            ),
        ),
        skills=(
            TailoringSkillGroup(
                label="Computer Vision & ML",
                text="PyTorch, RGB-D point clouds, C++",
                evidence=("PyTorch",),
            ),
            TailoringSkillGroup(
                label="Remote Access",
                text="Linux",
                evidence=("C++",),
            ),
        ),
        experience_order=("experience-2", "experience-1"),
        experiences=(
            TailoredExperience(
                experience_id="experience-1",
                bullets=(
                    fine_screen.TailoringText(
                        text="Trained visual policies from RGB-D point clouds.",
                        evidence=("RGB-D point clouds",),
                    ),
                ),
            ),
            TailoredExperience(
                experience_id="experience-2",
                bullets=(
                    fine_screen.TailoringText(
                        text="Built C++ software for devices.",
                        evidence=("C++",),
                    ),
                ),
            ),
        ),
        projects=(
            TailoredProject(
                evidence_id="project-1",
                bullets=(
                    fine_screen.TailoringText(
                        text="Documented Linux service deployment and health checks.",
                        evidence=("PyTorch",),
                    ),
                ),
            ),
        ),
        ramp_up=(RampUpSkillGroup(label="Remote Access", terms=("RDP", "VMware")),),
    )

    cards = (
        fine_screen.EvidenceCard(
            evidence_id="project-1",
            organization="Example Deployment",
            location="Remote",
            role="Cloud Delivery",
            dates="2026",
            facts=("PyTorch",),
        ),
    )
    rendered = fine_screen.render_tailored_resume(base, tailoring, cards)

    assert "Computer vision engineer using RGB-D \\& C++." in rendered
    assert rendered.index("{Second Company}") < rendered.index("{First Lab}")
    assert "Old lab bullet" not in rendered
    assert "{Static project}" not in rendered
    assert "{Example Deployment}" in rendered
    assert "Remote Access" in rendered
    assert "Linux, RDP, VMware" in rendered
    assert rendered.count("Remote Access") == 1
    assert rendered.count(fine_screen.SKILLS_INSERTION_MARKER) == 1
    assert rendered.count(r"\begin{document}") == 1
    assert rendered.count(r"\end{document}") == 1

    rendered_german_profile = fine_screen.render_tailored_resume(
        base.replace(r"\section{Summary}", r"\section{Profil}", 1), tailoring, cards
    )

    assert r"\section{Profil}" in rendered_german_profile
    assert "Computer vision engineer using RGB-D \\& C++." in rendered_german_profile

    assert r"\addtolength{\topmargin}{0.45in}" in rendered

    rendered_without_projects = fine_screen.render_tailored_resume(
        base.replace(r"\section{Projects}", r"\section{Education}", 1), tailoring, cards
    )
    assert r"\section{Projects}" in rendered_without_projects
    assert "{Example Deployment}" in rendered_without_projects

    with pytest.raises(ValueError, match="complete LaTeX document"):
        fine_screen.render_tailored_resume(
            base.replace("\\begin{document}\n", ""), tailoring, cards
        )


def test_tailoring_notes_do_not_add_a_mother_resume_queue() -> None:
    notes = fine_screen.build_job_notes("Example", "Role", _match(_job("no-queue")))

    assert "Mother-resume candidates" not in notes


def test_company_metadata_guard_blocks_placeholders_urls_and_role_titles() -> None:
    assert fine_screen.company_metadata_issue(_job()) == ""
    unknown = _job("unknown")
    unknown.company = "Unknown"
    url_company = _job("url")
    url_company.company = "https://example.test/jobs"
    role_company = _job("role")
    role_company.company = "Software Engineer II"
    intake_company = _job("intake")
    intake_company.company = "2027 Intake (Shanghai)"

    assert fine_screen.company_metadata_issue(unknown)
    assert fine_screen.company_metadata_issue(url_company)
    assert fine_screen.company_metadata_issue(role_company)
    assert fine_screen.company_metadata_issue(intake_company)


def test_selection_guard_requires_job_description() -> None:
    missing_description = _job("missing-description")
    missing_description.description_full = ""

    assert fine_screen.selection_issue(missing_description) == "missing job description"


def test_agent_error_blocks_only_its_own_job_from_selection() -> None:
    failed = _job("failed")
    valid = _job("valid")

    assert fine_screen.selection_block_reason(
        failed, {"failed": "agent call budget exhausted"}
    ) == ("agent call budget exhausted")
    assert (
        fine_screen.selection_block_reason(valid, {"failed": "agent call budget exhausted"}) == ""
    )


def test_refinement_targets_score_boundary_and_cpp_family_conflict() -> None:
    boundary = AgentDecision(
        job_id="boundary",
        variant="systems",
        core_fit="weak",
        daily_work="Build systems.",
        covered=("C++",),
        addable=(),
        true_gap=(),
        score=0.68,
        rationale="Boundary fit.",
        source="cache:base",
    )
    conflict = AgentDecision(
        job_id="conflict",
        variant="ai-engineer",
        core_fit="weak",
        daily_work="Build C++ vision software.",
        covered=("C++",),
        addable=(),
        true_gap=(),
        score=0.49,
        rationale="Family conflict.",
        source="cache:base",
    )
    refined = AgentDecision(**{**boundary.__dict__, "source": "cache:refined"})
    cpp_job = _job("conflict")
    cpp_job.title = "Senior Rust/C++ Computer Vision Engineer"

    assert fine_screen.decision_needs_refinement(_job("boundary"), boundary, 0.70)
    assert fine_screen.decision_needs_refinement(cpp_job, conflict, 0.70)
    assert not fine_screen.decision_needs_refinement(_job("boundary"), refined, 0.70)


def test_refinement_rescues_null_variant_for_explicit_cpp_title() -> None:
    rejected = _decision("rejected-cpp", "cache:base")
    cpp_job = _job("rejected-cpp")
    cpp_job.title = "Senior C++ / Qt Engineer"
    unrelated_job = _job("unrelated")
    unrelated_job.title = "Optical Sales Director"

    assert fine_screen.decision_needs_refinement(cpp_job, rejected, 0.60)
    assert not fine_screen.decision_needs_refinement(unrelated_job, rejected, 0.60)


def test_decision_source_summary_keeps_fresh_refinement_out_of_cache_hits() -> None:
    decisions = {
        "base": _decision("base", "agent"),
        "refined": _decision("refined", "agent-refined"),
        "cached": _decision("cached", "cache:refined"),
        "deduplicated": _decision("deduplicated", "deduplicated:cache:refined"),
    }

    assert fine_screen.summarize_decision_sources(decisions) == {
        "fresh_base": 1,
        "fresh_refined": 1,
        "cache_hits": 1,
        "deduplicated": 1,
        "other": 0,
    }


def test_true_gap_count_does_not_block_selection() -> None:
    match = _match(_job("many-gaps"))
    match.true_gap = {f"gap-{index}" for index in range(12)}

    assert fine_screen.is_selection_candidate(match, 0.70)


def test_reset_archives_only_exact_generated_cv_block(candidate, monkeypatch) -> None:
    job = _job()
    expected = (
        f"{candidate.legacy_file_slugs[0]}_CV_example-devices-device-integration-engineer.pdf"
    )
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        fine_screen,
        "inspect_existing_fine_screened",
        lambda _token, _jobs, _candidate: ([job], 1),
    )
    monkeypatch.setattr(
        fine_screen,
        "list_page_file_blocks",
        lambda _token, _page: [
            NotionFileBlock("generated", expected),
            NotionFileBlock("manual", "Candidate_Portfolio.pdf"),
        ],
    )

    def fake_request(_token: str, endpoint: str, method: str, _body: dict | None = None) -> dict:
        calls.append((method, endpoint))
        return {}

    monkeypatch.setattr(fine_screen, "notion_request", fake_request)

    pages, blocks = fine_screen.reset_existing_fine_screened("token", [job], candidate)

    assert (pages, blocks) == (1, 1)
    assert ("DELETE", "https://api.notion.com/v1/blocks/generated") in calls
    assert all("manual" not in endpoint for _method, endpoint in calls)


def test_fine_screen_query_treats_missing_screen_property_as_empty(monkeypatch) -> None:
    def fake_request(_token: str, endpoint: str, _method: str, _body: dict | None = None) -> dict:
        if endpoint.endswith("missing/query"):
            raise RuntimeError(
                'Notion API 400: {"message":"Could not find property with name or id: Screen"}'
            )
        return {"results": [{"id": "page-1"}], "has_more": False}

    monkeypatch.setattr(fine_screen, "notion_request", fake_request)

    assert fine_screen.fine_screened_page_ids("token", {"missing", "present"}) == {"page-1"}


def test_finalized_screen_status_is_explicit_and_fail_closed() -> None:
    assert (
        fine_screen.finalized_screen_tag(
            "selected", selected_ids={"selected"}, errors={}, selection_blocks={}
        )
        == fine_screen.SCREEN_TAG
    )
    assert (
        fine_screen.finalized_screen_tag(
            "rejected", selected_ids=set(), errors={}, selection_blocks={}
        )
        == fine_screen.SCREEN_REJECTED_TAG
    )
    assert (
        fine_screen.finalized_screen_tag(
            "blocked",
            selected_ids={"blocked"},
            errors={},
            selection_blocks={"blocked": "safety"},
        )
        == fine_screen.SCREEN_BLOCKED_TAG
    )
    assert (
        fine_screen.finalized_screen_tag(
            "error",
            selected_ids={"error"},
            errors={"error": "invalid output"},
            selection_blocks={"error": "safety"},
        )
        == fine_screen.SCREEN_ERROR_TAG
    )


def test_screen_property_contains_every_finalized_display_status(monkeypatch) -> None:
    calls: list[dict] = []
    monkeypatch.setattr(
        fine_screen,
        "notion_request",
        lambda _token, _endpoint, _method, body=None: calls.append(body) or {},
    )

    fine_screen.ensure_screen_property("token", "database", set())

    options = calls[0]["properties"][fine_screen.SCREEN_PROPERTY]["select"]["options"]
    assert {option["name"] for option in options} == set(fine_screen.SCREEN_OPTIONS)


def test_replacing_attachment_archives_exact_old_generated_block(
    candidate, monkeypatch, tmp_path: Path
) -> None:
    job = _job("replace-attachment")
    pdf = fine_screen.fine_screen_pdf_path(tmp_path, job, candidate)
    pdf.parent.mkdir(parents=True)
    pdf.write_bytes(b"pdf")
    old_name = f"{candidate.legacy_file_slugs[0]}_CV_{fine_screen.application_slug(job)}.pdf"
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        fine_screen,
        "list_page_file_blocks",
        lambda _token, _page: [NotionFileBlock("old-block", old_name)],
    )
    monkeypatch.setattr(
        fine_screen,
        "notion_request",
        lambda _token, endpoint, method, _body=None: calls.append((method, endpoint)) or {},
    )
    monkeypatch.setattr(fine_screen, "attach_one_pdf", lambda *_args, **_kwargs: True)

    assert fine_screen.attach_application_pdfs(
        "token", "page", tmp_path, job, candidate, replace_existing=True
    )
    assert calls == [("DELETE", "https://api.notion.com/v1/blocks/old-block")]


def test_replacing_attachment_preserves_old_block_when_upload_fails(
    candidate, monkeypatch, tmp_path: Path
) -> None:
    job = _job("replace-attachment-failure")
    pdf = fine_screen.fine_screen_pdf_path(tmp_path, job, candidate)
    pdf.parent.mkdir(parents=True)
    pdf.write_bytes(b"pdf")
    old_name = f"{candidate.legacy_file_slugs[0]}_CV_{fine_screen.application_slug(job)}.pdf"
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        fine_screen,
        "list_page_file_blocks",
        lambda _token, _page: [NotionFileBlock("old-block", old_name)],
    )
    monkeypatch.setattr(
        fine_screen,
        "notion_request",
        lambda _token, endpoint, method, _body=None: calls.append((method, endpoint)) or {},
    )
    monkeypatch.setattr(fine_screen, "attach_one_pdf", lambda *_args, **_kwargs: False)

    assert not fine_screen.attach_application_pdfs(
        "token", "page", tmp_path, job, candidate, replace_existing=True
    )
    assert calls == []


def test_apply_requires_notion_token(monkeypatch) -> None:
    monkeypatch.delenv("NOTION_INTEGRATION_TOKEN", raising=False)

    assert fine_screen.main(["--apply"]) == 2


def test_explicit_job_slice_flags_are_bounded() -> None:
    args = fine_screen.parse_args(
        ["--job-id", "job-1", "--job-id", "job-2", "--expect-job-count", "2"]
    )

    assert args.job_id == ["job-1", "job-2"]
    assert args.expect_job_count == 2


def test_custom_provider_requires_explicit_trust() -> None:
    assert (
        fine_screen.main(["--agent-provider", "command", "--agent-command-json", '["agent"]']) == 2
    )


def test_default_opportunity_score_is_point_seven() -> None:
    args = fine_screen.parse_args([])

    assert args.min_score == 0.70
    assert args.max_refinement_calls == 100
    assert args.positions_root is None
    assert fine_screen.parse_job_scraper_argv(args.job_scraper_command_json) == ["job-scraper"]
    assert not hasattr(args, "limit_per_track")


def test_job_scraper_command_is_validated_as_argv() -> None:
    assert fine_screen.parse_job_scraper_argv('["uv", "run", "job-scraper"]') == [
        "uv",
        "run",
        "job-scraper",
    ]
    with pytest.raises(ValueError, match="JSON argv array"):
        fine_screen.parse_job_scraper_argv("uv run job-scraper")


def test_per_track_limit_option_is_removed() -> None:
    try:
        fine_screen.parse_args(["--limit-per-track", "1"])
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("removed per-track limit option was unexpectedly accepted")


def test_failed_positions_pull_uses_no_shell_and_returns_nonzero(
    monkeypatch, tmp_path: Path
) -> None:
    recorded: dict[str, object] = {}

    def fake_run(argv: list[str], **kwargs: object) -> object:
        recorded["argv"] = argv
        recorded.update(kwargs)
        return type("Completed", (), {"returncode": 7})()

    monkeypatch.setattr(fine_screen.subprocess, "run", fake_run)

    assert (
        fine_screen.run_positions_pull(
            tmp_path, ["uv", "run", "job-scraper"], ["--post-age-days", "2"]
        )
        == 7
    )
    assert recorded["argv"] == [
        "uv",
        "run",
        "job-scraper",
        "run",
        "--post-age-days",
        "2",
    ]
    assert recorded["shell"] is False


def test_installed_job_scraper_needs_no_checkout(monkeypatch) -> None:
    recorded: dict[str, object] = {}

    def fake_run(argv: list[str], **kwargs: object) -> object:
        recorded["argv"] = argv
        recorded.update(kwargs)
        return type("Completed", (), {"returncode": 0, "stdout": "{}", "stderr": ""})()

    monkeypatch.setattr(fine_screen.subprocess, "run", fake_run)

    assert (
        fine_screen.read_feed(
            None,
            ["job-scraper"],
            ["ai"],
            ["--since-days", "1"],
            published_only=False,
        )
        == "{}"
    )
    assert recorded["argv"] == [
        "job-scraper",
        "feed",
        "--since-days",
        "1",
        "--profile",
        "ai",
    ]
    assert recorded["cwd"] is None
    assert recorded["shell"] is False


def test_result_persistence_uses_typed_cli_boundary_without_a_shell(
    monkeypatch, tmp_path: Path
) -> None:
    recorded: dict[str, object] = {}

    def fake_run(argv: list[str], **kwargs: object) -> object:
        recorded["argv"] = argv
        recorded.update(kwargs)
        return type("Completed", (), {"returncode": 0, "stdout": "IMPORTED", "stderr": ""})()

    monkeypatch.setattr(fine_screen.subprocess, "run", fake_run)
    result_path = tmp_path / "results.json"

    fine_screen.persist_screening_results(tmp_path, ["uv", "run", "job-scraper"], result_path)

    assert recorded["argv"] == [
        "uv",
        "run",
        "job-scraper",
        "db",
        "import-screening",
        str(result_path),
    ]
    assert recorded["shell"] is False


def test_finalized_publication_is_count_bounded_and_uses_no_shell(
    monkeypatch, tmp_path: Path
) -> None:
    recorded: dict[str, object] = {}

    def fake_run(argv: list[str], **kwargs: object) -> object:
        recorded["argv"] = argv
        recorded.update(kwargs)
        return type("Completed", (), {"returncode": 0, "stdout": "PUBLISHED", "stderr": ""})()

    monkeypatch.setattr(fine_screen.subprocess, "run", fake_run)
    result_path = tmp_path / "results.json"

    fine_screen.publish_finalized_results(
        tmp_path, ["uv", "run", "job-scraper"], result_path, expected_count=3
    )

    assert recorded["argv"] == [
        "uv",
        "run",
        "job-scraper",
        "db",
        "publish-screening",
        str(result_path),
        "--expect-job-count",
        "3",
    ]
    assert recorded["shell"] is False


def _feed_document(**overrides) -> str:
    document = {
        "schema_version": fine_screen.FEED_SCHEMA_VERSION,
        "generated_at": "2026-08-27T00:00:00+00:00",
        "window": {
            "since": "2026-08-25T22:00:00+00:00",
            "until": "2026-08-26T22:00:00+00:00",
        },
        "record_count": 1,
        "records": [
            {
                "job_id": "job-1",
                "profile_id": "cpp",
                "processing_mode": "core",
                "title": "Senior C++ Engineer",
                "company": "Example GmbH",
                "location": "Berlin",
                "employment_type": "full-time",
                "language": "en",
                "url": "https://example.invalid/jobs/1",
                "description": "Write C++.",
                "first_seen_at": "2026-08-26T09:00:00+00:00",
                "application_status": "new",
                "publication": {
                    "sink_id": "notion_daily",
                    "external_id": "page-1",
                    "container_id": "database-1",
                },
            }
        ],
    }
    document.update(overrides)
    return json.dumps(document)


def test_feed_records_map_onto_jobs() -> None:
    jobs, since, until = fine_screen.parse_feed_document(_feed_document())

    assert len(jobs) == 1
    job = jobs[0]
    assert job.id == "job-1"
    assert job.track == "cpp"
    assert job.title == "Senior C++ Engineer"
    assert job.description_full == "Write C++."
    assert job.notion_page_id == "page-1"
    assert job.notion_data_source_id == "database-1"
    assert job.processing_mode == "core"
    assert job.employment_type == "full-time"
    assert since == "2026-08-25T22:00:00+00:00"
    assert until == "2026-08-26T22:00:00+00:00"


def test_job_for_prompt_carries_the_raw_fields_the_agent_now_judges() -> None:
    """`job-scraper` no longer pre-judges country/employment-type/language;
    the agent needs the raw fields to make that call itself."""
    job = _job()
    job.location = "Munich"
    job.employment_type = "Werkstudent"

    profile = fine_screen.job_for_prompt(job)

    assert profile["location_raw"] == "Munich"
    assert profile["employment_type"] == "Werkstudent"
    assert profile["posted_age_hours"] != ""


@pytest.mark.parametrize(
    ("age_hours", "expected_bucket"),
    [(1, "<24h"), (48, "1-3d"), (100, "3-7d"), (500, "1-4w"), (900, ">4w")],
)
def test_posted_age_bucket_matches_expected_range(age_hours: float, expected_bucket: str) -> None:
    from datetime import UTC, datetime, timedelta

    first_seen_at = (datetime.now(UTC) - timedelta(hours=age_hours)).isoformat()

    assert fine_screen._posted_age_hours(first_seen_at) == expected_bucket


def test_posted_age_bucket_is_stable_within_a_bucket() -> None:
    """The bucket -- not a precise age -- feeds the cache key, so re-running
    screening seconds later on the same job must not look like a new job."""
    from datetime import UTC, datetime, timedelta

    first_seen_at = (datetime.now(UTC) - timedelta(hours=1)).isoformat()

    first = fine_screen._posted_age_hours(first_seen_at)
    second = fine_screen._posted_age_hours(first_seen_at)

    assert first == second == "<24h"


def test_posted_age_bucket_is_empty_for_an_unparseable_timestamp() -> None:
    assert fine_screen._posted_age_hours("not-a-timestamp") == ""


def test_result_handoff_preserves_processing_policy(tmp_path: Path) -> None:
    core = _job("core-1")
    discovery = _job("discovery-1", track="market")
    discovery.processing_mode = "discovery"
    decision = AgentDecision(
        job_id=core.id,
        variant="systems",
        core_fit="direct",
        daily_work="Build integrations.",
        covered=("C++",),
        addable=(),
        true_gap=("Fictional gap",),
        score=0.82,
        rationale="Supported fit.",
        source="agent",
    )
    path = tmp_path / "results.json"

    fine_screen.write_screening_result_document(
        path,
        [core, discovery],
        {core.id: decision},
        {core.id},
        {},
        {},
        {},
    )

    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["schema_version"] == 1
    assert document["record_count"] == 2
    assert document["records"][0]["processing_mode"] == "core"
    assert document["records"][1]["status"] == "retained"
    assert document["records"][1]["tailoring_status"] == "not_applicable"


def test_discovery_jobs_now_carry_a_real_decision_but_stay_retained(tmp_path: Path) -> None:
    """Discovery has no hard pre-feed filter left to rely on: it now gets a
    real agent decision (visible score/core_fit/rationale), but its `status`
    stays "retained" -- discovery is a visibility track, not a gated one, and
    never generates a resume regardless of fit."""
    discovery = _job("discovery-1", track="market")
    discovery.processing_mode = "discovery"
    decision = AgentDecision(
        job_id=discovery.id,
        variant=None,
        core_fit="none",
        daily_work="Unrelated retail management work.",
        covered=(),
        addable=(),
        true_gap=("wrong country",),
        score=0.05,
        rationale="Job is based outside the candidate's target countries.",
        source="agent",
    )
    path = tmp_path / "results.json"

    fine_screen.write_screening_result_document(
        path,
        [discovery],
        {discovery.id: decision},
        set(),
        {},
        {},
        {},
    )

    document = json.loads(path.read_text(encoding="utf-8"))
    record = document["records"][0]
    assert record["status"] == "retained"
    assert record["tailoring_status"] == "not_applicable"
    assert record["score"] == 0.05
    assert record["core_fit"] == "none"
    assert record["rationale"] == "Job is based outside the candidate's target countries."


def test_an_unknown_feed_version_is_refused() -> None:
    """Guessing at an unfamiliar document would generate real applications."""
    with pytest.raises(fine_screen.FeedError, match="schema_version"):
        fine_screen.parse_feed_document(_feed_document(schema_version=99))


def test_a_missing_feed_version_is_refused() -> None:
    payload = json.loads(_feed_document())
    del payload["schema_version"]

    with pytest.raises(fine_screen.FeedError, match="schema_version"):
        fine_screen.parse_feed_document(json.dumps(payload))


def test_non_json_feed_output_is_reported_not_crashed() -> None:
    with pytest.raises(fine_screen.FeedError, match="not JSON"):
        fine_screen.parse_feed_document("Traceback (most recent call last):")


def test_an_empty_feed_is_not_an_error() -> None:
    jobs, _, _ = fine_screen.parse_feed_document(_feed_document(records=[], record_count=0))

    assert jobs == []


def test_apply_with_skip_tailoring_is_refused() -> None:
    """Together they would report success having produced nothing."""
    assert fine_screen.main(["--apply", "--skip-tailoring"]) == 2


def test_skip_tailoring_is_accepted_on_its_own() -> None:
    parsed = fine_screen.parse_args(["--skip-tailoring"])

    assert parsed.skip_tailoring is True
    assert parsed.apply is False


def test_report_still_marks_selections_when_tailoring_is_skipped(tmp_path: Path) -> None:
    """--skip-tailoring must not empty the selection it is reporting on.

    Matches are normally dropped when they have no tailoring plan, because there
    that means the agent failed. With tailoring skipped nothing was ever asked
    for, so the same rule would report zero selections for a screen that really
    did select -- which is precisely the verdict the flag exists to surface.
    """
    job = _job("kept-without-plan")
    report = tmp_path / "cpp-report.csv"

    fine_screen.write_agent_report(
        report,
        [job],
        {job.id: _decision(job.id, "agent")},
        {job.id},
        {},
        {},
        {},
    )

    rows = list(csv.reader(report.read_text(encoding="utf-8").splitlines()))
    header, body = rows[0], rows[1:]
    assert len(body) == 1
    selected_column = header.index("selected")
    assert body[0][selected_column].strip().lower() in {"true", "yes", "1"}
