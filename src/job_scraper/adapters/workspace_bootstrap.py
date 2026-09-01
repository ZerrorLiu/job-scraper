"""Create a private fine-screen workspace from a first uploaded resume."""

from __future__ import annotations

import re
import shutil
from hashlib import sha256
from importlib.resources import as_file, files
from pathlib import Path


def candidate_slug(name: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_-]+", "", name.replace(" ", "_"))[:64].strip("_-")
    return slug or f"Candidate_{sha256(name.encode()).hexdigest()[:8]}"


def initialize_candidate_workspace(
    root: Path,
    *,
    candidate_name: str,
    email: str,
    resume_text: str,
    original_bytes: bytes,
    original_suffix: str,
) -> None:
    name = " ".join(candidate_name.split()).strip()
    if not name or len(name) > 120:
        raise ValueError("Candidate name must be 1 to 120 characters")
    config_path = root / "workspace.toml"
    if config_path.is_file():
        return
    if root.exists() and any(root.iterdir()):
        raise ValueError("Candidate workspace is non-empty but has no workspace.toml")
    template = files("fine_screen").joinpath("templates/workspace")
    with as_file(template) as template_path:
        shutil.copytree(template_path, root)
    slug = candidate_slug(name)
    config = config_path.read_text(encoding="utf-8")
    config = config.replace('name = "Your Name"', f'name = "{_toml(name)}"')
    config = config.replace('file_slug = "YourName"', f'file_slug = "{slug}"')
    config_path.write_text(config, encoding="utf-8")

    shared = root / "shared"
    shared.mkdir(parents=True, exist_ok=True)
    (shared / "contact.tex").write_text(
        "\\newcommand{\\resumeHeader}{\\begin{center}{\\Large\\textbf{"
        + _tex(name)
        + "}}\\\\\\href{mailto:"
        + _tex(email)
        + "}{"
        + _tex(email)
        + "}\\end{center}}\n",
        encoding="utf-8",
    )
    lines = [" ".join(line.split()).strip() for line in resume_text.splitlines()]
    lines = [line for line in lines if len(line) >= 8][:40]
    bullets = "\n".join(f"  \\resumeBullet{{{_tex(line[:500])}}}" for line in lines)
    imported = (
        "\\documentclass[a4paper,10pt]{article}\n"
        "\\input{../../shared/contact.tex}\n"
        "\\input{../shared/resume-style.tex}\n"
        "\\begin{document}\n\\resumeHeader\n"
        "\\resumeHeadline{Profile imported from the approved source resume.}\n"
        "\\section{Skills}\n\\begin{itemize}[leftmargin=*]\n"
        "% FINE_SCREEN_SKILLS_INSERTION_POINT\n\\end{itemize}\n"
        "\\section{Experience}\n"
        "\\resumeSubheading{Resume evidence}{}{Source resume}{}\n"
        "\\resumeItemListStart\n" + bullets + "\n\\resumeItemListEnd\n\\end{document}\n"
    )
    (root / "resume/variants/imported.tex").write_text(imported, encoding="utf-8")
    blank = root / "resume/variants/blank.tex"
    if blank.is_file():
        blank.unlink()
    source = root / "resume/source"
    source.mkdir(parents=True, exist_ok=True)
    suffix = original_suffix if original_suffix in {".pdf", ".docx"} else ".bin"
    (source / f"original{suffix}").write_bytes(original_bytes)


def _tex(value: str) -> str:
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
    return "".join(replacements.get(character, character) for character in value)


def _toml(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')
