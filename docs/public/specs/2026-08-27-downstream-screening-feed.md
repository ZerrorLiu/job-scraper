# Downstream screening feed

## Outcome

A downstream screener reads acquired jobs, their publication identity, and
their application status through a versioned command instead of opening this
project's SQLite files. The storage layout, the table names, and the unresolved
V1/V2 split stop being part of the downstream contract.

## Scope

- In scope:
  - A read-only `job-scraper feed` command emitting a versioned JSON document.
  - A record shape in `domain/` carrying job identity, publication, and
    application status.
  - A documented default for which jobs a generating screener wants.
- Out of scope:
  - Resolving the V1/V2 publication-state split.
  - Any write path, agent provider, resume workspace, or screening logic.
  - Changing what `job-scraper run` acquires or publishes.

## Acceptance criteria

- [x] `job-scraper feed` emits `schema_version`, the requested window,
  a record count, and the records.
- [x] The window is reported even when no record matched, because a caching
  reader cannot reconstruct it from an empty list.
- [x] A job with no publication row is returned and marked unpublished rather
  than silently dropped.
- [x] A job with no application row reports `new`.
- [x] `--published-only` and the settled-status default are applied by one
  shared selector, not per caller.
- [x] The command creates no files or directories for a profile that has never
  run.
- [x] Output is UTF-8 on every platform, including a legacy-codec console.

## Design and constraints

The feed remains the stable read boundary while the cleanup-first unified
workflow in
[`2026-08-28-first-class-agent-screening.md`](2026-08-28-first-class-agent-screening.md)
is implemented and proven. It replaces *how* a separate consumer reads without
making SQLite layout part of the contract:

```text
downstream screener -> job-scraper feed (versioned document)
job-scraper         -X-> downstream workspace
```

That one-way topology describes the current compatibility phase, not the final
workflow authority. The feed is not a second semantic-screening engine and is
retired from production orchestration only after replay, controlled cutover,
and the rollback window defined by the unified-workflow specification.

Before this, a downstream reader had to know four private things: the
`data/jobs_<profile>.db` filename convention, the `jobs` / `notion_sync_state`
/ `application_state` table names, this project's `.env`, and which store holds
live publication state. That last one is a live trap -- V2's
`external_publications` is a frozen migration snapshot while the Notion sink
still writes V1's `notion_sync_state`, so a reader that picked the newer-looking
table would silently under-report. The record shape hides all four.

`SCHEMA_VERSION` tracks the shape, not the storage. Moving publication state
from V1 to V2 must not change it. Adding a field is compatible; removing one or
repurposing its meaning is not.

The window is whole local days rather than a rolling 24 hours, because a daily
screener asking for "yesterday" means the calendar day whatever the hour the run
starts. `first_seen_at` is stored in UTC, so both ends are converted back to UTC
and the window is half-open -- a job seen exactly at the boundary belongs to the
next day.

Publication is modelled as a sink id plus an external id and a container id
rather than as Notion fields, so a second sink does not require a shape change.
Empty strings mean unpublished, never unknown.

The command is read-only by construction. The existence check precedes
constructing `Database`, whose `__init__` creates the parent directory --
otherwise asking for a feed would leave a `data/` tree behind for a profile that
has never run.

## Verification

Offline tests cover the document envelope, the selector, and the window
arithmetic; a second suite exercises the read against a real SQLite file so the
two LEFT joins are proven rather than assumed. Confirm on live data that
publication identity round-trips and that a legacy-codec console does not raise.

## Follow-ups

Resolve the V1/V2 publication-state split behind this contract. When that lands,
`read_screening_feed` changes and `SCHEMA_VERSION` does not.
