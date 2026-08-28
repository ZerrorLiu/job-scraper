# Agent deployment runbook

This document is the complete procedure for taking a new user from a fresh
clone to a working installation. It is written for an agent to execute. It is
self-contained: no other document is required to finish a deployment.

The repository ships implementations but no selection. A deployment consists of
generating the user's private workspace and supplying only the credentials
their chosen integrations need. Never infer or commit a user's search strategy.

## 0. The short path

The default composition needs **no credentials and no accounts**. If the user
just wants job discovery into a CSV file, the entire deployment is:

```bash
uv sync --extra dev
cp .env.example .env
uv run job-scraper init \
  --profile-id PROFILE_ID --label "PROFILE_LABEL" \
  --query "SEARCH_QUERY" --location "SEARCH_LOCATION" --country COUNTRY_CODE \
  --keyword "FILTER_SIGNAL" --timezone "AREA/CITY" \
  --source linkedin_direct --sink csv --processing-mode core
uv run job-scraper doctor --all
uv run job-scraper db init
```

Everything below exists for the optional integrations. Read section 3 only for
the ones the user actually enables.

## 1. Preconditions

Verify before asking the user anything:

```bash
python --version          # 3.11 or newer
uv --version              # https://docs.astral.sh/uv/getting-started/installation/
uv sync --extra dev
uv run pytest             # offline, credential-free; must pass before deploying
```

A failing suite on a clean clone is a repository problem, not a deployment
problem. Stop and report it rather than working around it.

Then read the machine-readable component list. It is authoritative; do not
deploy against IDs remembered from this document:

```bash
uv run job-scraper capabilities --json
```

It returns the registered `sources`, `channels`, `pipeline_steps`, `sinks`, and
the `credential_environment` each integration requires. The accepted bootstrap
input shape is in `schemas/agent-bootstrap.schema.json`.

## 2. The interview

Collect these before generating anything. Ask in this order and stop as soon as
the user has enough for one working profile — additional profiles can be added
later, and `init` refuses to overwrite an existing one.

Ask about the user's search, not about the configuration. They do not know what
a match scope is, which keyword field filters and which only annotates, or what
a Notion table prefix will do six weeks from now. Those are the agent's
decisions. [Designing a profile](profile-design.md) is the method for making
them; read it before the interview, not after.

1. **Profiles.** How many independent search directions, a label for each, and
   whether each is `core` (screen + resume), `review` (screen only), or
   `discovery` (visibility only).
   One profile is one search matrix with one filtering policy and one output
   set. Separate profiles when the filtering policy genuinely differs, not
   merely because the job titles differ.
2. **Search matrix.** Per profile: the search queries and the locations. These
   are combined into a deduplicated plan; do not pre-multiply them by hand.
3. **Country and timezone.** The country code each profile filters to, and the
   user's IANA timezone (`Europe/Berlin`, `Asia/Shanghai`). The timezone
   determines daily table naming and freshness windows; passing `--timezone`
   at `init` is easier than editing the runtime TOML afterwards.
4. **Filter signals.** Keywords that indicate a job is relevant. Derive them
   from the user's own stated experience. The repository supplies none.
5. **Sources.** `linkedin_direct` is free and needs nothing.
   `indeed_brightdata` is paid and optional — see 3.3 before offering it.
6. **Sinks.** `csv` needs nothing. `notion_daily` needs an account and setup —
   see 3.1.
7. **Recommendation email.** Only if the user already receives job
   recommendation mail and wants it ingested — see 3.2.
