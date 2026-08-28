# Ignore orphaned Notion status mappings

## Outcome

A stale Notion page mapping cannot make a scheduled acquisition run fail after
it has collected and published jobs. The invalid mapping is ignored, while a
safe existing fallback may still associate that page with a live job and valid
manual decisions continue to synchronize.

## Scope

- In scope:
  - Resolve a Notion page mapping only when its local job still exists.
  - Keep the existing title/company/URL fallback for pages without a usable
    mapping.
  - Add an offline regression test for an orphaned mapping.
  - Document the recovery behavior for operators.
- Out of scope:
  - Deleting or repairing historical Notion pages or local mappings.
  - Changing the meanings of `Applied`, `Not Interested`, or `Not Applied`.
  - Altering acquisition, publication, scheduling, or downstream screening
    selection.

## What this change replaces

`Database.find_job_id_by_notion_page_id` previously returned a stored mapping
without confirming its referenced job remained present. The existing lookup is
extended in place; no new storage type, configuration key, or parallel status
import path is added.

## Acceptance criteria

- [x] An orphaned mapping is not returned as a writable local job ID.
- [x] Importing a status for that page completes without a foreign-key error.
- [x] A valid page mapping still imports its status and preserves the page edit
  timestamp.
- [x] An unresolved page retains the existing safe fallback matching behavior.
- [x] An ambiguous fallback cannot write a status to a local job.
- [x] Scheduled acquisition can return success when every remaining pipeline
  stage succeeds, allowing its configured downstream action to run.

## Design and constraints

The local SQLite relationship is authoritative for write eligibility. The
mapping lookup joins `notion_sync_state` to `jobs`; an unmatched row behaves
like no mapping and may only proceed through the existing safe resolver. This
preserves foreign-key integrity without treating a historical mapping as a
reason to skip valid acquisition or publication work. A title/company fallback
remains valid only when it identifies exactly one job.

The change is local and idempotent: it neither writes to Notion during status
resolution nor alters or deletes historical data. It does not mask Notion
transport failures or unrelated SQLite errors.

## Verification

Run the focused offline status-import tests, then the repository Ruff,
Pyright, and Pytest quality gates. A live scheduled-run check is performed only
through the existing deployment path after the release manifest verifies.

An independent operator-path review is required because this affects the
scheduled daily workflow.

## Follow-ups

Expose a non-sensitive status-import outcome counter that distinguishes no
changed decisions from skipped stale mappings, without logging Notion payloads.
