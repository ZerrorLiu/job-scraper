# Agent Guide

## Mission

Maintain this repository as a composable, agent-friendly job discovery
library. Keep the public framework independent of any user's search strategy,
identity, credentials, or external workspace layout.

## Environment

- Python: `>=3.11`
- Environment and lock file: `uv`
- Install: `uv sync --extra dev`
- CLI: `uv run job-scraper ...`
- Default tests must be offline and credential-free.

## Privacy boundary

Never commit runtime configuration. The complete `config/` directory is a
private workspace and is intentionally ignored. Also never commit `.env`,
cookies, mailbox content or passwords, tokens, personal documents, databases,
logs, exports, or real external-service payloads.

Public examples and tests must use fictional, neutral values. Do not encode a
user's profiles, queries, target locations, watchlists, page names, database
IDs, or employment preferences as source defaults.

Do not delete existing CSV or SQLite files during normal development.

## Architecture rules

Dependency direction:

```text
CLI / concrete adapters -> application -> ports / domain
pipeline -> ports / domain
```

- `domain/` contains business values and policies. It must not import
  adapters, CLI, databases, external services, collectors, or network code.
- `ports/` contains stable structural interfaces and depends only on domain
  types.
- `pipeline/` contains small composable evaluation steps.
- `application/` orchestrates use cases through ports; do not construct
  concrete adapters there.
- `adapters/` translate external payloads and SDK errors at the boundary.
- `registry/` is the composition mechanism. Do not add a dependency-injection
  framework.
- Configuration is supplied at runtime from the ignored private workspace.
- Avoid import-time side effects: no environment loading, network calls,
  database creation, or file mutation during import.

The built-in production acquisition adapters are `linkedin_direct`,
`indeed_brightdata`, and `email_imap`. Fixture collectors are test-only.
Historical source values may remain readable for data compatibility.

## Extend before you add

This repository is developed in many short sessions by agents that do not share
memory. The failure mode is not bad code; it is accumulation — a second
document covering what one already covered, a parallel module beside the one
that needed editing, a `_v2` name that outlives its migration. Every such
addition is cheap to make and expensive forever after, because the next agent
must now read both and cannot tell which is authoritative.

So: **the default action is to change an existing thing. Creating a new one is
the exception, and it must be justified.**

### The survey

Before creating any new file, module, class, CLI command, configuration key,
environment variable, or document, find where the concern already lives:

```bash
rg -n "<the concept, and its likely synonyms>" src public_tests docs
ls docs/public/ && ls src/job_scraper/*/          # what homes already exist
uv run job-scraper capabilities --json            # registered component IDs
git log --oneline -15                             # what recently moved
```

Then answer, in the handoff:

- which existing file, module, or document is the closest home;
- why extending it is not the right change.

"I did not find one" is only an acceptable answer after the searches above.
Not finding an existing home because you searched for your own new name rather
than the concept is the most common way this rule is broken.

### Rules

