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
uv run job-scraper run --source SOURCE_ID       # only this source of the profile
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

This is the compatibility path for the current separate screener. An
orchestrator may first invoke `job-scraper run` with an argv list and continue
to screening only when it returns exit code `0`. Screening-only operation skips
that acquisition call and reads already acquired state through `job-scraper
feed`. Do not build a shell command string, and do not open or modify the source
SQLite database from the downstream process.

The planned unified workflow replaces this orchestration only after replay and
controlled cutover. It screens and validates artifacts before Notion is updated;
the current feed remains available during migration and rollback.

The migration handoff is also versioned. After a separate screener writes its
`screening-*-results.json`, validate and persist it in the configured shared
workspace before any final display reconciliation:

```bash
uv run job-scraper db init
uv run job-scraper db import-screening PATH_TO_RESULTS_JSON
uv run job-scraper db publish-screening PATH_TO_RESULTS_JSON --expect-job-count COUNT
```

The import is idempotent for the same canonical job, search track, and contract
version. It fails closed when the feed job has no V1-to-canonical crosswalk,
when the result mode differs from the current profile policy, when the
workspace needs a migration, or when the document is malformed. All supplied
documents are validated and committed as one transaction; a later bad record
cannot leave an earlier partial import behind.

`publish-screening` is the bounded final-display transition. It refuses a
handoff that does not exactly match durable canonical state, then publishes the
corresponding stored jobs through the existing idempotent Notion sink. Resume
artifacts must validate before this command is called; a caller may then refresh
the feed to obtain page IDs and attach authorized artifacts.

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

## Networked Codex/Chrome browser worker

The production browser lane uses one tenant-local FastAPI process and SQLite
queue/outbox. Start only one Uvicorn worker for a client instance:

```bash
uv run job-scraper serve --host 127.0.0.1 --port 8123 \
  --portal-db /private/positions/portal.db \
  --upload-root /private/positions/uploads \
  --public-url https://positions.example \
  --candidate-workspace /private/positions/candidate-workspace
uv run job-scraper serve-enroll-token --ttl-seconds 3600
uv run job-scraper browser-search-refresh
uv run job-scraper browser-email-refresh
```

Put authenticated HTTPS ingress in front of the loopback port. The enrollment
token is single use; deliver it out of band and never store it in Git or logs.

For a token-managed Cloudflare Tunnel, add the public hostname in **Zero Trust
→ Networks → Tunnels → Public Hostnames** and route it to
`http://127.0.0.1:8500`. Keep Uvicorn bound to loopback; do not open port 8500
in the VPS firewall. Verify both `https://HOST/healthz` and the authenticated
browser-worker contract after saving the hostname. Cloudflare Access must not
be placed in front of `/v1/browser/*` until `positions-client` has explicit
Access service-token headers; its device bearer token already authenticates
that API. Cloudflare rate limits/WAF may still protect `/login`.

Install `deploy/systemd/positions-web.env.example` as the private mode-0600
`/etc/positions/positions-web.env`, then install the updated
`positions-browser-api.service`. The same loopback process serves Web and API.
Run accepted results outside the HTTP process:

```bash
uv run job-scraper browser outbox run --limit 100
uv run job-scraper browser status
uv run job-scraper browser outbox list --state failed
uv run job-scraper browser outbox retry --event-id browser:TASK_ID --expect-count 1
uv run job-scraper browser revoke-device --device-id DEVICE --expect-count 1
```

The Web portal uses passwordless email login. Set `POSITIONS_SMTP_HOST`,
`POSITIONS_SMTP_PORT`, `POSITIONS_SMTP_FROM`, and, when required,
`POSITIONS_SMTP_USERNAME`/`POSITIONS_SMTP_PASSWORD` in the service secret
store. `--dev-print-login-link` and `--insecure-cookie` are local development
options only. Uploaded resumes live under `--upload-root`, outside Git and the
Web root.

Production resume analysis uses a shell-free, read-only `codex exec` call with
a strict output schema. `--resume-analysis-model` optionally pins its model.
`--dev-basic-resume-analysis` is a local fixture mode and must not be used to
activate a real user's profile.

