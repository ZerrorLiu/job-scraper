# External acquisition run lifecycle bounds

## Outcome

External acquisition runs must finish with a visible terminal outcome. A
Bright Data snapshot that exceeds the local polling deadline is cancelled and
recorded as non-resumable, while a snapshot that is already ready remains
eligible for reuse. Email preparation must also bound Bright Data detail
enrichment so one stalled provider cannot keep the whole all-track run in
`running` indefinitely.

## Scope

- In scope: Bright Data snapshot timeout cleanup, local snapshot lifecycle
  status, and bounded Email detail enrichment.
- Out of scope: provider retry policy, job filtering semantics, Notion
  publishing policy, and live cancellation of historical snapshots created by
  older versions.

## Acceptance criteria

- [ ] A Bright Data polling timeout calls the provider cancel endpoint and
      records a terminal local status with the timeout reason.
- [ ] A successfully ready snapshot can still be downloaded and consumed;
      timed-out or cancelled snapshots are not resumed by a later request.
- [ ] Email Bright Data detail enrichment has a finite total timeout and falls
      back to the existing email-card path when that timeout is reached; active
      provider snapshots are cancelled as the local task is cancelled.
- [ ] Offline tests cover cancellation, timeout status, ready reuse, and Email
      enrichment timeout fallback with fictional data.
- [ ] The repository quality gates pass.

## Design and constraints

The existing request hash and `external_snapshot_state` table remain the
recovery contract for ready snapshots. Timeout cleanup uses Bright Data's
documented snapshot cancellation endpoint. Cancellation errors are recorded
without hiding the original timeout, because the provider may already have
transitioned the snapshot to another terminal state. No credentials, private
runtime payloads, or external historical snapshots are embedded in source or
tests.

## Verification

Run focused offline acquisition tests, then the repository format, lint,
type-check, and default test commands from the project guide. Do not repeat a
live Bright Data or mailbox run as part of the offline regression suite.

## Follow-ups

None.
