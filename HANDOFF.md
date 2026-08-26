# Handoff — production hardening cleanup (2026-08-26)

Branch `cleanup/production-hardening`, **uncommitted**. All four gates pass:
`ruff format --check`, `ruff check`, `pyright` (0 errors), `pytest` (184 passed).

Read `docs/public/specs/2026-08-26-production-hardening-cleanup.md` first — it is
the authoritative record of what changed and why. This file adds the operational
context a spec should not carry.

---

## State

| | before | after |
| --- | ---: | ---: |
| `src/` lines | 15,600 | 14,602 |
| public tests | 100 | 184 |
| pyright coverage | 2 largest files exempted (3,182 lines) | no exemptions |
| `cast()` in `src/` | 13 | 4 (all legitimate) |
| layering violations | 4 | 0 |

Diff scope: `+1,311 / -1,821` across `src/` and `public_tests/`.

## What changed

**Correctness.** Real migration runner for the workspace store (was: recorded
versions but never applied them). Notion no longer replays object-creating POSTs
after a 5xx (could duplicate pages); 429 still retries. Manual Notion status is
preserved for both `select` and `status` property types. Email state file is
written atomically and pruned to 90 days. `upsert_job` is one connection and one
transaction. Batched Bright Data submissions isolate per-input failures.
Lookup indexes added to both stores (real query plans went `SCAN` → `SEARCH`).

**Structure.** Export filtering now reuses the pipeline steps instead of a
second implementation. One country reference (`domain/countries.py`) replaced
four partial copies; one JSON-LD extractor replaced two. `policy_from_legacy`
moved to `configuration/`, so `pipeline/` no longer imports the TOML model.
Service-locator `services: dict[str, object]` replaced with typed fields;
`ComponentRegistry` is generic. That last change exposed a real contract lie —
`JobSink.publish` declared `Sequence[JobRecord]` while receiving `AcceptedJob` —
now fixed, with `AcceptedJob` promoted to `domain/`.

**Performance** (measured on real stored candidates, verdicts identical):
`normalize_candidate` 7.27 ms → 2.33 ms/job. Country inference on a miss
2,783 µs → 43 µs. Keyword matching compiles one alternation per keyword set
instead of one scan per keyword. Per-candidate process spawn removed from email
detail fetching (was ~163 ms each).

**Removed / archived.** Dead compat shims, `SQLiteV1Repository`, the Bright Data
legacy polling *discovery* branch, ~400 lines of unreferenced forwarding in
`run_daily`. Three one-off Notion migration scripts moved to
`local/one-off-notion-migrations/` (archived, not deleted — they were never in
git). Stale pre-V2 tests quarantined in `tests/stale_pre_v2/` with a README.

## Verification performed

- Sandbox full run against **copies** of all five production databases, live
  LinkedIn acquisition, 24h window, `--skip-notion`. Completed end to end,
  **exit code 0**. 4/4 tracks (28/47/18/35 accepted), four CSVs written, email
  phase processed 130 candidates across all four tracks, all five databases
  `integrity_check=ok`, migration applied once and idempotent.
  Row deltas were internally consistent (`jobs` and `application_state` grew by
  the same amount in every track database, so the new single-transaction upsert
  left no orphans).
- One behavioural change shows up on that run and is worth knowing before the
  first real run: the new 90-day retention pruned the email state from **882 to
  565** processed-message records. That is safe — the mailbox is only ever
  searched back `lookback_days` (7 by default), so a 90-day-old record can no
  longer suppress anything — but it does delete rows on first save.
- Notion write path verified **read-only** against 4,577 live pages: 194
  `Applied` + 350 `Not Interested` all survive a simulated update.
- Every performance change validated by replaying real candidates and comparing
  accept/reject verdicts, not just timings.

---

## Next task (approved, not started)

**Bind Notion by ID instead of by table title.**

Today all four tracks resolve their target by listing the parent page's children
and string-matching a database whose title equals `"{daily_table_prefix} Jobs"`.
The anchor is a mutable display name, so renaming the table in Notion, changing
`daily_table_prefix`, or two tracks sharing a prefix each cause a *new* database
to be created and the existing one to be orphaned.

`NOTION_DATABASE_ID` in `.env` is a `YOUR_ACTUAL_…` placeholder, filtered out by
`environment_credential`, so the `database_id` branch of `ensure_daily_database`
never runs. Note that filter is a magic-substring check — a placeholder written
as `<your-id-here>` would be treated as a real ID and collapse all four tracks
onto one database, each renaming it.

Shape of the change:

1. Resolve once by title as today, then persist the resulting
   `database_id` + `data_source_id` per track (a small file under `data/` is
   preferable to a workspace table while the V1/V2 split is unresolved).
2. On later runs bind directly by the stored id; fall back to title discovery
   and re-persist only if that id 404s.
3. Invert `sync_daily_schema`'s rename: with an id anchor, "title differs" means
   push the configured name to Notion, not adopt Notion's.
4. Surface the current binding per track in `db status`.

Do not touch any existing Notion object while implementing this.

## Traps — please read before changing anything here

1. **`[notion] container_title` is a live page name.** Three tracks carry the
   copy-pasted value `"C++ Job Scraper Daily Tables"`, including the AI track. It
   reads wrong and is load-bearing: editing it orphans the existing table
   (~2.9k publication rows). I changed it once during cleanup and reverted.
2. **`workspace.db → external_publications` is frozen at 2026-07-23.** Only
   `migrate_v1` writes it. Live publication state is each track database's
   `notion_sync_state`. I built an analysis on the frozen table and reached a
   conclusion that was wrong by 17x. It is now labelled `(frozen)` in
   `counts()` and `db status` — trust that label.
3. **Benchmark on the real candidate mix, not a synthetic job.** A synthetic
   benchmark said reordering the pipeline would be 12x faster; on real data it
   was 1.2–1.5x *slower* and was reverted. The reasoning is recorded next to
   `DEFAULT_STEPS`.
4. **Trace call sites before calling code dead.** The Bright Data polling
   machinery looked dead in the discovery path but is live for email detail
   enrichment.

## Known pre-existing issues (not introduced here, not fixed)

- **Bright Data timeouts.** `BRIGHTDATA_EMAIL_DETAIL_SNAPSHOT_TIMEOUT_SECONDS`
  is 120s; the observed median snapshot completion is 125s, so most time out by
  construction. Worse, `execute_resilient_brightdata_detail_batches` bisects on
  *any* exception including timeout, resubmitting the same URLs as new paid
  snapshots that also time out. Of 212 production snapshots: 64 consumed, 105
  ready-but-never-collected, 24 cancelled, 19 stuck. This explains the 70%
  `email_fallback` rate. **The owner reports Bright Data has upstream problems
  and has deprioritised this — do not act on it without asking.**
- **152 orphaned `notion_sync_state` rows** in `data/jobs.db` referencing
  deleted `jobs` ids (identical count in production and sandbox). These are why
  some already-published jobs fail the in-run duplicate check.
- **V1/V2 storage split unresolved.** Both schemas are written every run.
  ~1,513 lines, and it is the root cause of trap #2. Resolving it needs a real
  data migration — the owner asked for that to be done with them present.

## Not done deliberately

- Cumulative export retention exists and is tested but **defaults to disabled**
  (`[project] retained_exports = 0`). Enabling it deletes files the operator
  already has (188 CSVs / 528 MB in `exports/`); that is their call.
- No CSV or SQLite file was deleted, per `AGENTS.md`.
- No live Notion write was performed. The third verification phase — a real
  Notion-writing run — was never executed.
