# Browser-visible Indeed search discovery

## Outcome

Allow an authorised local interactive browser to turn the enabled track search
matrix into a resumable, one-at-a-time list of visible Indeed search pages, and
turn the visible result cards into the existing browser-detail queue. This
restores a non-metered discovery path without treating browser control as a
repository source adapter.

For the future multi-client service, preserve this queue boundary behind a
client-local worker: the VPS retains work durably, while each user authorizes
Codex to use that user's own Chrome profile. No browser profile, session,
credential, cookie, or result is shared between clients.

## Scope

- In scope: deterministic search-task generation from existing track query and
  location settings, a separate search-result checkpoint, validated visible
  card fields, and expansion into the existing detail queue.
- Out of scope: repository browser automation, headless/VPS operation,
  credential or cookie handling, CAPTCHA handling, continuous scheduling, and
  direct search-result or detail-page HTTP requests.
- The current JSONL implementation does not yet provide remote enrollment,
  HTTPS lease/result transport, or multi-client services. Those approved target
  contracts are specified below and remain implementation work.

## What this change replaces

The closest existing home is the browser-detail queue in
`integrations/browser_details.py` and its `ingest-email` CLI modes. It accepts
known email URLs but has no representation for a browser-visible search page.
The search queue extends that same local-contract boundary rather than adding a
new registered source, because the package must not own a browser session.
Nothing is superseded.

## Acceptance criteria

- [x] A local command can generate deduplicated Indeed search tasks from a
  track's existing query and location matrix without contacting Indeed.
- [x] Search tasks and visible-card results are validated, resumable, and have
  one browser lease at a time.
- [x] A completed search task can add its validated cards to the detail queue
  without replacing an imported or terminal detail result for the same URL.
- [x] Discovered details enter the existing normalization, filtering, storage,
  and publication path with `indeed` as their job-source provenance.
- [x] The browser path records blocking and no-result outcomes without retry
  escalation or browser automation.
- [x] Offline tests cover URL construction, lease rules, card validation,
  idempotent expansion, and result provenance.

## Design and constraints

The private search matrix uses the same profile composition as a normal run,
then reads the resulting query/location values from the runtime source settings.
The target country and optional Indeed market override use the same market
resolution as the direct Indeed adapter; the browser queue never silently
falls back to another country storefront. The queue stores only the concrete
browser URL, query/location values, and the visible-card values needed to
request a detail; it never stores rendered HTML, screenshots, credentials,
cookies, or browser-profile data.

Search and detail work are separate JSONL checkpoints. The search queue
transitions from `pending` to one `in_progress` lease, then to `complete`,
`expanded`, `blocked`, or `unavailable`. A completed search result supplies
canonical Indeed `viewjob` URLs and non-empty title, company, and location
values. Expansion creates detail tasks keyed by the canonical URL and preserves
the furthest existing detail state.

The interactive agent remains responsible for visible navigation and paced,
single-lane operation. A browser block, login page, CAPTCHA, or unavailable
search page is a terminal recorded outcome; it is not an instruction to bypass
access controls. The existing browser-detail contract validates the final job
description before the application pipeline processes it.

## Approved client/server browser design

### Isolation

Only code and schemas are shared. Every client has a separate server runtime,
configuration, database, browser queue, locks, logs, credentials, output, and
Notion destination. A browser worker credential is bound to exactly one client;
the server resolves that binding before parsing a task request. Neither a
request field nor a Chrome profile name can select another client.

During enrollment, the user selects an existing Chrome profile on their own
computer. The local worker keeps the profile identity/path locally and obtains
an exclusive lock before browser work. The VPS receives neither that path nor
cookies, passwords, rendered HTML, or screenshots. A shared VPS Chrome profile
and cross-client browser sessions are forbidden.

### Pull, lease, and result transport

The client initiates every HTTPS connection; the VPS never pushes directly to
a client computer. A worker claims at most one task with a short lease,
heartbeats during visible navigation, then submits an idempotent terminal
result. The protocol provides the equivalent of:

```text
POST /browser/tasks/claim
POST /browser/tasks/{task_id}/heartbeat
POST /browser/tasks/{task_id}/complete
POST /browser/tasks/{task_id}/blocked
```

The task envelope contains a version, opaque task and lease IDs, profile ID,
`search|detail` kind, exact URL or query/location, creation/expiry times, and an
idempotency key. Result submission repeats the current lease identity and
contains only validated visible fields, observation time, and
`complete|blocked|unavailable`. A missing client remains `pending`; an expired
lease returns to that same queue. Duplicate completion is safe. Login walls,
CAPTCHA, access blocks, and unavailable pages are recorded, not bypassed.

The intended local executor is `positions-client` plus an installed Codex
browser skill. Codex uses the user's authorized Chrome profile to perform the
visible interaction; the CLI owns authentication, claiming, schema validation,
heartbeats, and result submission. Continuous browser availability, if needed,
uses a client-owned always-on device with its own Chrome profile rather than a
shared browser on the main VPS.

### Indeed search and email convergence

Indeed browser search tasks are generated only from that client's own profiles,
queries, locations, and source policy. A completed search returns visible cards;
each validated card becomes a separate detail task. Search cards never enter
the job pipeline directly.

The email channel is deliberately Indeed-only. It extracts minimal message/card
provenance and creates a detail task for every card because the email payload is
not a complete posting. The browser resolves redirect links, obtains the
canonical Indeed job reference and full description, and returns a validated
detail. Thus both entrances converge before normalization:

```text
Indeed search -> visible cards --+
                                  +-> browser detail -> canonical pipeline
Indeed email  -> email cards -----+
```

An incomplete, blocked, or unavailable detail remains durable but is not
screened, tailored, or published to Notion.

### Cold start

Server-side `create-client` creates an empty isolated runtime and one-time
enrollment token; it never copies another user's tracks or keywords. Local
`positions-client enroll`, `setup`, and `doctor` bind one device, verify Codex
and browser capability, record the chosen Chrome profile locally, configure the
client's Indeed mail boundary and job profiles, and exercise claim/result
transport. Initial calibration is count-bounded and read-only. Production is
enabled only after one authorized search, one email-card detail, offline lease
recovery, and downstream client-specific screening/Notion publication pass.

### Remaining implementation acceptance

- [ ] Server client creation and storage/process isolation are implemented and
  cross-client access tests fail closed.
- [ ] One-time worker enrollment, credential rotation, task claim, heartbeat,
  expiry, idempotent completion, and blocked results have conformance tests.
- [ ] `positions-client` uses only the locally selected Chrome profile and
  retains no server-visible browser credential or profile path.
- [ ] Indeed search cards and every Indeed email card require a successful
  browser detail before pipeline admission.
- [ ] Offline client recovery and a bounded live browser validation pass before
  any recurring production enablement.

## Verification

Add focused offline tests and run Ruff format/check, Pyright, and Pytest. This
is a non-trivial CLI and public workflow change, so an independent user-path
simulation is required. A small, user-authorised live browser validation is
separate from the default test suite.

## Follow-ups

After a live validation establishes observed result-card stability, decide
whether to add an explicit bounded browser-assisted source component. Do not
make that decision merely from the queue implementation.