Screening, evidence-bound tailoring, PDF generation, result persistence, and
release verification use the bundled `fine-screen` and `fine-screen-release`
commands. A separate fine-screen code installation is no longer part of the
runtime; continue to pass the private workspace explicitly.
When `--candidate-workspace` is configured, the authenticated Web portal lists
the latest screening CSV results, run manifests, and generated PDFs. Downloads
are restricted to exact PDF filenames under that workspace's
`CV/Fine-Screened` directory.
The first resume upload may bootstrap an empty workspace. Review the generated
`resume/variants/imported.tex` before the first apply run; it is deliberately
plain and evidence-preserving, not a claim that arbitrary PDF layout was
reconstructed. Re-uploading never replaces an existing workspace.

The outbox command expands completed search cards into detail tasks and imports
completed details through the existing pipeline, with Notion last. Automatic
processing attempts stop after five failures; retry only an inspected exact
event. `--skip-notion` keeps a run local. The local worker commands and Chrome
behavior are documented in the `positions-client` repository and its
`positions-browser-worker` plugin.

## Legacy local browser detail repair

The JSONL lane remains a local migration fallback while network cutover is
verified. When an authorised local browser can render an Indeed listing but direct HTTP
or Bright Data cannot, emit a local-only queue from email recommendations:

```bash
uv run job-scraper ingest-email --browser-queue local/indeed-browser-queue.jsonl
```

If the existing mailbox subject filters do not select the relevant historic
recommendations, add an explicit sender substring and a bounded larger mailbox
window. This affects only queue export, not a normal email run:

```bash
uv run job-scraper ingest-email --browser-queue local/indeed-browser-queue.jsonl --browser-sender indeed --max-messages 200 --lookback-days 90
```

This queue contains only unique Indeed `viewjob` URLs and their local email
card context. It does not publish jobs, change mailbox state, control a
browser, or enable Bright Data. Lease exactly one task before using the local
interactive browser:

```bash
uv run job-scraper ingest-email --browser-claim local/indeed-browser-queue.jsonl
```

The command prints that row as JSON and refuses a second lease until the row
is resolved. The interactive agent updates the claimed row in the same file
with `status: "complete"`, a full `title`, `company_name`, `location_raw`, and
`description`. A `blocked` or `unavailable` row must carry an `error` and is
retained as the local recovery record instead of being retried automatically.

Import completed rows through the normal email pipeline:

```bash
uv run job-scraper ingest-email --browser-results local/indeed-browser-queue.jsonl
```

The importer accepts one canonical URL per task, rejects login/blocking pages
and incomplete descriptions, applies normal database and publication
deduplication, and checkpoints an accepted row as `imported`. Add
`--skip-notion` to make the import local-only. Do not place these JSONL files
in the repository: they contain private mailbox context.

## Legacy local browser search discovery

The same local browser workflow can discover new Indeed listing URLs from the
existing private track query/location matrix. It makes no network request and
does not enable the Bright Data source:

```bash
uv run job-scraper ingest-email --browser-search-queue local/indeed-browser-search.jsonl
uv run job-scraper ingest-email --browser-search-claim local/indeed-browser-search.jsonl
```

The search queue reads the existing track config paths from the private email
configuration only to obtain their query/location matrix; it does not connect
to IMAP. Use `--track-config PATH` to choose a narrower matrix explicitly.

The second command leases one search page. The interactive browser operator
updates the row with `status: "complete"` and a `cards` list. Every card needs
a canonical Indeed `viewjob` URL plus visible `title`, `company_name`,
`location_raw`, and `context`; a blocked or unavailable search gets its terminal
status and an `error` instead. A completed search with no matching cards is
valid and will be checkpointed as expanded. The following fictional shapes are
the full payload changes after a task has been claimed:

```json
{"status":"complete","cards":[{"url":"https://de.indeed.com/viewjob?jk=fictional","title":"Platform Engineer","company_name":"Example GmbH","location_raw":"Berlin","context":"Visible result-card summary."}]}
{"status":"complete","cards":[]}
{"status":"blocked","error":"Browser showed a CAPTCHA."}
```

