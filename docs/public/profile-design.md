# Designing a profile

The [deployment runbook](agent-deployment.md) covers the procedure. This
document covers the decisions inside it: how to turn what a person tells you
about their job search into a profile's queries, keywords, filters, and
destinations.

The user does not know these mechanics and should not be asked to. The agent
decides, states the decision in the user's terms, and records it in the private
workspace.

Vocabulary: a **profile** is what the code and CLI call one search direction.
`track_label` and "track" appear in configuration keys and Notion titles for
historical reasons and mean the same thing.

## What belongs in one profile

A profile is one search matrix evaluated by one filtering policy into one set
of destinations. Split into a second profile when the *policy* differs, not
when the job titles differ.

| Situation | One profile or two |
|---|---|
| "Backend engineer" and "platform engineer" in the same city, same seniority | One. Add both as queries. |
| Same role, but one search must exclude German-language postings and the other must not | Two. `require_english` is per profile. |
| Same role in Berlin and in Amsterdam, same filters | One. Add both as locations. |
| A speculative direction the user wants to watch without mixing into their main results | Two, so its results land in a separate export and Notion table. |

Every profile costs a full pass over its matrix on every run. Two profiles that
share a policy and differ only in query text double the requests for nothing.

## The search matrix

`base_queries` × `locations` is expanded into search intents, deduplicated
across profiles, and issued once. Supply each dimension once and let the
expansion happen:

```bash
--query "backend engineer" --query "platform engineer" \
--location "Berlin, Germany" --location "Munich, Germany"
```

That is four intents, not one; `job-scraper plan --show-queries` prints the
expansion. Do not pre-multiply the dimensions by hand — writing
`"backend engineer Berlin"` as a query produces a worse search and hides the
count.

Locations are free text passed to the source. For `indeed_brightdata` the
location also determines the Indeed market, so a location the adapter cannot
place is an error rather than a guess; pin it with `options.country` and
`options.domain` when the text is ambiguous.

## The keyword fields do different jobs

This is the part that is easiest to get wrong, because `init` writes the same
list into two of them.

| Field | Effect | Scope |
|---|---|---|
| `target_keywords` | **Accepts or rejects the job.** A job matching none of these is dropped by the `role` step. | `target_match_scope` |
| `include_keywords` | **Annotation only.** Populates `keyword_hits` on the stored record and seeds tech-stack extraction. Filters nothing. | whole text |
| `exclude_keywords` | Rejects on **title only**, by design — a fast pre-filter. | title |
| `excluded_requirement_patterns` | Rejects on the **description**, for requirements the user cannot meet. | description |
| `target_rules` | Named multi-group acceptance rules, evaluated alongside `target_keywords`. | per rule |

`init` seeds `target_keywords` and `include_keywords` with the same list, which
is a reasonable start but rarely the right end state. Separate them once the
user's intent is clear: `target_keywords` should be the *narrowest* set that
still admits everything they want to see, and `include_keywords` can be much
broader because a false positive there only adds an annotation.

`target_match_scope` is `"title"` or `"combined"` (title plus description).
`init` writes `"combined"`. Prefer `"title"` when the user's signal is a role
name, and `"combined"` when it is a technology that may only appear in the body.
`"combined"` on a common word admits a great deal of noise.

### Target rules

Use `target_rules` when acceptance needs more than "any one of these words":

```toml
[[filters.target_rules]]
name = "backend_with_cloud"
keyword_groups = [["python", "go"], ["aws", "gcp", "kubernetes"]]
minimum_keyword_matches = 2
match_scope = "combined"
```

A rule with `keyword_groups` requires `minimum_keyword_matches` distinct groups
to match, which expresses "a language *and* a platform" without enumerating the
cross product.

## Country, language, and employment policy

- `country` accepts a comma-separated list. It filters the job's resolved
  country, which is derived from the posting, not from the search location.
- `require_english`, `allowed_description_languages`, and
  `minimum_english_ratio` are the language policy. Set them only when the user
  actually cannot work in the local language; they reject real jobs.
- `full_time_only`, `allow_part_time`, `allow_temporary` shape employment
  scope. `init` writes the permissive combination. Student and internship
  postings are always rejected by the role step regardless of these values.
- `company_names` is an allowlist, empty by default. A non-empty list means
  *only* those employers pass.

## Destinations

