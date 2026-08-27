# Private configuration reference

The repository does not ship runtime configuration. An Agent generates it with
`job-scraper init` under the ignored `config/` directory, or in another
directory selected by `JOB_SCRAPER_CONFIG_DIR`. The accepted bootstrap input
is machine-readable in `schemas/agent-bootstrap.schema.json`.

## Workspace layout

```text
config/
  defaults.toml
  profiles/
    <profile-id>.toml
  <runtime-config>.toml
  data/
    notion_database_bindings.json # created locally after an existing table is resolved
  email.toml                 # only when IMAP is enabled
  watchlists.toml            # optional
```

Every value in this tree is local. Do not force-add it to Git.

## Profile definition

Each `profiles/<profile-id>.toml` contains a `[profile]` table. Supported keys:

- `id`, `label`, `enabled`
- `runtime_config`: relative path to the full runtime TOML
- `sources`, `channels`, `pipeline`, `sinks`: registered component IDs
- `base_queries`, `locations`, `early_career_modifiers`: the profile's single
  search matrix definition
- `watchlists`: optional local watchlist IDs

`defaults.toml` may provide the same keys in `[defaults]`. Optional
per-profile overrides can live in `[profiles.<profile-id>]` inside
`local.toml`.

Built-in IDs:

- Sources: `linkedin_direct`, `indeed_brightdata`
- Channel: `email_imap`
- Pipeline: `country`, `freshness`, `company`, `employment_scope`,
  `excluded_terms`, `role`, `requirement_exclusion`, `language`
- Sinks: `csv`, `notion_daily`

## Runtime TOML

The full runtime file has these tables:

- `[project]`: timezone, database/export paths, labels, freshness windows,
  `retained_exports`, and an optional workspace database path.
- `[filters]`: target countries, inclusion/exclusion terms, target rules,
  company allowlists, employment policy, and language policy.
- `[http]`: user agent, timeouts, retry delays, and retry count.
- `[sources.<registered-source-id>]`: enabled flag, bounded
  page/detail/query workers, explicit `search_queries`, and explicit
  `locations`. Adapter-specific keys are preserved in `SourceConfig.options`,
  so a new source can add settings without changing the core configuration
  model.
- `indeed_brightdata` may use
  `inherit_search_matrix_from` to reuse another source's matrix, and accepts
  `options.country` / `options.domain` to pin the Indeed market explicitly.
  Without them the market is derived from the search location; a location the
  adapter cannot place is an error rather than a silent default, because
  guessing a market means searching -- and paying for -- the wrong country.
- `[notion]`: enabled flag and display labels only. Keep the Internal connection
  token in `NOTION_INTEGRATION_TOKEN`, never in TOML. `container_title` and
  `daily_table_prefix` are live Notion addressing/display inputs; do not change
  them as a cosmetic correction during an active deployment.

Unknown keys in `[project]`, `[filters]`, `[http]`, and `[notion]` are
rejected, with a suggestion when the key is close to a real one. Only
`[sources.<id>]` accepts unrecognized keys, because that is the documented
extension point for adapter-specific settings; a near-miss of a known source
field is still reported as a typo.

During composition, the profile's queries and locations are injected into
every selected source. They are not duplicated in each source table. There
are no built-in search terms or target locations.

## Cumulative exports

Every run rewrites the complete filtered history into
`<export_dir>/<prefix>_<date>.csv`, so the directory grows by one full export
per run. `[project] retained_exports` bounds how many dated files of a series are kept.
It defaults to `0`, meaning no pruning, so enabling it is a deliberate choice
rather than something an upgrade does to files you already have. Pruning only
ever considers files matching the series this sink writes, so other profiles'
exports and hand-written files in the same directory are never touched.

```toml
[project]
retained_exports = 14   # keep two weeks of this track's dated exports
```

## Mailbox configuration

`email.toml` describes the recommendation mailbox. `init --email` writes it
once, with the IMAP host supplied and the rest at defaults:

