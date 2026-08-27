# Operations

Everyday commands for an installation that is already deployed. For a first
installation, use the [deployment runbook](agent-deployment.md).

Every command reads the private workspace, so profiles, sources, and queries
never appear on the command line. A workspace outside the repository is
selected with `JOB_SCRAPER_CONFIG_DIR`.

## Daily run

```bash
uv run job-scraper run
```

This executes every enabled profile with the sources, channels, and sinks its
configuration selected. There are no activation flags in the normal path: a
source chosen during `init` is already enabled in the workspace.

The default mailbox lookback follows the widest configured online freshness
window, so one command covers the complete workflow consistently.

Exceptional runs:

```bash
uv run job-scraper run --profile PROFILE_ID     # one profile only
uv run job-scraper run --post-age-days 7        # temporary freshness override
uv run job-scraper run --skip-notion            # acquire and export, publish nothing
uv run job-scraper run --skip-email             # skip mailbox ingestion
uv run job-scraper run --skip-export            # skip the cumulative CSV
uv run job-scraper run --profile-workers 1      # serialize profiles
```

## Inspect before running

```bash
uv run job-scraper list                   # profiles and their enabled state
uv run job-scraper plan --show-queries    # the deduplicated search plan
uv run job-scraper doctor --all           # runtime, credentials, destinations
uv run job-scraper config validate --all  # configuration well-formedness
```

`plan` and `doctor` contact nothing. Run them after any configuration edit.

## Workspace database

```bash
uv run job-scraper db status   # row counts, pending migrations, Notion bindings
uv run job-scraper db init     # create each profile's database, run nothing
uv run job-scraper db migrate  # consolidate profile databases into the workspace
```

Schema migrations are not a separate command. The store applies its ordered,
recorded migrations whenever it initializes, so a normal run brings the schema
forward on its own. They are idempotent and never drop data.

`db status` is read-only. It reports row counts, any migration the workspace
has not applied, and any table the current schema no longer defines — which is
how a workspace that outlived a removed feature makes itself visible instead of
drifting silently. It also shows each profile's stored Notion database binding,
or `unbound`, without contacting Notion.

`db migrate` is the separate, data-level step that copies each profile's own
database into the shared canonical workspace database. Preview it first, and
note that it refuses to run when the destination resolves to a profile's own
source database:

```bash
uv run job-scraper db migrate --dry-run
uv run job-scraper db migrate --workspace PATH   # explicit destination
```

It reads its sources and never modifies or deletes them.

## Downstream screening feed

A downstream screener reads acquired jobs through the feed contract rather than
by opening the database directly. The document is versioned JSON on stdout, or
written to a path with `--output`.

```bash
uv run job-scraper feed                              # yesterday onward, all profiles
uv run job-scraper feed --profile PROFILE_ID
uv run job-scraper feed --since-days 7
uv run job-scraper feed --since-date 2026-08-01 --until-date 2026-08-07
uv run job-scraper feed --published-only             # only jobs a sink has published
uv run job-scraper feed --include-settled            # keep applied / not-interested
```

Window boundaries are counted in the profile's timezone and reported back in
the document, alongside `schema_version`, `generated_at`, and `record_count`.
Jobs already settled as applied or not interested are dropped unless
`--include-settled` is given.

## Repairing email detail enrichment

Indeed jobs discovered in recommendation emails are enriched before filtering.
Transient `408`, `429`, and `5xx` responses are retried with backoff; listing
URLs run in small concurrent batches, and a failing batch is split until a
persistently bad URL is isolated, so one vendor error does not downgrade every
email job.

To deliberately revisit recently processed messages after an upstream outage:

```bash
uv run job-scraper ingest-email --reprocess --lookback-days 1
```

This is a repair command, not the normal daily entry point. It ignores the
processed-message state, evaluates the same email cards against every enabled
email profile, and keeps normal database and Notion deduplication. Add
`--skip-status-import` when current Notion job decisions do not need to be
imported before the repair.

## Manual decisions are authoritative

Jobs marked `Applied` or `Not Interested` in Notion are normalized locally and
excluded from later candidate processing. A matching repost is suppressed for
30 days using normalized title, company, and location history; unrelated roles
from the same company are unaffected.

## Exports

Every run rewrites the complete filtered history into
`<export_dir>/<prefix>_<date>.csv`, so the directory grows by one full export
per run. `[project] retained_exports` bounds how many dated files of a series
are kept. It defaults to `0`, meaning no pruning. Pruning only considers files
matching the series a sink writes, so other profiles' exports and hand-written
files in the same directory are never touched.

## Scheduling

The project has no built-in scheduler. Drive `job-scraper run` from the host's
own scheduler — a systemd timer, `cron`, or Task Scheduler — with the working
directory set to the installation and `JOB_SCRAPER_CONFIG_DIR` set when the
workspace lives elsewhere. A scheduled run publishes to external systems on the
user's behalf, so enable one only with their explicit go-ahead.

## When something looks wrong

| Symptom | First check |
|---|---|
| No jobs at all | `plan --show-queries`, then the freshness window in `[project]` |
| A profile is skipped | `list` for its enabled state, then `config validate --all` |
| Notion writes nothing | `doctor --all`, then `db status` for the binding |
| Notion made a duplicate table | `daily_table_prefix` or `container_title` was changed; see [configuration](configuration.md) |
| Indeed returns nothing | expected without the Bright Data gates; `doctor --all` names the gate |
| Mailbox ingestion is empty | the app password and IMAP folder in `config/email.toml` |
