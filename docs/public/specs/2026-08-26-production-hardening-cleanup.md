# Production hardening and dead-path removal

## Outcome

A full read of the codebase found a set of defects that only appear in
long-running installations -- schema changes that never reach an existing
database, retries that can duplicate an external record, a state file that can
be truncated by an interrupted write, unbounded growth in exports and state,
and hot paths dominated by repeated work. Alongside them sat a large layer of
compatibility shims and duplicate implementations that no caller reached.

After this change the library evolves its own schema, never replays a
non-idempotent external write, keeps its on-disk footprint bounded, evaluates
one job roughly four times faster, and carries one implementation of each rule
instead of two.

## Scope

- In scope:
  - Correctness on the write paths: schema migration, Notion retry policy and
    status preservation, atomic state persistence, transactional upsert,
    per-input failure isolation for batched acquisition.
  - Bounded growth: cumulative export retention, processed-message retention,
    and removing a byte-identical duplicate payload from the workspace store.
  - Performance of normalization: precompiled location matching, single-pass
    language detection, cached keyword patterns, and removing a per-candidate
    process spawn.
  - Structural duplication: one country reference, one JSON-LD extractor, one
    policy evaluation shared by acquisition and export.
  - Removing unreferenced compatibility shims, dead adapters, and a legacy
    acquisition path.
  - Moving deployment-specific policy out of the library into configuration.
- Out of scope:
  - Any change to the acquisition sources themselves or to what a profile
    selects.
  - Any migration that drops existing rows. Tables belonging to removed
    features are reported, not deleted.
  - Reviving the pre-V2 local test files; they are quarantined, not ported.

## Acceptance criteria

- [x] A workspace database created before a schema change receives that change
  on the next `initialize()`, recorded and idempotent.
- [x] A Notion object-creating request is not replayed after a 5xx or a
  dropped connection; a 429 still is.
- [x] Updating an existing Notion row preserves a manually set status for both
  the `select` and `status` property types.
- [x] An interrupted state write leaves the previous state intact.
- [x] One failing input in a batched acquisition does not discard its siblings'
  records or their already-submitted snapshot ids.
- [x] The processed-message state is bounded. Cumulative export retention is
  available and correctly scoped, but defaults to disabled: turning it on
  deletes files the operator already has, so it is opt-in rather than an
  upgrade side effect.
- [x] Export filtering and acquisition filtering cannot disagree on the same
  job and policy.
- [x] `db status` performs no writes.
- [x] A mistyped field in `[project]`, `[filters]`, `[http]`, or `[notion]` is
  rejected instead of silently taking a default.
- [x] No deployment-specific host, country list, or market default remains in
  library code.
- [x] Type checking covers the two adapters that were previously exempt.
- [x] The decisions a run makes -- sink selection, export destination, freshness
  window, profile identity, exit code -- are answerable without performing a
  run, and are covered by tests that do not mock the world to reach them.

## Design and constraints

**Schema evolution.** `CREATE TABLE IF NOT EXISTS` is sufficient for a new
database and useless for an existing one, so the workspace store now carries an
ordered `MIGRATIONS` tuple applied and recorded on `initialize()`. Versions at
or below the base schema describe what the base script already creates. A table
the schema no longer defines is surfaced through `unknown_tables()` and
`db status`; nothing is dropped, per the data-safety rule in `AGENTS.md`.

**Non-idempotent retries.** A request that creates an object may have been
applied server-side before the failure reached the client, so replaying it can
duplicate the object. Retries are therefore gated on whether the call is safe
to replay; an explicit rate-limit rejection always is, because no work
happened. See [`../architecture.md`](../architecture.md).

**One rule, one implementation.** The cumulative export previously
re-implemented the filter policy. It now rebuilds a `JobRecord` from the stored
row and runs the same pipeline steps, minus freshness and history, which do not
apply to a cumulative file. Country data, JSON-LD extraction, and the
title-only policy narrowing are likewise single definitions.

