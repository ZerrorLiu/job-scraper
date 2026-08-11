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
