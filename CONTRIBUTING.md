# Contributing

[`AGENTS.md`](AGENTS.md) is authoritative for this repository: the privacy
boundary, the architecture rules, the extension workflow, and the quality
gates. Read it before making a change. This page is the short human-facing
summary.

## Setup

1. Install Python 3.11+ and [uv](https://docs.astral.sh/uv/getting-started/installation/).
2. Run `uv sync --extra dev`.
3. Copy `.env.example` to `.env` only when local integrations are needed.

## The rule that matters most

Never commit the private `config/` workspace, `.env`, credentials, mail
content, databases, logs, exports, personal documents, search queries,
locations, watchlists, or external workspace identifiers. Public examples and
tests use fictional, neutral values.

Verify with `git status --ignored --short` before pushing.

## Before opening a pull request

```bash
uv run ruff format --check .
uv run ruff check .
uv run pyright
uv run pytest
```

Default tests must stay offline and credential-free.

## Adding a component

Implement an existing Port, register a stable component ID in
`registry/builtins.py`, use fake transports in tests, and update
[the extension guide](docs/public/extension-guide.md) and
[the configuration reference](docs/public/configuration.md).
[The development guide](docs/public/agent-development-workflow.md) describes
optional planning and review practices to choose according to the change.