Keep the other fields from the claimed JSON row unchanged. If an operator stops
before recording an outcome, first confirm no browser is working that task,
then change that same row back to `pending` and remove `lease_started_at`; do
not create a second row. A browser block, login, or CAPTCHA should instead be
recorded as `blocked` or `unavailable` with its error.

Expand completed search rows into the existing detail queue, then use the
detail lease and import commands above:

```bash
uv run job-scraper ingest-email --browser-search-results local/indeed-browser-search.jsonl --browser-detail-queue local/indeed-browser-queue.jsonl
```

Search and detail queues must be separate files. Both are one-browser-lane
checkpoints; there is no background browser, headless/VPS mode, login, or
CAPTCHA bypass. Jobs discovered this way retain `indeed` as their job-source
provenance once their final detail is imported.

## Manual decisions are authoritative

Jobs marked `Not Interested` in Notion are normalized locally and excluded
from later candidate processing. A matching repost is suppressed for 30 days
using normalized title, company, and location history; unrelated roles from
the same company are unaffected. `Applied` remains recorded for downstream
status and audit, but does not suppress an otherwise matching candidate.
Changing a Notion status back to `Not Applied` clears the local exclusion on
the next status import. Re-eligible does not mean the Notion sink creates a
duplicate page: publication retains its own page-level idempotency.

If a historical Notion page has a stale local mapping whose job no longer
exists, status import ignores that mapping and continues. It does not repair or
delete the historical page; valid mapped pages and safe fallback matches still
import normally.

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

### Browser task services

The reference units in `deploy/systemd/` keep the browser API and transactional
outbox on an always-on Linux installation, and refresh the client-private search
and email task queues on their own cadence. Install them only after replacing
the reference installation path and service account where necessary:

```bash
sudo cp deploy/systemd/positions-browser-* /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now positions-browser-api.service
sudo systemctl enable --now positions-browser-outbox.timer
sudo systemctl enable --now positions-browser-refresh.timer
```

`positions-browser-refresh.timer` creates server-side work; it does not drive a
browser. A recurring Codex task on the client machine claims that work through
`positions-client` and uses that client's assigned Chrome profile. When the
client is off, work remains pending and the VPS continues its non-browser jobs.
Check server state with `job-scraper browser status` and client state with
`positions-client doctor` and `positions-client status`.

### Giving one source its own cadence

A profile's `sources` list says which adapters the track uses, not how often
each should run. When one source wants a different schedule from the rest —
typically a board sweep that spends one request per configured employer, where
running it daily multiplies load on a third party for postings that move
slowly — split it across two timers with `--source` rather than duplicating the
track as a second profile:

```bash
# daily timer: everything except the sweep
uv run job-scraper run --profile PROFILE_ID --source SOURCE_A --source SOURCE_B

# weekly timer: the sweep alone
uv run job-scraper run --profile PROFILE_ID --source SOURCE_C
```

Both timers name what they run, so a later profile edit cannot silently change
either one's scope. Selecting a source the profile does not enable is an error
rather than a quiet no-op, because a timer that acquires nothing after such an
edit is otherwise indistinguishable from one whose source found nothing new.

The freshness window must be at least the sweep interval. `FreshnessStep`
filters on `posted_at`, a fixed value, so a posting older than the window is
rejected on every future run, not just the current one — a weekly sweep under a
window shorter than a week loses whatever appeared between runs, permanently.

## When something looks wrong

| Symptom | First check |
|---|---|
| No jobs at all | `plan --show-queries`, then the freshness window in `[project]` |
| A profile is skipped | `list` for its enabled state, then `config validate --all` |
| Notion writes nothing | `doctor --all`, then `db status` for the binding |
| Jobs were published but a downstream scheduled action did not run | inspect the run's final status-import error before the host scheduler's success trigger |
| Notion made a duplicate table | `daily_table_prefix` or `container_title` was changed; see [configuration](configuration.md) |
| Indeed returns nothing | expected without the Bright Data gates; `doctor --all` names the gate |
| Mailbox ingestion is empty | the app password and IMAP folder in `config/email.toml` |
