# Browser-assisted Indeed acquisition

## Outcome

Provide a local, agent-operated path for turning Indeed recommendation-email
URLs into complete job details when an authorised interactive browser session
can render the listing. It restores useful Indeed coverage without enabling
the metered Bright Data source.

## Scope

- In scope, phase 1: a resumable, one-at-a-time local queue of
  email-discovered Indeed listing URLs; a validated result contract for title,
  company, location, description, and canonical platform URL; and import of a
  completed detail through the existing job pipeline.
- In scope, phase 2: separately design and validate browser-visible Indeed
  search-result discovery before it becomes a source component.
- Out of scope: copying browser profiles, cookies, or credentials; VPS or
  headless operation; browser login, CAPTCHA handling, application delivery,
  and concurrent browser navigation.

## What this change replaces

No existing public browser acquisition component exists. The survey covered
the source registry, email detail enrichment, CLI, extension guide, and public
specifications. `indeed_brightdata` remains available but disabled by default;
this change does not rename or re-enable it. The closest existing home is the
email-detail enrichment flow, which currently has only direct HTTP and Bright
Data detail providers and cannot call an interactive Codex browser session.

## Acceptance criteria

- [x] Phase 1 can emit unique, eligible Indeed email URLs without publishing
  jobs or mutating browser state.
- [x] One browser result at a time is validated before it can enter the
  existing `RawJobRecord` to pipeline to repository flow.
- [x] A missing description, login page, blocking page, or malformed result is
  recorded as a non-retry-storm terminal or deferred state and never treated as
  a complete description.
- [x] Queue state is resumable and idempotent; an already imported listing is
  not re-imported as a duplicate.
- [x] All tests are offline and use fictional URLs and browser-result payloads.
- [x] Phase 2 has a separately documented, explicit local browser-search
  queue; it remains unregistered as a source until live validation establishes
  a stable observable contract.

## Design and constraints

The public package owns only the deterministic queue and result-import
contract. An interactive local Agent owns browser navigation and must use a
single browser lane with explicit rate limiting. This keeps Codex-specific
browser control, profile state, and authentication outside the repository and
out of private configuration committed to source control.

The queue itself is a private JSONL checkpoint. `pending` items may be leased
one at a time as `in_progress`; a second lease is refused until the first row
is resolved. A browser Agent replaces that row with `complete` and structured
detail, or with `blocked`/`unavailable` plus an error. Successful import marks
the row `imported`. The queue has no HTML, screenshots, cookies, account data,
or browser-profile fields. Its task identity is the canonical Indeed URL and
`jk` value, so a refreshed email queue preserves prior terminal and imported
rows instead of creating another work item.

Browser work may be interrupted by login, CAPTCHA, site blocking, browser
disconnection, or user control. Those outcomes are recorded without bypass or
automatic retry escalation. Parsing, validation, pipeline evaluation, and
storage may remain independently concurrent after browser results arrive.

The existing platform listing URL stays authoritative. Normal deduplication,
manual-status import, CSV, and Notion safeguards remain unchanged. See the
architecture and extension guides for layer and Port boundaries.

## Verification

Add focused offline tests for queue eligibility, result validation, failure
states, idempotent import, and one-browser-lane enforcement. Run Ruff format,
Ruff check, Pyright, and Pytest. An independent agent must exercise the public
phase-1 user path with fictional artifacts. Live browser verification is a
separate, user-authorised check and is not part of the default suite.

## Follow-ups

Phase 2 browser-visible search discovery is specified in
[`2026-08-27-browser-indeed-search-discovery.md`](2026-08-27-browser-indeed-search-discovery.md).
It must not become a registered source merely because phase 1 can enrich known
email URLs.
