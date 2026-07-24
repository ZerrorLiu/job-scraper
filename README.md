# Job Scraper

A composable, agent-friendly job discovery library. It provides reusable
collection, normalization, filtering, persistence, and publishing components
without shipping anyone's search profile or workspace configuration.

## What is included

- LinkedIn public-job collection
- Indeed collection through Bright Data datasets
- IMAP recommendation-email ingestion
- Typed domain records and composable pipeline steps
- SQLite, cumulative CSV, and Notion adapters
- Profile orchestration, request coalescing, bounded concurrency, and a live
  terminal dashboard
- Offline, credential-free quality checks

The repository intentionally contains no profiles, search terms, locations,
company watchlists, Notion workspace names, personal documents, or runtime
data. Those live in the ignored `config/`, `data/`, and `.env` workspace on
each installation.

## Agent-first setup

This project is designed to be installed and configured with an agent rather
than distributed as an executable.

Requirements:

- Python 3.11 or newer
- [uv](https://docs.astral.sh/uv/getting-started/installation/)
- Credentials only for the integrations the user enables

After cloning:

```powershell
uv sync --extra dev
Copy-Item .env.example .env
uv run job-scraper capabilities --json
```

Ask the Agent to turn the user's requirements into one or more `init`
commands. For each profile it supplies explicit queries, locations, countries,
filter keywords, source IDs, and sink IDs:

```powershell
uv run job-scraper init `
  --profile-id PROFILE_ID `
  --label "PROFILE_LABEL" `
  --query "SEARCH_QUERY" `
  --location "SEARCH_LOCATION" `
  --country COUNTRY_CODE `
  --keyword "FILTER_SIGNAL" `
  --source linkedin_direct `
  --source indeed_brightdata `
  --sink csv `
  --sink notion_daily `
  --email `
  --imap-host "IMAP_HOST"
```

Repeat `--query`, `--location`, `--country`, and `--keyword` as needed.
Repeat `init` for additional independent profiles. The command writes only to
the ignored private workspace and refuses to overwrite existing configuration.

The Agent can use
[the deployment playbook](docs/public/agent-deployment.md) and
[configuration reference](docs/public/configuration.md), then validate it:

```powershell
uv run job-scraper list
uv run job-scraper config validate --all
uv run job-scraper doctor --all
uv run job-scraper db init
```

Secrets are saved once in `.env`; they do not need to be entered for every
run. A configuration directory outside the repository can be selected with
`JOB_SCRAPER_CONFIG_DIR`.

## Running

```powershell
# Preview the deduplicated plan
uv run job-scraper plan --show-queries

# Run all enabled profiles, configured sources, channels, and sinks
uv run job-scraper run

# Run only one local profile
uv run job-scraper run --profile <profile-id>

# Apply a temporary freshness override
uv run job-scraper run --post-age-days 7
```

Profiles, sources, and queries are discovered from the private workspace.
Every source selected during `init` is enabled there; daily runs do not need
source-specific activation flags. The default mailbox lookback follows the
widest configured online freshness window so one command covers the complete
workflow consistently.

## Architecture

```text
CLI / adapters
      |
      v
application orchestration
      |
      v
ports + domain <- composable pipeline
      |
      v
repositories / CSV / Notion
```

See [architecture](docs/public/architecture.md) and the
[extension guide](docs/public/extension-guide.md).

## Quality checks

```powershell
uv run ruff format --check .
uv run ruff check .
uv run pyright
uv run pytest
```

Default tests are offline and require no credentials. Live integration checks
must be explicitly selected with `pytest -m live`.
