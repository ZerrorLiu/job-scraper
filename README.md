# Job Scraper

A composable, agent-friendly job discovery library. It provides reusable
collection, normalization, filtering, persistence, and publishing components
without shipping anyone's search profile or workspace configuration.

## What is included

- LinkedIn public-job collection
- Indeed collection through Bright Data datasets
- Local Indeed collection through your connected Chrome, with SQLite and CSV output
- IMAP recommendation-email ingestion
- Typed domain records and composable pipeline steps
- SQLite, cumulative CSV, and Notion adapters
- A versioned JSON feed for a downstream screener
- Profile orchestration, request coalescing, bounded concurrency, and a live
  terminal dashboard
- Offline, credential-free quality checks

The repository intentionally contains no profiles, search terms, locations,
company watchlists, Notion workspace names, personal documents, or runtime
data. Those live in the ignored `config/`, `data/`, and `.env` workspace on
each installation.

## Installation

For Indeed on one computer, start with
[the local Chrome setup](docs/public/agent-deployment.md#local-indeed-with-connected-chrome).
It needs no VPS, paid scraper, mailbox, or separate client repository. An
interactive agent with a connected Chrome performs the page reading; installing
the Python package alone does not control a browser.

This project is installed and configured with an agent rather than distributed
as an executable. You describe what you want to find; the agent generates your
private workspace.

Requirements are Python 3.11+, [uv](https://docs.astral.sh/uv/getting-started/installation/),
and credentials only for the integrations you enable. The default composition —
LinkedIn into a CSV file — needs no credentials and no accounts.

```bash
uv sync --extra dev
cp .env.example .env
uv run job-scraper capabilities --json
```

Then hand the agent [the deployment runbook](docs/public/agent-deployment.md).
It is the complete procedure: the interview, credential acquisition for each
optional integration, workspace generation, and the offline verification
ladder. A minimal deployment is one `init` command:

```bash
uv run job-scraper init \
  --profile-id PROFILE_ID --label "PROFILE_LABEL" \
  --query "SEARCH_QUERY" --location "SEARCH_LOCATION" --country COUNTRY_CODE \
  --keyword "FILTER_SIGNAL" --timezone "AREA/CITY" \
  --source linkedin_direct --sink csv --processing-mode core
```

`init` writes only to the ignored private workspace and refuses to overwrite
existing configuration. Secrets are saved once in `.env`. A workspace outside
the repository is selected with `JOB_SCRAPER_CONFIG_DIR`.

## Running

```bash
uv run job-scraper doctor --all         # runtime, credentials, destinations
uv run job-scraper plan --show-queries  # preview the deduplicated plan
uv run job-scraper run                  # every enabled profile and its outputs
```

Profiles, sources, and queries are discovered from the private workspace, so
the normal daily command takes no arguments and no activation flags. See
[operations](docs/public/operations.md) for exceptional runs, the workspace
database, the downstream feed, email repair, exports, and scheduling.

## Architecture

```text
CLI / adapters
      |
      v
use-case orchestration
      |
      v
ports + domain <- composable pipeline
      |
      v
repositories / CSV / Notion / feed
```

Dependencies point inward: domain and ports never depend on a vendor SDK,
network transport, CLI, or database. A local profile selects component IDs; the
public repository supplies implementations but no selection.

## Documentation

Start at [the documentation map](docs/public/README.md). It separates the
install-and-run documents from the change-the-code documents.

Contributors and agents read [`AGENTS.md`](AGENTS.md) first — it is
authoritative on the privacy boundary, architecture rules, and quality gates.

## Quality checks

```bash
uv run ruff format --check .
uv run ruff check .
uv run pyright
uv run pytest
```

Default tests are offline and require no credentials. Live integration checks
must be explicitly selected with `pytest -m live`.