| Key | Default | Meaning |
|---|---|---|
| `mailbox.host` | from `--imap-host` | IMAP server |
| `mailbox.port` | `993` | IMAP port |
| `mailbox.use_ssl` | `true` | TLS |
| `mailbox.folder` | `"INBOX"` | Folder or label to read |
| `mailbox.username_env` | `JOB_EMAIL_USERNAME` | Which variable holds the username |
| `mailbox.password_env` | `JOB_EMAIL_APP_PASSWORD` | Which variable holds the app password |
| `mailbox.lookback_days` | `7` | IMAP `SINCE` window |
| `mailbox.max_messages` | `50` | Cap per run |
| `mailbox.subject_keywords` | `[]` | Empty means every message in the folder is considered |
| `mailbox.sender_allowlist` | `[]` | Empty means any sender |
| `mailbox.state_path` | `data/email_ingest_state.json` | Processed-message state |
| `tracks.config_paths` | `[]` | Which profiles the mail is routed to |

The credentials themselves stay in the environment; `username_env` and
`password_env` name the variables, they do not hold values.

`tracks.config_paths` being empty is correct for the normal path: a
`job-scraper run` passes the enabled profiles' runtime configs to the mailbox
job itself, so the file does not repeat them. It matters only when invoking
`job-scraper ingest-email` directly, which reads this file alone — set it, or
pass `--track-config`, or the run has nothing to route into.

Narrow `subject_keywords` and `sender_allowlist` once the user's actual
recommendation senders are known. Left empty, ingestion evaluates every message
in the folder, which is slower and produces more false matches.

Two further tables keep private routing detail out of the library:

- `mailbox.skipped_link_hosts`: bulk-mail infrastructure hosts that only ever
  serve tracking or unsubscribe redirects, never a job page.
- `[platform_country_scope]`: platforms that list a role in one country while
  recruiting across a region. Jobs arriving from a listed platform are
  evaluated against that country list instead of the track's own
  `filters.country`.

```toml
[mailbox]
skipped_link_hosts = ["links.example-mailer.invalid"]

[platform_country_scope]
example_board = ["DE", "NL", "AT"]
```

## Bright Data controls

To suspend direct Bright Data collection, omit `indeed_brightdata` from the
effective profile `sources` list (including inherited defaults and any local
override) and leave `BRIGHTDATA_DIRECT_COLLECTION_ENABLED` unset or `false`.
Keep its runtime `[sources.indeed_brightdata]` table intact; profile composition
determines which source adapters run, and the environment flag prevents a
mistakenly selected source or `--enable-indeed` from making a request. Email-derived
Indeed detail enrichment also requires all of:

- `BRIGHTDATA_API_KEY`
- `BRIGHTDATA_DATASET_ID`
- `BRIGHTDATA_EMAIL_DETAIL_ENRICHMENT_ENABLED=true`

The explicit flags default to disabled, so retained credentials alone do not
submit paid snapshots. To resume direct collection, set
`BRIGHTDATA_DIRECT_COLLECTION_ENABLED=true`, restore `indeed_brightdata` to the
effective profile `sources` list, and set its runtime `enabled` value to `true`.
`job-scraper doctor --all` reports whether each profile is disabled by its
source list or suspended by the flag, without contacting Bright Data. The
special `run_brightdata_notion_e2e` command is also blocked by the direct flag.
Separately set the email flag to `true` only if email detail enrichment is also
desired.

## Secrets

Copy `.env.example` to `.env` and fill only enabled integrations. Supported
variables include:

- `BRIGHTDATA_API_KEY`, `BRIGHTDATA_DATASET_ID`
- `JOB_EMAIL_USERNAME`, `JOB_EMAIL_APP_PASSWORD`
- `NOTION_INTEGRATION_TOKEN`, `NOTION_DATABASE_ID`,
  `NOTION_PARENT_PAGE_ID`

Validate the private workspace with:

```powershell
uv run job-scraper list
uv run job-scraper config validate --all
uv run job-scraper doctor --all
uv run job-scraper db init
uv run job-scraper db status
uv run job-scraper run
```

