"""The private workspace this screener reads and writes.

Everything personal lives in a workspace directory that this package never
ships: resume templates, the evidence library, the skills whitelist, and every
generated application. The package supplies the mechanism and the layout
contract; the workspace supplies the content and the identity.

That split is what makes this repository publishable. It is also what lets one
installation serve more than one candidate -- the workspace path is an argument,
not a constant.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python < 3.11 fallback
    import tomli as tomllib  # type: ignore[no-redef]

WORKSPACE_CONFIG_NAME = "workspace.toml"

# Every generated file starts with the candidate's slug, so a bare directory
# listing still says whose application it is. Kept restrictive because these
# become filenames on three operating systems and a Notion attachment name.
_SLUG_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


class WorkspaceError(ValueError):
    """The workspace is missing, malformed, or not laid out as promised."""


@dataclass(frozen=True, slots=True)
class Candidate:
    """Who the generated applications belong to.

    `file_slug` is the only spelling new files use. `legacy_file_slugs` exists
    solely so that cleanup and replacement still recognize files written by an
    earlier naming scheme -- dropping one would orphan those files in the
    external workspace rather than archiving them, which is silent data loss.
    Never write with a legacy slug; only match against it.
    """

    name: str
    file_slug: str
    legacy_file_slugs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise WorkspaceError("[candidate] name must not be empty")
        for slug in (self.file_slug, *self.legacy_file_slugs):
            if not _SLUG_PATTERN.match(slug):
                raise WorkspaceError(
                    f"candidate slug {slug!r} must be letters, digits, '-' or '_', "
                    "and start with a letter or digit; it becomes a filename"
                )


@dataclass(frozen=True, slots=True)
class Workspace:
    """A resolved private workspace.

    Paths are properties rather than stored fields so that a workspace moved on
    disk cannot half-update, and so the layout is documented in one place.
    """

    root: Path
    candidate: Candidate
    _config: dict = field(default_factory=dict, repr=False)

    # -- inputs the operator maintains -------------------------------------

    @property
    def resume_variants_dir(self) -> Path:
        """`.tex` templates, one per direction, each with the skills marker."""
        return self.root / "resume" / "variants"

    @property
    def resume_shared_dir(self) -> Path:
        return self.root / "resume" / "shared"

    @property
    def shared_dir(self) -> Path:
        """Fragments the templates `\\input`, plus the two data files below."""
        return self.root / "shared"

    @property
    def skills_whitelist_path(self) -> Path:
        return self.shared_dir / "quick-learn-skills.toml"

    @property
    def evidence_library_path(self) -> Path:
        return self.shared_dir / "evidence-library.json"

    @property
    def profile_notes_path(self) -> Path:
        """Free-form facts the tailoring agent may draw on, and must not exceed."""
        return self.shared_dir / "profile-notes.md"

    # -- outputs this screener writes --------------------------------------

    @property
    def applications_dir(self) -> Path:
        return self.root / "cover-letter" / "applications"

    @property
    def generated_variants_dir(self) -> Path:
        return self.resume_variants_dir / "applications"

    @property
    def output_pdf_dir(self) -> Path:
        return self.root / "CV" / "Fine-Screened"

    @property
    def reports_dir(self) -> Path:
        return self.root / "analysis" / "fine-screen"

    @property
    def archive_dir(self) -> Path:
        return self.root / "archive" / "fine-screen"

    @property
    def agent_cache_dir(self) -> Path:
        return self.reports_dir / "agent-cache"

    @property
    def vendor_dir(self) -> Path:
        return self.root / "vendor"

    def require_inputs(self) -> None:
        """Fail before doing any work if the workspace cannot supply its half.

        Checked up front rather than at first use: this screener calls a paid
        agent and writes into an external workspace, so discovering a missing
        template halfway through leaves a partial batch behind.
        """
        missing = [
            path
            for path in (
                self.resume_variants_dir,
                self.skills_whitelist_path,
                self.profile_notes_path,
                self.evidence_library_path,
            )
            if not path.exists()
        ]
        if missing:
            listed = ", ".join(str(path.relative_to(self.root)) for path in missing)
            raise WorkspaceError(
                f"workspace {self.root} is missing: {listed}. "
                "See the template under `fine_screen/templates/workspace`."
            )


def load_workspace(root: Path) -> Workspace:
    """Read `workspace.toml` from a workspace root.

    The config is required rather than defaulted. A screener that silently
    invented a candidate name would write real files under it.
    """
    root = root.expanduser().resolve()
    config_path = root / WORKSPACE_CONFIG_NAME
    if not config_path.is_file():
        raise WorkspaceError(
            f"no {WORKSPACE_CONFIG_NAME} in {root}. "
            "Copy the one under `fine_screen/templates/workspace` and edit it."
        )
    try:
        config = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise WorkspaceError(f"{config_path} is not valid TOML: {exc}") from exc

    candidate_config = config.get("candidate")
    if not isinstance(candidate_config, dict):
        raise WorkspaceError(f"{config_path} has no [candidate] table")

    unknown = sorted(set(candidate_config) - {"name", "file_slug", "legacy_file_slugs"})
    if unknown:
        raise WorkspaceError(f"[candidate] has unknown fields: {', '.join(unknown)}")

    try:
        name = str(candidate_config["name"])
        file_slug = str(candidate_config["file_slug"])
    except KeyError as exc:
        raise WorkspaceError(f"[candidate] is missing {exc.args[0]!r}") from exc

    legacy = candidate_config.get("legacy_file_slugs", [])
    if not isinstance(legacy, list):
        raise WorkspaceError("[candidate] legacy_file_slugs must be an array")

    return Workspace(
        root=root,
        candidate=Candidate(
            name=name,
            file_slug=file_slug,
            legacy_file_slugs=tuple(str(slug) for slug in legacy),
        ),
        _config=config,
    )
