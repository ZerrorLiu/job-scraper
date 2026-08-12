# Acquisition reliability and correctness hardening

## Outcome

The acquisition-to-publish pipeline (sources, normalization/filter pipeline,
profile orchestration, storage and Notion/CSV sinks) currently has several
defects that cause silent data problems rather than visible failures: distinct
postings can collapse into one canonical identity, a country can be
misattributed from an ambiguous location string, a transient network error can
permanently poison a coalesced request for the rest of a run, and a single job
or Notion write failure can abort an entire batch or falsely mark unrelated,
already-successful tracks as failed. After this change, one bad record, one
failed write, or one transient network error is isolated and reported instead
of silently dropping data or aborting unrelated work.

## Scope

- In scope: job identity/dedup, country and language classification, request
  coalescing and retry behavior across LinkedIn/Bright Data/email
  acquisition, per-job and per-track failure isolation in Notion publishing
  and email-track orchestration, SQLite/CSV write safety, and CLI/config
  validation strictness (`config.py`, `doctor`, `config validate`, profile-id
  resolution, `db status`/`db migrate`).
- Out of scope: `career-strategy-analysis/` and `job-market-analysis/`
  (private analysis output, not part of the shipped library); any SQLite
  schema changes; provider API contracts; browser-based application flow
  (already removed); building a comprehensive country/region gazetteer for
  location strings that carry no country signal at all.

## Acceptance criteria

- [ ] Two distinct postings that each list two or more cities no longer
      collapse to the same canonical job id merely because both display as
      "Multiple locations".
- [ ] A location string with a country/region token in a later comma-separated
      segment (e.g. `"Vienna, VA, USA"`) resolves to the correct country
      instead of matching an earlier, unrelated city-name hint.
- [ ] `english_ratio`/`german_ratio` no longer report high confidence from a
      handful of keyword hits on short or description-less text; low-evidence
      text classifies as unknown language rather than a false positive.
- [ ] `RequestCoalescer` no longer serves a permanently cached exception to
      callers after a single transient failure; the next independent call
      retries.
- [ ] A Bright Data request that fails with a network-level error (timeout,
      connection error) is retried with the same backoff as an HTTP-level
      transient error, instead of failing immediately.
- [ ] A single job's Notion write failure inside a publish batch does not
      abort the remaining jobs in that batch, and the sink reports which jobs
      failed instead of raising past the caller.
- [ ] One email track's Notion publish failure does not mark a different,
      already-successful track as failed, and does not block that other
      track's processed-message state from being saved.
- [ ] `config.py` rejects a `[sources.*]` field that is a plausible typo of a
      known field name (e.g. `max_detial_fetches`) instead of silently
      defaulting the real field and discarding the typo into `options`. A
      genuinely novel adapter-specific key (not close to any known field)
      still passes into `SourceConfig.options` unchanged, per the documented
      extension point in `docs/public/configuration.md`.
- [ ] `doctor`/`config validate` check every local profile by default when
      neither `--profile` nor `--all` is given, matching `run`'s default.
- [ ] `--profile` values containing path separators or `..` are rejected
      before being used to build a filesystem path.
- [ ] Offline tests cover each of the above with fictional data, and the
      repository quality gates pass.

## Design and constraints

Dependency direction and layering rules from `AGENTS.md` are unchanged: fixes
stay inside their existing layer (`domain`, `pipeline`, `application`,
`adapters`, `cli`, `configuration`). No new component IDs or registry entries
are introduced.

Identity/location: `domain/locations.py`'s `merge_locations` keeps its
human-readable `city` field ("Multiple locations" for ambiguous cases)
unchanged, but sorts the unique location list before joining `location_raw`,
making it order-independent. `domain/identity.py` prefers the fuller
`location_raw` over the lossy `city` sentinel when computing the canonical
location component, so two distinct multi-city postings no longer hash to the
same identity purely from the ambiguous display string.

Country classification: `pipeline/normalize.py`'s `known_location_country`
checks comma-separated segments from the last (most likely to carry a
country/region designator) to the first, instead of scanning the whole string
against `COUNTRY_LOCATION_HINTS` in dict-insertion order. This does not add a
new gazetteer of US state abbreviations or similar; a location string with no
country token anywhere remains an accepted known limitation, not silently
"fixed" with more heuristics.

Reliability: `RequestCoalescer.execute` evicts a key from its internal map on
failure before propagating the exception, so a future independent call is a
fresh attempt; concurrent callers already waiting on the same in-flight
`Future` still observe the original failure, which is correct coalescing
semantics. Bright Data's JSON request retry loop widens its caught exception
set to include the network-level errors that were previously wrapped into a
non-retryable error type before the retry loop could see them.

Failure isolation: `NotionDailySink._publish_locked` isolates each job's
Notion write in its own try/except, accumulates failures into the existing
`PublishResult.errors` field (already part of the port contract, previously
unused), and continues the batch. `jobs/ingest_email_recommendations.py`
mirrors this at the per-track level so one track's publish failure marks only
that track's run as failed and does not block saving processed-message state
for unrelated tracks.

CLI/config: `config.py`'s source-config loader flags a top-level key only
when it is a close (`difflib`, cutoff 0.8) match to a known field name,
distinguishing a likely typo of a known field from the documented
adapter-specific extension point, which still flows into `SourceConfig.
options` unchanged. `configuration/loader.py` validates `--profile` values
against the same identifier pattern `cli/bootstrap.py` already enforces for
`init`. `doctor`/`config validate` default to `--all` semantics instead of an
arbitrary first profile. These are documented, visible CLI/config behavior
changes for diagnostic commands, not silent default changes to what `run`
acquires or publishes.

No credentials, private runtime payloads, or real workspace data are touched
or embedded in source or tests. No SQLite schema migration is introduced;
storage fixes (`Database.connect()` foreign keys, `migrate_v1` read-only
source connection, atomic CSV writes) change connection/write behavior only.

## Verification

Add or extend focused offline `public_tests/` coverage for: multi-location
dedup identity stability, segment-based country resolution, low-evidence
language-ratio fallback, coalescer retry-after-failure, Bright Data
network-error retry, per-job Notion publish isolation, per-track email
publish isolation, unknown source-config field rejection, and profile-id path
traversal rejection. Manually run `job-scraper doctor` and
`job-scraper config validate` against this repository's local `ai`/`cpp`/
`it_adjacent` profiles to confirm the new default covers all three profiles
with a clear summary. Run the repository's standard quality gates: `uv run
ruff format --check .`, `uv run ruff check .`, `uv run pyright`, `uv run
pytest`. No live network/credentialed run is part of this verification.

## Follow-ups

- The dead-at-runtime `ImapEmailChannel` (`adapters/sources/email/imap.py`)
  is annotated as not exercised by `run`/`run_all_tracks` rather than fixed
  to add persisted state; deciding whether to remove it entirely is left for
  a separate change.
- Building a broader country/region gazetteer (e.g. US state abbreviations)
  for location strings with no explicit country token is deferred; not
  attempted here.
