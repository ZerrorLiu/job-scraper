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

### Two keyword lists, two opposite matching rules

`target_keywords` and `excluded_requirement_patterns` sit next to each other in
the same file and look like the same kind of list. They match by opposite
rules, and each rule fails in the opposite direction:

| Field | Matching | Fails when you write |
|---|---|---|
| `target_keywords`, `exclude_keywords`, `target_rules` | **word boundary** (`(?<![a-z0-9])…(?![a-z0-9])`) | a stem — it matches nothing inside a longer word |
| `excluded_requirement_patterns` | **whole substring**, on a normalized copy | a bare noun — it matches only that exact run of characters, so a qualifier in front defeats it |

This matters most in a compounding language. German glues its nouns together,
so on a German-language surface a stem in `target_keywords` is inert:
`assistenz` does not match `Teamassistenz`, `recruit` does not match
`Recruiter`, `buchhalt` does not match `Buchhalter`. In one measured profile
this silently rejected an entire query's results — a query returning only
compounds accepted 0% until the whole words were written out.

The same profile's `excluded_requirement_patterns` failed the other way:
`deutschkenntnisse` did not match `sehr gute Deutschkenntnisse`, so the most
common German phrasing of a language requirement passed straight through.
Writing the phrasings rather than the vocabulary raised the rejection rate from
35% to 59% on the same sample.

Derive both lists from real rejected titles and descriptions rather than
guessing them — the compounds that matter are not the ones that come to mind.

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
  actually cannot work in the local language; they reject real jobs. They are
  one interacting contract with exactly two modes, not three independent
  knobs:
  - **`allowed_description_languages` non-empty.** The verdict is decided by
    label membership alone — a description passes iff its detected language
    label (`English`, `German`, `Mixed`, or `Unknown`) is in the list.
    `minimum_english_ratio` cannot affect the verdict in this mode and
    `load_config` refuses to load a profile that sets both, so you don't have
    to reason about the interaction: pick this mode and the ratio key is
    simply absent from the profile.
  - **`allowed_description_languages` empty.** `minimum_english_ratio` gates
    the verdict through `require_english`: a description passes only if it is
    labelled `English` at or above the configured ratio (or if
    `require_english = false`, everything passes).

  An earlier version of this contract let a non-empty list fall through to
  the ratio check for descriptions labelled `English`, which rejected a
  *more*-English description while a *less*-English one admitted through
  another label in the same list passed — see
  [`specs/2026-08-27-description-language-policy-defect.md`](specs/2026-08-27-description-language-policy-defect.md)
  for the measurement that found it. The two-mode split above is the fix.
- These keys judge what language a posting is *written in*.
  `excluded_requirement_patterns` is what judges the language an employer
  *demands*, and for a candidate reading a foreign-language market that is the
  one that matters. Patterns match as whole substrings against a normalized
  copy of the description, so a pattern naming a bare noun does not match that
  noun behind an intensifier — write the phrasings, not the vocabulary.
- `full_time_only`, `allow_part_time`, `allow_temporary` shape employment
  scope. `init` writes the permissive combination. Student and internship
  postings are always rejected by the role step regardless of these values.
- `company_names` is an allowlist, empty by default. A non-empty list means
  *only* those employers pass.
- `excluded_company_names` is a denylist, empty by default, checked before
  the allowlist: a company matching it is rejected under
  `RejectionReason.COMPANY_IS_PUBLISHER` even if it also matches
  `company_names`. It exists for a `company_name` that names a publisher,
  staffing agency, or crowd-work platform rather than an employer — see
  [`specs/2026-08-27-employer-direct-source-coverage.md`](specs/2026-08-27-employer-direct-source-coverage.md).

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
| Screening feed | Available compatibility interface | This repository publishes versioned job records for the current separate screener. The cleanup-first unified workflow will move screening behind the application boundary after replay and cutover. |

Do not invent a component ID. `job-scraper capabilities --json` is the
authoritative list, and `init` rejects an ID the registry does not know.

### The screening-feed compatibility interface

The current released workflow stops at "here are the jobs" and uses a separate
private program for screening and document generation. This is an honest
description of today's runtime, not the long-term architecture: the active
[`cleanup-first unified workflow`](specs/2026-08-28-first-class-agent-screening.md)
moves reusable screening and tailoring orchestration behind this repository's
application boundaries only after cleanup, evidence reconciliation, replay,
and controlled cutover.

The boundary is the feed contract, and it exists today:

```bash
uv run job-scraper feed --profile PROFILE_ID --published-only
```

It emits versioned JSON — `schema_version`, `generated_at`, `window`,
`record_count`, `records` — where each record carries `job_id`, `profile_id`,
`processing_mode`, `title`, `company`, `location`, `language`, `url`, `description`,
`employment_type`, `first_seen_at`, `application_status`, and a `publication`
object naming the sink, the external object ID, and its container.

That last object is what lets a screener write its verdict back to the same
Notion page the user is already reading, which is why `--published-only`
exists. Adding a field to the record is a compatible change; removing one or
changing its meaning bumps `SCHEMA_VERSION`.

A deployment that continues to use the current separate screener and wants to
write verdicts back to existing Notion pages should therefore:

- keep `notion_daily` enabled, so published objects exist to annotate;
- leave the workspace database in place rather than treating exports as the
  system of record;
- read jobs through `job-scraper feed`, never by opening the SQLite file. The
  database schema is internal and changes without notice; the feed is the
  contract.

A deployment intended only for the future unified workflow does not need to
publish to Notion before screening. In that workflow, Notion receives finalized
durable state after screening and any authorized artifact generation.

There is no built-in screener command in the current release. Until the unified
path has publication authority, deployments continue to use the versioned feed
rather than opening SQLite directly.

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
