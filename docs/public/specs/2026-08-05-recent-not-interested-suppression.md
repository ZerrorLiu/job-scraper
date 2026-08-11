# Recent `Not Interested` suppression

## Outcome

When a job is reposted with a new source record, a recent manual `Not
Interested` decision for the same role must continue to prevent the candidate
from being accepted again.

## Scope

- In scope:
  - Suppress candidates matching a recent manual `Not Interested` decision.
  - Use a rolling 30-day window measured from the decision's edit time.
  - Match by normalized title, company, and location; an unknown location may
    fall back to title and company.
  - Preserve the existing exact-record handling for all processed statuses.
- Out of scope:
  - Suppressing every role from a company.
  - Changing the meaning of `Applied`.
  - Reading or writing private runtime configuration or external payloads.
  - Live Notion verification.

## Acceptance criteria

- [ ] A repost with a different job record is rejected when the matching
      `Not Interested` decision is less than 30 days old.
- [ ] A decision exactly 30 days old or older does not suppress a candidate.
- [ ] A different role or location is not suppressed by an unrelated decision.
- [ ] Imported Notion status uses the page's edit timestamp when available.
- [ ] Existing offline tests and all repository quality gates remain passing.

## Design and constraints

The legacy SQLite repository remains the active adapter for the daily and email
candidate flows. It records the normalized manual status and edit time, then
answers a narrow recent-history query for the application use case. The
application use case converts that result into a `Decision` with a dedicated
rejection reason; it does not re-evaluate upstream filtering.

The lookback boundary is exclusive: `decision_time > run_started_at - 30 days`
suppresses, while older decisions are eligible again. Matching is conservative
for known locations and falls back only when either side lacks a usable
location. No database migration or destructive cleanup is needed.

## Verification

Add credential-free tests for recent, boundary, expired, and non-matching
manual decisions, plus Notion edit-time import. Then run the repository gates:

```powershell
uv run ruff format --check .
uv run ruff check .
uv run pyright
uv run pytest
```

An independent user-path simulation is not required because this is an
internal decision-query behavior with no new CLI or public extension point.

## Follow-ups

None.