`db status` is read-only. It reports row counts, any migration the workspace
has not applied yet, and any table the current schema no longer defines --
which is how a workspace that outlived a removed feature makes itself visible
instead of drifting silently.

## Notion workspace structure

The sink owns the shape of what it writes. A deployment chooses two names; the
rest is fixed and is what a downstream screener and the user both rely on.

```text
<NOTION_PARENT_PAGE_ID>            the page the integration was granted
  └── container_title              a child page, created if absent
        └── "<daily_table_prefix> Jobs"   one table per profile
```

`container_title` defaults to `Job Discovery`. `daily_table_prefix` defaults to
the profile label, and the table title is always the prefix followed by
` Jobs`. An earlier generation of this sink created one table per day named
`<prefix> <YYYY-MM-DD>`; those titles are still recognized when resolving an
existing table, but new tables are never created that way.

### Properties

Each job is one row. The sink writes exactly these properties:

| Property | Type written | Content |
|---|---|---|
| `Job` | title | Job title, hyperlinked to the source posting |
| `Company` | rich text | Employer, or `N/A` |
| `Location` | rich text | City, or `N/A` |
| `Status` | select or status | Application state; see below |
| `Date` | date | The local date the job was found |
| `Source` | multi-select, select, or rich text | Originating platform |
| `Language` | select or rich text | Detected description language |

Where a column already exists, the sink matches the property type the workspace
uses rather than imposing its own, which is why several rows above list more
than one type. A table created by the sink gets the first type listed, with
`Job` frozen as the first column.

The page body of each row carries a `Summary` heading with the originating
query or email subject, the posting date, the location, and the language.

### Status vocabulary

`Status` is the one column a person is expected to edit, and the value flows
back: a job marked applied or not-interested is excluded from later candidate
processing, and a matching repost is suppressed for 30 days.

| Written by the sink | Read back as | Also accepted when reading |
|---|---|---|
| `Not Applied` | new | `New`, `N/A`, `NA`, empty, `没投` |
| `Applied` | applied | `Interview`, `Offer`, `Rejected`, `投了` |
| `Not Interested` | not interested | `Not Fit`, `不考虑` |

An unrecognized value is treated as new, so a workspace that adds its own
status names never loses a job — but it also never suppresses one. Keep custom
values within the accepted sets above if they must round-trip.

## Notion database bindings

After a profile first resolves an existing daily table, the sink stores its
database ID and data-source ID in
`<database_path parent>/notion_database_bindings.json`. The file is private
operational state, keyed by the stable profile ID, and must not be committed.
Future runs address that database ID directly, so a Notion table rename or a
changed `daily_table_prefix` does not silently create a replacement table.
The saved data-source ID is the binding-time diagnostic value; a publishing run
uses the current data-source ID returned with its database response.

If the stored database returns a Notion `404`, the sink falls back to the
existing title discovery and refreshes the binding when it finds a table. Other
Notion errors do not trigger discovery or database creation. When an ID-bound
database's title differs, the configured table title is sent to Notion on the
normal publishing path. `job-scraper db status` shows the stored binding or
`unbound` for each selected profile; it does not contact Notion or create the
binding file, even when the workspace database itself is missing. The initial
binding is created by the normal live publishing path after it resolves an
existing table. Do not change `daily_table_prefix` or use a live run merely as
a verification step before that binding is established; obtain the owner's
approval for the normal operational run.

`job-scraper run` selects every enabled profile by default. Source activation
comes only from the private configuration generated by `init`; no
source-specific CLI flag is required. When no explicit `--post-age-days`
override is supplied, mailbox ingestion uses the widest configured online
freshness window across the selected profiles.

Email-derived Indeed URLs may use the standard dataset to fetch complete job
details in batches of ten with up to three batches in flight. Retryable Bright
Data responses (`408`, `429`, `500`, `502`, `503`, and `504`) receive three
attempts with exponential backoff. Persistent failures are split down to
individual URLs and recorded on only the affected candidates. Direct Indeed
collection remains single-stage, and the original Indeed URL remains the job link.
