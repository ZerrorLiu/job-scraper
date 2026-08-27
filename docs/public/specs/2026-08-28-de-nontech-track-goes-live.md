# DE Non-Tech track goes live

## Outcome

The DE Non-Tech track had a reviewed export (659 postings, single-source
Arbeitsagentur) sitting in a local database with Notion publishing turned
off. This change takes it the rest of the way to a running, unattended
track: a role-keyword gap closed against real rejection data, a per-table
Notion column for posting age, and a production deployment with its own
schedule so the shared daily timer's `--post-age-days 1` cannot silently
gut this track's 60/90-day freshness window.

## Scope

- In scope: `target_keywords` additions for de_nontech; a per-table "Posted
  At" / "Days Listed" Notion schema addition, gated so it cannot reach any
  other track's table; a one-off backfill of the existing 659 rows into the
  new "DE Non-Tech Jobs" Notion table; VPS deployment of this track's
  config, and a dedicated systemd timer that does not pass
  `--post-age-days`.
- Out of scope: Personio/ATS as a source for this track (dropped --
  requires hand-curated board tokens, not autonomous discovery, so it does
  not scale the way Arbeitsagentur does). Pruning `missing_target_keywords`
  further beyond the gap identified here.

## What this change replaces

`config/profiles/de_nontech.toml`'s `sinks = ["csv"]` (Notion excluded
because the export had not been reviewed) becomes `["csv", "notion_daily"]`
now that it has been. `[notion] enabled` in `config/config.de_nontech.toml`
goes from `false` to `true` for the same reason. Nothing else is superseded;
this is additive.

## Design and constraints

**Role-keyword gap.** `target_keywords` selects a title as in-track by
whole-word match (`pipeline/role_filter.py`); it does not stem. Replaying
the 330 `missing_target_keywords` rejections from a real run found 33 that
plausibly belonged (10%), concentrated in finance/back-office words the list
lacked entirely: tax, audit, treasury, payroll, compliance, reception
(English), financial reporting, financial planning, compensation and
benefits. Tax and audit roles in Germany sit behind a professional
certification track (Steuerberater / Wirtschaftsprüfer) the candidate does
not hold, so "tax", "audit", "auditor", "auditorin" were deliberately left
out even though they would have matched correctly -- a keyword match is not
the same as an applicable role. "compliance" was dropped for the same
reason: it is credential-adjacent often enough that the false-positive risk
was not worth it. The remaining words were added; replaying the same 330
rejections afterward recovered 27 of them, all genuinely in-track roles, no
inspected false positives.

**Posted At / Days Listed.** Every daily table previously carried one
`Date` property populated with the day a row was synced into Notion, not
the source's real publish date -- indistinguishable from "today" for all
659 rows on first backfill, useless for judging how stale a listing is on a
source where postings stay live for months. Rather than change the shared
daily-table schema (`integrations/notion.py:_create_daily_database` /
`sync_daily_schema`, used by every track), this track's data source alone
got two properties added directly via the Notion API: `Posted At` (date,
from `JobRecord.posted_at`) and `Days Listed` (a Notion formula bucketing
`dateBetween(now(), prop("Posted At"), "days")` into 0-7d / 8-30d / 31-60d /
61-90d / 90d+ / Unknown, computed live so it never needs re-syncing).
`adapters/sinks/notion_payload.py:build_daily_properties` writes `Posted
At` only when the table's schema already has it (checked via
`property_types`, the same mechanism already used for `Language`/`Status`/
`Source`'s type-adaptive writes) -- writing an unrecognized property name
to a table without it is a Notion API validation error for the whole
request, so this must stay conditional rather than unconditional like the
other fields. No other track's schema has these properties, so no other
track's publish calls are affected.

**Deployment found a live break.** Deploying this session's git history to
the VPS (`/srv/positions`, user `dev`) surfaced that its four existing
tracks' config files still set `minimum_english_ratio` *and*
`allowed_description_languages` together -- a combination the language-policy
defect fix (`config.py:resolve_minimum_english_ratio`, landed earlier this
session) now refuses to load. `job-scraper config validate --all` failed
for all four immediately after `git pull`, hours before the next scheduled
run. Fixed by removing the now-redundant `minimum_english_ratio` line from
each (`allowed_description_languages` already governed the verdict; the
ratio had no effect per the defect this fix addressed). Config files are
gitignored and per-host, so this was a local-only-vs-VPS drift the git pull
exposed rather than caused by the pull itself -- the VPS config had been
stale since that fix landed.

**Scheduling.** `positions-daily.service` runs a bare
`job-scraper run --profile-workers 4 --post-age-days 1` -- appropriate for
the other four tracks (LinkedIn/email-sourced, want strict 24h freshness),
wrong for this one: `FreshnessStep` filters on `JobRecord.posted_at`, a
fixed value, so a job posted more than a day ago fails that filter every
day forever, not just today. There is no per-profile freshness override in
a single `job-scraper run` invocation, so this track runs under its own
`positions-de-nontech-daily.timer` / `.service` (`ExecStart` has no
`--post-age-days`, so `effective_post_age_hours` falls through to this
track's own configured 60-day recent / 90-day bootstrap window from
`config/config.de_nontech.toml`). Scheduled Tue-Sat 04:30 Europe/Berlin, 30
minutes after the shared timer, to avoid both hitting the VPS at once.
`config/profiles/de_nontech.toml` stayed `enabled = false` on the VPS only
between the deploy and the timer's creation, specifically so the *shared*
timer's next unattended firing could not sweep it in with the wrong window
in the interim; it is `true` again now that the dedicated timer exists.

Bootstrap-vs-recent selection is automatic and does not need touching
again: `run_daily.py:effective_post_age_hours` uses `bootstrap_post_age_hours`
only when the database has no prior observations for that source, and
switches to `recent_post_age_hours` from then on -- already true for this
track since the 659-row export was acquired.

## Verification

- `public_tests/test_notion_posted_at_property.py`: `Posted At` is written
  only when the schema has it, and only when `posted_at` is non-null.
- Replayed the 330 stored `missing_target_keywords` rejections against the
  updated keyword list (`workspace.db` `profile_matches` /
  `canonical_jobs`); 27 recovered, spot-checked as correct.
- `job-scraper config validate --all` and `job-scraper doctor --all` green
  on the VPS after both the `minimum_english_ratio` fix and the de_nontech
  config deploy.
- Manual `job-scraper run --profile de_nontech --profile-workers 1` on the
  VPS before enabling the timer, to catch any invocation problem
  (`notion_daily` newly in `sinks`) under supervision rather than
  unattended.
- Full `public_tests/` suite green after each commit in this session.

## Follow-ups

- BA per-query result cap: `max_listing_pages = 4` means ~100 results per
  query by Arbeitsagentur's own ranking; a query whose true result count is
  much larger than that will silently miss postings ranked below the cut,
  independent of the freshness window. Not a "misses postings over time"
  problem like the freshness-window one -- worth revisiting only if a
  specific query is suspected of running hot.
- Personio/ATS as a de_nontech source: not pursued (see Scope).
