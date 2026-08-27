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

## Employer applicant-tracking board controls (`ats_direct`)

Reads employer applicant-tracking boards directly instead of a search or
aggregation surface. It ignores `search_queries`/`locations` entirely — its
unit of work is a board token, not a query matrix — so cost scales with the
configured employer count. See
[`specs/2026-08-27-employer-direct-source-coverage.md`](specs/2026-08-27-employer-direct-source-coverage.md).

```toml
[sources.ats_direct]
enabled = true

[[sources.ats_direct.options.boards]]
provider = "personio"
token = "examplecorp"          # the board's Personio subdomain
company_name = "Example Corp"  # optional; falls back to a title-cased token

[[sources.ats_direct.options.boards]]
provider = "jsonld"
token = "https://careers.example.test/backend-engineer"  # a single posting page
company_name = "Careers Page GmbH"
```

An unknown `provider` id fails at load time, before any request. A token
that is unreachable, returns an error, or returns a payload the provider's
parser does not recognize fails that token only; the rest of the run is
unaffected. Provider support is a table of provider id → request → parser in
`collectors/ats.py`; adding a provider adds one row and one parser function.

**Finding a `personio` token.** A board token is the company's subdomain in
`https://{token}.jobs.personio.de/`. This is workspace configuration, found
by a person, not discovered automatically — the reasoning is under
[Design and constraints](specs/2026-08-27-employer-direct-source-coverage.md#design-and-constraints)
in the source spec. Two ways to find one for a target employer:

- Search `site:jobs.personio.de "<company name>"`; the subdomain in any
  result is the token.
- Open the employer's own careers/"Karriere" link — many redirect straight
  to their Personio board — and read the token off the resulting URL.

Confirm a token before adding it by opening `https://{token}.jobs.personio.de/xml`
directly; a valid board returns XML, not an error page.

**Finding a `jsonld` token.** The token is the posting page URL itself — no
discovery beyond finding the page. Confirm it by viewing the page source for
a `<script type="application/ld+json">` block containing `"@type":
"JobPosting"`.

## Public employment agency controls (`arbeitsagentur_direct`)

Reads Germany's public statutory employment-service job search API — public,
unmetered, and unauthenticated. Uses the profile's `search_queries` and
`locations` like `linkedin_direct`. See
[`specs/2026-08-27-public-employment-agency-source.md`](specs/2026-08-27-public-employment-agency-source.md).

```toml
[sources.arbeitsagentur_direct]
enabled = true
max_listing_pages = 4          # 25 postings per page

[sources.arbeitsagentur_direct.options]
exclude_private_intermediary = false  # a posting placed by a staffing agency, not the employer
exclude_temporary_employment = false  # a posting for temporary-employment agency work
# search_path / detail_path override the pinned default endpoint versions;
# set them only after confirming the provider has moved a path again.
```

Both exclusion flags default to `false` (nothing is excluded silently) and are
applied as search parameters, not by inspecting each posting — the matching
per-posting flags are optional in the payload, so a posting that omits one
cannot be told from one that sets it false.

A `403` from either endpoint fails loudly, names the endpoint, and is not
retried — the provider has moved the endpoint version before while leaving the
fixed public client identifier valid, so a `403` here has meant a retired path,
not a rejected credential.

Three things about this surface differ from every other source, and each one
fails silently if copied from another profile:

| Key | What this surface needs | What happens otherwise |
|---|---|---|
| `locations` | the German place name, e.g. `"Deutschland"` | `"Germany"` resolves to nothing and the run returns zero postings without an error |
| `recent_post_age_hours` / `bootstrap_post_age_hours` | wide — weeks to months | a posting stays listed for months and the date recorded is *first* publication, so a 24h window finds only what was reported today |
| `require_english` | `false` when the profile admits non-English descriptions | the CSV export queries English rows only, so German postings are acquired and then dropped on the way out |

Freshness on this surface is not a feed window. "Old" does not mean "filled":
an employer reports a vacancy and it stays listed. Pagination is also ranked
and capped, so a posting first published weeks ago may only now reach the pages
a profile reads — a narrow window discards it on the first day it was ever
visible.

## Token-free board controls (`workable_direct`, `arbeitnow_direct`, `berlinstartupjobs_direct`)

Three sources that read public boards needing no per-employer configuration —
the complement to `ats_direct`, which reads one employer at a time and can only
read employers someone has already listed. See
[`specs/2026-08-27-token-free-board-sources.md`](specs/2026-08-27-token-free-board-sources.md).

```toml
[sources.workable_direct]
enabled = true
max_listing_pages = 12         # 20 postings per page, followed by an opaque cursor

[sources.arbeitnow_direct]
enabled = true
max_listing_pages = 8          # whole-board feed; no query matrix

[sources.berlinstartupjobs_direct]
enabled = true
max_listing_pages = 2          # 100 postings per page; the board is small
```

`workable_direct` uses the profile's `search_queries` and `locations` like
`linkedin_direct`, and takes an English location name (`"Germany"`, not
`"Deutschland"` — the opposite of `arbeitsagentur_direct`). It needs no detail
request: the listing already carries the description, requirements, and
benefits sections, which are joined into one description.

`arbeitnow_direct` and `berlinstartupjobs_direct` ignore `search_queries` and
`locations` entirely — they page a whole board and let the pipeline filter.
`max_listing_pages` is the only bound on how much they read.

Three behaviors are shared and worth knowing before tuning `max_listing_pages`:

| Behavior | Why |
|---|---|
| A rate-limit response ends that source's paging and keeps what it already collected | one of these boards refuses a sub-second page loop; `base_delay_seconds` is what prevents it, and retrying a rate limit only spends the budget faster |
| A posting whose employer cannot be determined is dropped, with an event | a blank company would reach every sink; `berlinstartupjobs_direct` reads the employer out of the posting title, and a title without the `//` separator has none |
| Postings are not filtered by language or freshness here | both are pipeline decisions; `workable_direct` records the board's own language tag in the raw payload as corroboration only |

These surfaces carry the same staffing and crowd-work re-listers the search
surfaces do — on one sample a crowd-work platform was the second most frequent
company. Set the publisher denylist below before enabling them.

Two properties of `arbeitnow_direct` are worth expecting rather than treating
as defects. Its employer names arrive lowercased and unspaced, as the feed
sends them (`jetbrains`, not `JetBrains`) — the denylist casefolds, so matching
is unaffected, but a publication sink shows them as received rather than
prettified, because inventing a display name would be a guess. And a minority
of its postings carry no location at all; those are passed through empty and
rejected by the `country` step, rather than being given a default that would
make an unplaceable posting look placed.

## Publisher denylist (`excluded_company_names`)

`[filters] excluded_company_names` rejects a job whose `company_name` is a
publisher, staffing agency, or crowd-work platform rather than an employer —
exact match after casefolding and whitespace normalization, so a denylist
entry never matches a publisher's name occurring as a substring of a longer
employer name. It is evaluated by `CompanyStep` for every source, under
`RejectionReason.COMPANY_IS_PUBLISHER`, distinct from `COMPANY_NOT_ALLOWED`.
Empty by default; nothing is rejected until it is set.

```toml
[filters]
excluded_company_names = ["eFinancialCareers", "Emails", "Unknown"]
```

The example above is the email channel's old hardcoded publisher set —
`efinancialcareers` a job-board publisher, `Emails` a sender-domain
extraction artifact, `Unknown` the generic-extraction sentinel for a card
with no recoverable company name — now a workspace choice instead of code.
See
[`specs/2026-08-27-employer-direct-source-coverage.md`](specs/2026-08-27-employer-direct-source-coverage.md).

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
back. Only a job marked not-interested is excluded from later candidate
processing, and a matching repost is suppressed for 30 days. `Applied` stays
available to downstream status views but does not exclude an otherwise
matching candidate. Changing `Not Interested` back to `Not Applied` clears the
local exclusion on the next status import.

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