8. **Screening.** If the deployment will keep using the current separate
   screener and write verdicts back to existing Notion objects, keep
   `notion_daily` enabled and read the
   [screening-feed compatibility section](profile-design.md#the-screening-feed-compatibility-interface).
   A deployment intended only for the future unified workflow does not need to
   pre-publish jobs for screening; that workflow finalizes upstream state before
   updating Notion.

The optional components, what each one costs, and which are interface-only are
tabulated in
[Designing a profile](profile-design.md#optional-and-reserved-components).
Offer a paid or unbuilt component honestly or not at all.

Ask for a credential only when its integration has been chosen. Never ask the
user to paste a credential into chat when they can write it into `.env`
themselves; if they do paste one, do not echo it back, and never write it into
a TOML file, a spec, a test, or a commit.

## 3. Credential acquisition

Each subsection states what artifact is needed, where the user obtains it, and
how to confirm it works. Provider UI labels change; match on intent rather than
on exact wording.

### 3.1 Notion (`notion_daily` sink)

Needs `NOTION_INTEGRATION_TOKEN` plus one destination identifier.

The token is an **internal integration** secret — not the user's password, and
not a public OAuth application:

1. The user opens their Notion integrations settings
   (`https://www.notion.so/my-integrations`) and creates a new **internal**
   integration associated with the workspace the jobs should land in.
2. They copy its integration secret into `.env` as `NOTION_INTEGRATION_TOKEN`.

A new integration has access to nothing. The user must then grant it the parent
page explicitly, or every call returns `404`:

3. They open the Notion page that should contain the daily job tables, open
   that page's overflow menu, find the connections/access entry, and add the
   integration created in step 1.
4. They copy that page's ID into `.env` as `NOTION_PARENT_PAGE_ID`. It is the
   32-character identifier in the page URL, after the title slug and before any
   `?` query string. Hyphenated and unhyphenated forms are both accepted.

`NOTION_DATABASE_ID` is an alternative to `NOTION_PARENT_PAGE_ID` when the
destination is an existing database rather than a container page. Set one of
them; `doctor` reports an error when neither is present.

Confirm with `uv run job-scraper doctor --all`. A `404` after this point means
step 3 did not take effect: the token is valid, but the integration was never
granted that page.

**Do not change `container_title` or `daily_table_prefix` afterwards.** Notion
tables are first resolved by title. Renaming either value silently creates a
duplicate table and orphans the existing one. After the first successful
publishing run the profile stores a database ID binding locally, which makes
later runs immune to renames — but that binding does not exist yet during
deployment. See the Notion sections of
[the configuration reference](configuration.md).

### 3.2 Recommendation email (`email_imap` channel)

Needs `JOB_EMAIL_USERNAME` and `JOB_EMAIL_APP_PASSWORD`, plus an IMAP host
passed to `init` as `--imap-host`.

The password must be a provider-issued **application password**, not the
account's login password — providers reject the login password over IMAP once
two-factor authentication is on. For a Google account the user enables 2-Step
Verification first, then generates an app password from their account security
settings; other providers have an equivalent. The user must also confirm that
IMAP access is enabled in their mail settings.

`init --email --imap-host HOST` writes `config/email.toml`. The mailbox folder,
lookback window, and message limits live there and can be edited afterwards.

### 3.3 Bright Data (`indeed_brightdata` source)

Needs `BRIGHTDATA_API_KEY` and `BRIGHTDATA_DATASET_ID` from a paid Bright Data
account, plus an explicit opt-in flag.

Offer this source only when the user asks for Indeed coverage and accepts the
cost. It is a low-yield addition for most searches, and it is deliberately
inert by default: credentials alone never submit a paid snapshot. Direct
collection additionally requires `BRIGHTDATA_DIRECT_COLLECTION_ENABLED=true`,
and enriching email-discovered Indeed jobs additionally requires
`BRIGHTDATA_EMAIL_DETAIL_ENRICHMENT_ENABLED=true`. `doctor` reports which of
the two gates is holding a profile back without contacting the vendor.

The optional `BRIGHTDATA_WEBHOOK_URL` / `BRIGHTDATA_WEBHOOK_TOKEN` pair makes
snapshot acquisition asynchronous and requires a reachable receiver. Leave both
blank unless the user has one; the synchronous path is the default.

## 4. Generate the workspace

Run `init` once per profile. It writes only into the ignored private workspace
and refuses to overwrite an existing profile, so a mistake is corrected by an
explicit edit plus re-validation rather than by re-running it. Which steps are
safe to repeat, and what re-deploying onto another machine actually requires,
are tabulated in
[Designing a profile](profile-design.md#idempotency-and-re-deployment).

```bash
uv run job-scraper init \
  --profile-id PROFILE_ID \
  --label "PROFILE_LABEL" \
  --query "SEARCH_QUERY" \
  --location "SEARCH_LOCATION" \
  --country COUNTRY_CODE \
  --keyword "FILTER_SIGNAL" \
  --timezone "AREA/CITY" \
  --source linkedin_direct \
  --sink csv
```

Repeat `--query`, `--location`, `--country`, `--keyword`, `--source`, and
`--sink` as needed. Add `--email --imap-host HOST` for mailbox ingestion, and
`--step` to override the default pipeline order.

This creates:

```text
config/
  profiles/<profile-id>.toml   # composition: which components, which matrix
  <profile-id>.toml            # runtime: filters, http, per-source settings
  email.toml                   # only with --email
```

The whole tree is private. Confirm it stays that way before any commit:

```bash
git status --ignored --short
```

`config/`, `.env`, `data/`, and `exports/` must appear as ignored, never as
staged or untracked-to-be-added.

To place the workspace outside the repository, set `JOB_SCRAPER_CONFIG_DIR`
before running any command, or pass `--config-dir` to `init`.

Then write the secrets. Copy `.env.example` to `.env` and fill only the
variables the chosen integrations require. Leave everything else blank.

## 5. Verify offline

Run the full ladder. Every step here is offline and free.

```bash
uv run job-scraper list                  # profiles are discovered and enabled
uv run job-scraper config validate --all # TOML is well-formed, IDs are real
uv run job-scraper doctor --all          # credentials and destinations resolve
uv run job-scraper db init               # create the operational databases
uv run job-scraper plan --show-queries   # the deduplicated search plan
```

`plan` is the last checkpoint before anything is spent or published. Read it
back to the user: it shows exactly which query and location intents will be
issued. A plan with more intents than expected usually means queries and
locations were pre-multiplied by hand during the interview.

## 6. Interpreting `doctor`

| Report | Meaning | Action |
|---|---|---|
| `OK profile` | Composition loads and every component ID is registered | none |
| `ERROR notion destination` | Neither `NOTION_PARENT_PAGE_ID` nor `NOTION_DATABASE_ID` is set | finish 3.1 step 4 |
| `WARNING notion: optional credentials missing` | Sink selected, token absent | finish 3.1 steps 1–2 |
| `WARNING email credentials` | Channel selected, mailbox credentials absent | finish 3.2 |
| `OK brightdata direct: disabled by the effective profile source list` | Source not selected | none; expected |
| `OK brightdata direct: suspended; ...` | Source selected but the environment gate is false | set the flag only with the user's cost consent |
| `OK privacy: no known local secret files detected` | No stray credential files in the tree | none |

A `WARNING` means an integration the user selected cannot run yet. An `ERROR`
means the run would fail. Neither is a reason to edit library code.

## 7. First live run

A live run makes outbound requests, and with `notion_daily` it **creates and
writes external content in the user's workspace**. Get explicit approval, then
start bounded:

```bash
uv run job-scraper run --profile PROFILE_ID --skip-notion
```

Inspect the summary and the CSV export. When acquisition and filtering look
right, run the complete composition:

```bash
uv run job-scraper run
```

`run` needs no activation flags: it executes every enabled profile with the
sources, channels, and sinks its private configuration selected.
`--skip-email`, `--skip-notion`, `--skip-export`, `--post-age-days`, and
`--query` exist for exceptional runs only.

Do not use a live run as a casual verification step against Notion. The first
successful publishing run is what establishes the database ID binding, and a
misconfigured title at that moment is what creates a duplicate table.

## 8. Hand off

Tell the user, in their own terms:

- which profiles exist and what each one searches for;
- where their data lands — the database, the export directory, and any external
  workspace;
- which integrations are active and which are deliberately inert;
- that `config/` and `.env` are their private workspace, are not in Git, and
  are not preserved by cloning the repository;
- how to run it again, and how to schedule it if they want it daily.

For everyday commands after deployment, see [operations](operations.md). For
every configuration key, see [the configuration reference](configuration.md).

## 9. Hard rules

- Never commit `config/`, `.env`, databases, exports, logs, or mailbox content.
- Never write a credential into TOML, a spec, a test fixture, or a commit
  message.
- Never encode a user's queries, locations, watchlists, employer names, or
  workspace identifiers as library defaults.
- Never enable a paid source without the user's explicit consent to the cost.
- Never perform an external write — Notion content, mailbox state — without the
  user's explicit approval for that run.
- Never delete or overwrite an existing CSV or SQLite workspace during
  deployment.

## Cloud and multi-machine deployments

A cloud agent may clone and modify the public repository without ever receiving
the owner's workspace. A new user has the agent generate a workspace; an
existing user injects their own workspace and secrets at job runtime, through
the runner's secret store rather than a committed file. Treat both as
deployment inputs, not repository content.

Because the workspace is not in Git, moving an existing installation to another
machine means copying `config/`, `.env`, and the database. Cloning the
repository alone produces an empty installation.