**Layering.** Translating the TOML filter model into a domain policy moved from
`pipeline/` to `configuration/`, and the search planner now depends on a
structural profile protocol. `pipeline/` and `application/` no longer import
the configuration model, matching the dependency direction in `AGENTS.md`.

**Deployment policy.** Sender-infrastructure hosts and per-platform country
widening are supplied by the mailbox config; the Indeed market must be derived
from the search location or pinned in source options, with no built-in default
country. Behavior for an existing workspace is unchanged once those values are
configured.

**Separating decision from procedure.** `run_daily.main` was a 230-line
transaction script in which ordinary rules -- which sinks this run uses, where
the export goes, what exit code the outcome deserves -- were reachable only by
driving the whole procedure, including its databases and network. Those rules
now live in `application/run_plan.py` as plain functions over plain values, and
`main` consults them. This is why `run_daily` coverage moved from 23% to 65%:
not by mocking more, but by there being less that *needs* mocking.

**Matching cost.** Evaluating a candidate was dominated by scanning its
description once per configured keyword: one real profile carries 46 rule
keywords, so a description could be scanned 46 times to answer one question.
Keyword sets are now compiled into a single alternation per set, with identical
word-boundary semantics, and the normalized haystack is built at most once per
candidate per scope. Separately, diacritic stripping walked every character in
Python even for text that had none; ASCII text now skips it.

A tempting third change was rejected by measurement. `role` accounts for about
nine out of ten rejections, so moving it first looks obviously right -- but it
is only cheap when a profile scopes it to the title, and profiles that scope it
to "combined" make it read every description in full. Measured against the real
candidate mix, "role first" was 1.2x to 1.5x *slower*. The original order
stands, with the reasoning recorded next to it so the next person measures
before rearranging.

**Bounded work.** Detail fetching bounds itself with a socket timeout, a
response-size cap, and a total budget shared across retries, which removes the
reason the previous implementation spawned one interpreter per candidate.

## Verification

- `ruff format --check`, `ruff check`, `pyright`, `pytest` all pass. Type
  checking now includes the two previously exempt adapters.
- New focused offline tests cover: Notion write safety, state durability and
  retention, batch failure isolation, export retention scoping, export/pipeline
  equivalence, config strictness, and migration application to an existing
  database.
- Migration behavior was additionally exercised against a copy of a real
  workspace file to confirm it applies once, is idempotent, and reports drift.
- Query plans were checked against a copy of a real operational database to
  confirm the added indexes replace full table scans.
- Every matching and ordering change was validated by replaying real stored
  candidates through the pipeline before and after, comparing accept/reject
  verdicts rather than trusting that the rewrite preserved them. Verdict counts
  were identical for all profiles in every case; a change that could not clear
  that bar was reverted.
- No independent user-path simulation: the CLI surface, command names, flags,
  and output shape are unchanged. The behavior changes are internal, plus two
  new optional configuration tables that default to the previous behavior when
  absent.

## Follow-ups

- Coverage of the Notion client remains low (21%). Most of the remainder is
  view and container management: code whose entire behavior is composing a
  request body, where a mock-based test asserts only that the body matches
  itself. Its real assurance is live verification, not unit tests.
- `external_publications` in the workspace store is written only by
  `migrate_v1`, so it froze at the last migration while looking exactly like
  live data. It is now labelled as frozen in `counts()` and `db status`, but the
  underlying problem is the unresolved V1/V2 split: publication state lives in
  V1, canonical identity in V2, and a report drawn from the wrong one is
  silently wrong. Resolving that split is the real fix.
- `ingest_email_recommendations` still splits one operation across `prepare()`
  and `finish()` joined by a mutable struct. The split exists so the mailbox can
  be read while the online tracks run; it would be better expressed as an
  explicit two-phase value than as two functions that must be called in order.
- The quarantined pre-V2 test files still describe behaviors worth asserting.
  Port them into `public_tests/` case by case rather than reviving the files.
