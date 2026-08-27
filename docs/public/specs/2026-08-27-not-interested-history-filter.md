# Not Interested-only history exclusion

## Outcome

Previously observed, published, or applied jobs remain eligible for the
current run. Only a user decision of `Not Interested`, including a recent
matching repost, excludes an otherwise matching candidate.

## Scope

- In scope: candidate processing for direct sources and email-derived jobs;
  the public operations description of manual status behavior; and focused
  offline regression coverage.
- Out of scope: changing what statuses the Notion importer records, deleting
  existing job or decision history, re-publishing historical records, or
  changing the downstream screening feed's settled-status representation.

## What this change replaces

The closest existing home is `application/process_candidate.py`, which owns
candidate history decisions. Its `HistoryMode` and `already_seen` rejection
are removed rather than retained as a parallel policy. The existing Notion
status import continues to record `Applied` and `Not Interested` for audit and
downstream consumers; only the candidate-exclusion meaning changes.

## Acceptance criteria

- [x] An otherwise accepted job is not rejected merely because it was seen or
  previously published.
- [x] An `applied` status is recorded but does not exclude a current
  candidate.
- [x] A `not_interested` status and a matching recent repost remain excluded.
- [x] Changing `Not Interested` back to `Not Applied` clears the local
  exclusion at the next status import.
- [x] Email-derived jobs use the same history behavior as other candidates.
- [x] Public operations documentation states the new status boundary.
- [x] Focused offline tests and the repository quality gates pass.

## Design and constraints

The application use case remains the sole boundary between pipeline acceptance
and repository history. It still persists observations and profile decisions
for audit. No user data, runtime configuration, browser state, or historical
payload is embedded in public source or tests.

The Notion importer may retain an `applied` status because application history
is meaningful to downstream consumers; it is no longer an exclusion decision
for acquisition. `Not Interested` remains an explicit user opt-out, and the
existing bounded repost check remains in force.

## Verification

Add focused tests for seen, applied, and not-interested candidates; run Ruff,
Pyright, and Pytest. This is a non-trivial history-policy change, so an
independent agent must walk the public user path without private data.

## Follow-ups

Re-evaluating existing local history is an operational action, not part of
this policy change. It must use already stored records and must not re-fetch
browser pages.
