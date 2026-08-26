# Agent deployment playbook

This repository deliberately separates deployment input from source code. An
agent should never infer or commit a user's search strategy.

## First conversation

Ask the user for:

1. Which acquisition adapters to enable.
2. How many independent profiles they want and the label for each.
3. Search queries and locations for each profile.
4. Filtering policy, including countries, excluded terms, employment scope,
   language handling, and optional company watchlists.
5. Which sinks to enable and the desired external workspace layout.

Ask for credentials only when the matching integration is enabled. Save local
credentials in `.env`, or inject them through the cloud runner's secret store.
Never print, copy into TOML, or commit them.

## Deployment sequence

1. Install Python 3.11+ and uv.
2. Run `uv sync --extra dev`.
3. Run `job-scraper capabilities --json` and use
   `schemas/agent-bootstrap.schema.json` to form the deployment inputs.
4. Run `job-scraper init` once per requested profile. This creates the private
   configuration tree under ignored `config/`, or under
   `JOB_SCRAPER_CONFIG_DIR`.
5. Create `.env` locally or inject equivalent environment secrets. For Notion,
   set `NOTION_INTEGRATION_TOKEN` and configure the non-secret target
   page/database ID for each profile. A normal `job-scraper run` may create,
   rename, or write Notion content through its configured sink and needs the
   owner's explicit approval.
6. Run `job-scraper config validate --all` and `job-scraper doctor --all`.
7. Run `job-scraper db init` as an offline database check.
8. Run one bounded live profile, inspect the summary, then run
   `job-scraper run` for the complete enabled composition.

The Agent generates queries and filtering keywords from the user's own
experience and preferences; the repository supplies no defaults for them.
`init` refuses to overwrite an existing profile, so subsequent changes are
explicit edits followed by validation.

The normal command has no activation flags: `job-scraper run` executes all
enabled profiles and their configured sources, channels, and sinks. Use
`--profile`, `--skip-email`, `--skip-notion`, `--skip-export`, or a freshness
override only for exceptional runs.

## Cloud agents

Cloud agents may clone and modify the public repository without receiving the
owner's local configuration. A new user lets the Agent generate their private
workspace. An existing user injects their own workspace and secrets at job
runtime. Treat both as deployment inputs, not repository content.

Before proposing or pushing a change, the agent must run the quality gates in
`AGENTS.md` and confirm that `git status --ignored` still classifies all
runtime workspace files as ignored.