- **One home per concern.** The tables in [Documentation](#documentation) and
  in [`docs/public/README.md`](docs/public/README.md) are binding for
  documentation. For code, the boundary is the Port: a new behavior is a new
  adapter or step behind an existing Port far more often than it is a new
  subsystem.
- **Replace, do not accumulate.** When a change supersedes existing behavior,
  documentation, or tests, remove what it supersedes in the same change. A
  superseded thing left in place is not neutral; it is a false signal.
- **No parallel versions.** No `_v2`, `_new`, `_improved`, `_final` module,
  document, or function coexisting with what it replaces. Migrate and delete,
  or do not start. When a migration genuinely needs two live paths, the spec
  states the removal condition and the change that removes it.
- **No near-duplicate names.** A name differing only by separator, case, or a
  synonym — `extension_guide.md` beside `extension-guide.md` — is treated as a
  collision, not as two files. Search before naming.
- **Deprecation ends.** Anything kept for compatibility carries, in its spec, a
  stated condition for removal. Hidden or suppressed CLI surface is deprecated
  surface: it is listed in the spec that introduced the deprecation, not left
  undocumented.
- **Reuse the vocabulary.** Before introducing a term for a concept, check what
  the domain already calls it. Two names for one thing costs more than an
  imperfect name.

### Deleting versus archiving

- **Delete outright:** anything regenerable — `build/`, `__pycache__/`,
  `.pytest_cache/`, `.coverage`, `*.egg-info/`, empty directories.
- **Delete outright:** anything tracked in Git that a change supersedes. Git
  history is the archive; a commit is how it stays recoverable.
- **Archive under `local/`:** superseded material that was never in version
  control, where deletion would be irreversible. `local/` is ignored and exists
  only in an installation, not in this repository. Give it a `README.md`
  stating what each archived item is and what replaced it.
- **Never** archive by leaving something in place and adding a comment saying
  it is unused.

## Extension workflow

1. Choose the relevant Port: Source, Channel, PipelineStep, Repository, or Sink.
2. Implement the adapter or pure step without modifying the core runner.
3. Register a stable component ID in `registry/builtins.py`.
4. Configure it only in the local private workspace.
5. Add unit and contract tests using fake transports or local fictional
   fixtures.
6. Update the public extension and configuration references.

External raw payloads may use `object` at the adapter boundary. Convert them
into typed domain values before entering application or pipeline code. A
business rejection is a `Decision`, not an exception.

Per-port detail is in
[`docs/public/extension-guide.md`](docs/public/extension-guide.md).

## Documentation

[`docs/public/README.md`](docs/public/README.md) is the map. Documentation is
split by audience, and a change belongs in exactly one place:

| Kind of information | Home |
|---|---|
| The procedure for installing and configuring an installation | `docs/public/agent-deployment.md` |
| How to decide what goes into a profile, and which components to offer | `docs/public/profile-design.md` |
| Everyday commands for an existing installation | `docs/public/operations.md` |
| What a configuration key does, and the shape a sink writes | `docs/public/configuration.md` |
| Layers, concurrency, external writes, schema evolution | `docs/public/architecture.md` |
| How to add a source, step, or sink | `docs/public/extension-guide.md` |
| Why a behavior is the way it is | `docs/public/specs/<date>-<slug>.md` |
| Repository law: privacy, architecture, gates | this file |

`README.md` is a navigation page. It states what the project is, the shortest
path to a working installation, and where to go next; it does not accumulate
operational detail. Keep procedural instructions out of specs, and keep
rationale out of the workflow document.

Root-level Markdown is limited to `README.md`, `AGENTS.md`, `CONTRIBUTING.md`,
and `SECURITY.md`. Working notes, migration write-ups, and superseded designs
belong in the ignored `local/` archive, not in `docs/`.

## Quality gates

Run before handing work back:

```powershell
uv run ruff format --check .
uv run ruff check .
uv run pyright
uv run pytest
```

When changing Domain, Pipeline, or configuration behavior, keep focused branch
coverage for changed core behavior at or above 90%. Use `pytest -m live` only
when the user explicitly requests live verification and credentials are
available.

## Development operating mode

Use the repository workflow in
[`docs/public/agent-development-workflow.md`](docs/public/agent-development-workflow.md)
for every change. It is the project's agent harness. For implementation work,
read the project-local skill at
[`skills/job-scraper-development/SKILL.md`](skills/job-scraper-development/SKILL.md)
before acting.

### Workflow priority

- For code-changing work, use the installed `vibe-flow` skill first to select the
  smallest suitable process, then follow this repository's workflow and constraints.
- Prefer the installed Vibe Skills for routing, bootstrap, and fresh verification.
  The mandatory `job-scraper-development` skill remains required for implementation.
  Any other older, prompt-only local skill is supplementary; when guidance conflicts,
  use this `AGENTS.md` and the mandatory project-local skill.
- This repository's privacy, architecture, and quality-gate rules remain authoritative
  over generic Vibe defaults.

1. Discuss the requested outcome first. Restate scope, acceptance criteria,
   constraints, and meaningful trade-offs. Ask for a decision only when it
   cannot be safely inferred.
2. Before implementation, create or update a neutral specification under
   `docs/public/specs/` using
   [`docs/public/spec-template.md`](docs/public/spec-template.md). Update the
   relevant public architecture, configuration, or extension documentation in
   the same documentation phase when its contract changes.
3. Implement in small, reviewable steps, then add or update focused offline
   tests. Run the quality gates before handoff.
4. For a non-trivial change to a CLI flow, configuration, public extension
   point, public API, or architecture, use an independent subagent to walk the
   intended user path from the request and visible artifacts. Do not expose the
   intended solution or private runtime data. Summarize actionable friction and
   follow-up suggestions. Skip this only for a mechanical, behavior-preserving
   change and state why.

Treat a specification as the source of truth for why and what changes; keep
procedural instructions in the workflow document. Do not let either contain
private search strategy, identities, credentials, workspace IDs, or runtime
payloads.

## Data safety

- Migrations must be idempotent, support dry-run behavior, and never
  modify/delete their source databases.
- CSV output stays cumulative by default.
- Preserve original platform provenance for jobs entering through email.
- Resolve exact paths before any destructive operation.
