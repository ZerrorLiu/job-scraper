# Bright Data snapshot recovery after polling timeout

> Superseded timeout behavior: the current lifecycle contract is documented in
> [`2026-08-11-external-run-lifecycle-bounds.md`](2026-08-11-external-run-lifecycle-bounds.md).

## Outcome

When a Bright Data Indeed snapshot is already ready, the next identical run
can still reuse and consume it. A snapshot that exceeds the local polling
deadline is cancelled and is not reused, preventing stale provider runs from
keeping later acquisitions in `running`.

## Scope

- In scope: the default Bright Data snapshot polling window, recovery of ready
  snapshots, and cancellation of timed-out snapshots.
- Out of scope: changing Bright Data request payloads, retrying terminal
  provider failures, or changing job filtering and deduplication policy.

## Acceptance criteria

- [ ] The default polling window covers the observed long-running snapshots
      while remaining bounded.
- [ ] A snapshot that timed out locally is cancelled and is not resumable.
- [ ] A ready snapshot is not triggered again and is marked consumed only
      after its records are successfully yielded.
- [ ] Focused offline tests cover the timeout/recovery behavior.
- [ ] The repository quality gates pass.

## Design and constraints

The existing request hash and `external_snapshot_state` table remain the
recovery contract. A polling timeout is transient: it must not be recorded as
a terminal provider failure or make the snapshot consumed. The default wait is
increased to 1800 seconds to cover the observed 1111–1127 second executions;
the bound remains finite so a permanently stalled provider cannot block a run
forever. No private runtime data, credentials, databases, exports, or logs are
modified or deleted by the code change.

## Verification

Run the focused Bright Data adapter tests, then:

```powershell
uv run ruff format --check .
uv run ruff check .
uv run pyright
uv run pytest
```

The real Bright Data run is not repeated automatically because it can create
external requests and is not required for the offline regression test.

## Follow-ups

None.
