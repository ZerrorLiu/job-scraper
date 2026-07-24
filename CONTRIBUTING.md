# Contributing

Contributions should preserve the repository's composable architecture and keep default tests
offline and credential-free.

## Development setup

1. Install Python 3.11+ and [uv](https://docs.astral.sh/uv/getting-started/installation/).
2. Run `uv sync --extra dev`.
3. Copy `.env.example` to `.env` only when local integrations are needed.

Never commit the private `config/` workspace, `.env`, credentials, browser
profiles, mail content, databases, logs, exports, personal documents, search
queries, locations, watchlists, or external workspace identifiers.

## Before opening a pull request

```powershell
uv run ruff format --check .
uv run ruff check .
uv run pyright
uv run pytest
```

New adapters should implement an existing Port, register a stable component
ID, use fake transports in tests, and update
`docs/public/extension-guide.md`.
