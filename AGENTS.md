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

1. Choose the relevant Port: Source, Channel, PipelineStep, Repository, Sink,
   or StatusGateway.
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

## Data safety

- Migrations must be idempotent, support dry-run behavior, and never
  modify/delete their source databases.
- CSV output stays cumulative by default.
- Preserve original platform provenance for jobs entering through email.
- Resolve exact paths before any destructive operation.