| Sink | Needs | Produces |
|---|---|---|
| `csv` | nothing | `<export_dir>/<export_filename_prefix>_<date>.csv`, rewritten in full each run |
| `notion_daily` | token and destination page | one table per profile inside a container page |

The Notion structure — container page, table title, the seven properties, and
the status vocabulary that flows back — is specified in
[the configuration reference](configuration.md#notion-workspace-structure).
Read it before choosing `container_title` and `daily_table_prefix`, because
both are live addressing values and changing them later orphans the existing
table.

Choose the prefix from the user's own vocabulary for that search direction.
It becomes a visible table name they will read every day.

## Optional and reserved components

Not everything a profile can name is available in every deployment. State the
status honestly when offering one:

| Component | Status | What a deployment does |
|---|---|---|
| `linkedin_direct` | Available | Default source. No account, no credential. |
| `csv` | Available | Default sink. No credential. |
| `email_imap` | Available | Needs a mailbox and an app password. Only worth enabling if the user already receives recommendation mail. |
| `notion_daily` | Available | Needs an internal integration and a granted page. |
| `indeed_brightdata` | Available, gated, paid | Selecting the source is not enough: `BRIGHTDATA_DIRECT_COLLECTION_ENABLED` must also be true. Low yield for most searches. Offer only on request. |
| Bright Data email enrichment | Available, gated, paid | Separate gate, `BRIGHTDATA_EMAIL_DETAIL_ENRICHMENT_ENABLED`. Enriches Indeed jobs found in email. |
| Bright Data async snapshots | Available, needs infrastructure | `BRIGHTDATA_WEBHOOK_URL` / `_TOKEN` require a receiver the vendor can reach. Without them acquisition polls synchronously. |
| Downstream screening | **Interface only** | This repository publishes the feed and stores the result. The screener itself is a separate program; see below. |

Do not invent a component ID. `job-scraper capabilities --json` is the
authoritative list, and `init` rejects an ID the registry does not know.

### The downstream screening interface

This repository deliberately stops at "here are the jobs". Deciding which ones
deserve an application, and generating anything from them, belongs to a
separate program.

The boundary is the feed contract, and it exists today:

```bash
uv run job-scraper feed --profile PROFILE_ID --published-only
```

It emits versioned JSON — `schema_version`, `generated_at`, `window`,
`record_count`, `records` — where each record carries `job_id`, `profile_id`,
`title`, `company`, `location`, `language`, `url`, `description`,
`first_seen_at`, `application_status`, and a `publication` object naming the
sink, the external object ID, and its container.

That last object is what lets a screener write its verdict back to the same
Notion page the user is already reading, which is why `--published-only`
exists. Adding a field to the record is a compatible change; removing one or
changing its meaning bumps `SCHEMA_VERSION`.

A deployment that will eventually screen should therefore:

- keep `notion_daily` enabled, so published objects exist to annotate;
- leave the workspace database in place rather than treating exports as the
  system of record;
- read jobs through `job-scraper feed`, never by opening the SQLite file. The
  database schema is internal and changes without notice; the feed is the
  contract.

There is no screener command in this repository, and none is planned here.

## Idempotency and re-deployment

Deployment must be safe to repeat. What each step does when run twice:

| Command | Run twice |
|---|---|
| `job-scraper init` | **Refuses.** It will not overwrite an existing profile, runtime, or `email.toml`. Change configuration by editing the file, then re-validating. |
| `job-scraper db init` | Safe. Creates what is missing, applies recorded migrations, leaves data alone. |
| `job-scraper db migrate` | Safe, and never writes to its own source database. Preview with `--dry-run`. |
| `job-scraper config validate` / `doctor` / `plan` / `list` | Safe. Read-only, offline, contact nothing. |
| `job-scraper feed` | Safe. Read-only. |
| `job-scraper run` | Safe with respect to duplicates — jobs deduplicate by identity, exports are rewritten in full, and an existing Notion row is updated rather than replaced, preserving the status a person set. It is *not* free: it makes outbound requests and external writes. |

Because `init` refuses to overwrite, a corrected profile is an explicit edit.
That is deliberate: it means no re-run of a deployment script can silently
discard a user's tuning.

Re-deploying the same installation elsewhere means copying `config/`, `.env`,
and the database — not re-running `init`, which would produce a profile with
none of the accumulated edits and none of the history. Cloning the repository
alone produces an empty installation.

One value is never safe to change after the first successful publish:
`daily_table_prefix`, and with it `container_title`. See the configuration
reference.
