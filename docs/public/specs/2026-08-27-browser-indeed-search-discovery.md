# Browser-visible Indeed search discovery

## Outcome

Allow an authorised local interactive browser to turn the enabled track search
matrix into a resumable, one-at-a-time list of visible Indeed search pages, and
turn the visible result cards into the existing browser-detail queue. This
restores a non-metered discovery path without treating browser control as a
repository source adapter.

For the multi-client service, preserve this queue boundary behind a
client-local Codex worker: the VPS retains work durably, while each user's own
Codex account and Chrome plugin operate that user's selected existing Chrome
profile. No browser profile, session, credential, cookie, task, or result is
shared between clients. The browser executor is the Codex App task itself; a
standalone `codex exec` subprocess and a separate browser-automation program
are not part of the production path.

## Scope

- In scope: deterministic search-task generation from existing track query and
  location settings, a separate search-result checkpoint, validated visible
  card fields, and expansion into the existing detail queue.
- Out of scope: browser automation inside this repository, headless/VPS Chrome,
  copying or reading Chrome profile data, CAPTCHA solving, job application or
  submission, and direct search-result or detail-page HTTP requests.
- The current JSONL implementation does not yet provide remote enrollment,
  HTTPS lease/result transport, or multi-client services. Those approved target
  contracts are specified below and remain implementation work.

## What this change replaces

The closest existing home is the browser-detail queue in
`integrations/browser_details.py` and its `ingest-email` CLI modes. It accepts
known email URLs but has no representation for a browser-visible search page.
The search queue extends that same local-contract boundary rather than adding a
new registered source, because the package must not own a browser session.
The committed local JSONL contract remains a compatibility input during
cutover; the uncommitted server/client prototypes superseded by the approved
target are listed below.

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
  escalation or an in-repository browser driver.
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

The local Codex worker remains responsible for visible navigation and paced,
single-lane operation through the user's Chrome plugin. A browser block, login
page, CAPTCHA, or unavailable search page is a terminal recorded outcome; it is
not an instruction to bypass access controls. The existing browser-detail
contract validates the final job description before the application pipeline
processes it.

## Approved client/server implementation contract

This section is the authority for the browser slice of the server/client
split. The broader screening and artifact boundaries remain in
`2026-08-28-first-class-agent-screening.md`. The implementation may change
libraries only by updating this spec first; it must not create a second queue
or browser path alongside this one.

### Fixed technology choices

| Concern | Decision | Reason |
|---|---|---|
| Browser executor | A dedicated local Codex App worker task using the installed Chrome plugin | This is the already-approved surface that can operate the user's real logged-in Chrome without exporting the profile |
| Browser interaction | Visible Chrome navigation, semantic element interaction, scrolling, waiting, expansion, and reading through the plugin | Indeed is handled like a human-operated site, not as an HTTP data endpoint |
| Chrome identity | The existing Chrome profile whose extension instance the user connects during enrollment | Profile selection and login remain in Chrome; no path, cookie, password, history, or browser identifier is sent to the VPS |
| Worker scheduling | A recurring Codex App heartbeat attached to one dedicated local worker task per client | It keeps the browser capability inside Codex App and avoids treating `codex exec` or an OS Python daemon as the agent |
| Client helper | `positions-client` CLI for enrollment, claim, heartbeat, local journal, completion, and status | The helper never drives Chrome or makes screening decisions; secrets stay in the OS credential store |
| API runtime | FastAPI contract adapter served by one Uvicorn worker per client | It replaces the stdlib `http.server` prototype with explicit schema/error handling and a production ASGI server |
| Transport | Client-initiated HTTPS pull through the existing Cloudflare Tunnel ingress with bearer device credentials | It requires no public VPS listener and works behind client NAT |
| Server process model | One OS user, Uvicorn process, loopback port, SQLite database, runtime directory, and timers per client | A request cannot select another tenant in application code because the ingress hostname has already selected one isolated process |
| Queue persistence | SQLite on VPS-local storage plus a transactional outbox | Leases remain durable and downstream expansion/import cannot be lost after an accepted result |

`codex exec` remains valid elsewhere as a structured-output CLI, but it is not
the Chrome-plugin host. A declined browser permission from a non-interactive
`codex exec` run is therefore an invocation error, not evidence that the
client's Codex App browser capability is unavailable.

The Codex worker receives the client-specific task URL, query/location values,
and visible public job content. Each client uses their own Codex account and
Chrome extension connection; onboarding must disclose that those values are
processed in that client's Codex environment. The worker never reads Chrome
cookies, storage, passwords, history, or profile files.

### Prototype replacement plan

The in-progress prototypes are inputs to this implementation, not parallel
production paths:

- the stdlib `ThreadingHTTPServer` adapter is replaced by the FastAPI/Uvicorn
  adapter in the same server boundary;
- inline search expansion/detail import in the HTTP completion handler is
  replaced by the transactional outbox consumer;
- `WindowsChromeProfileLocator` is removed because Chrome selection belongs to
  the Codex Chrome-plugin connection, not the transport CLI;
- the Python `core_loop`/`StubBrowserWorker` prototype is replaced by
  command-level transport operations orchestrated by the Codex worker skill;
- Windows Task Scheduler browser execution is replaced by one Codex App
  recurring heartbeat attached to the dedicated worker task;
- the local JSONL queue remains a manual compatibility workflow until network
  cutover proves parity, after which its removal requires a reference search
  and an explicit migration note in this spec.

No second server command, `_v2` module, standalone browser engine, or browser
queue is added beside these replacements.

### Repository and contract ownership

`job-scraper` remains the VPS/server repository. It owns task generation,
versioned HTTP schemas, authorization, SQLite queue/outbox, search expansion,
detail validation, canonical import, and downstream orchestration. It contains
no Chrome-plugin calls or local Codex automation code.

`positions-client` is the separate local delivery repository. It owns the thin
transport CLI, OS credential-store adapter, local work journal, and the Codex
browser-worker plugin/skill. It contains no track policy, email parser,
screening, CV/evidence, Notion, canonical-job database, or copy of server
business logic.

The server publishes OpenAPI plus versioned fictional request/result fixtures.
The client vendors the released schema/fixture version and runs conformance
tests; it does not import `job_scraper` Python modules at runtime. Compatible
additive fields are preserved and ignored when unknown. An unknown major
`contract_version` fails before a task is claimed. Contract upgrades are made
in place with an explicit compatibility/removal condition, never through a
parallel endpoint or `_v2` module.

### Isolation

Only read-only release code, versioned schemas, and fictional conformance
fixtures are shared. Every client has a separate Linux service account,
runtime directory, configuration, database, browser queue, outbox, locks,
logs, credentials, output, backup, and Notion destination. Fine-screen state,
CV/evidence, artifacts, agent cache, and track/query policy are likewise
per-client. There is no shared job catalog or cross-client result cache.

The existing Cloudflare Tunnel ingress maps an opaque per-client HTTPS hostname
to one tenant-specific loopback port before the request reaches `job-scraper
serve`. Uvicorn binds to `127.0.0.1`, never a public interface, and runs one
worker because that process owns one SQLite queue. The application process
mounts one tenant runtime and has no tenant selector. A browser worker
credential is bound to exactly one device in that runtime. Request bodies do
not contain `client_id`; a request field, task payload, query, or Chrome profile
name can never select another client.

The current uncommitted stdlib `ThreadingHTTPServer` adapter is a prototype and
is superseded by the FastAPI/Uvicorn adapter. It is replaced in place, not kept
as a compatibility endpoint. Cloudflare access policy may add another gate,
but tenant authorization still belongs to the application device credential;
the tunnel is not treated as authentication.

The shared read-only release is installed once. Per-client state lives under a
root-provisioned runtime such as `/srv/positions/clients/<client-slug>/`, owned
by a dedicated `positions-<client-slug>` service account and mode-restricted
from every other client account. The runtime contains separate `config`,
`data`, `run`, `logs`, `exports`, `artifacts`, and `backups` roots. The initial
unit set is:

```text
positions-api@<client-slug>.service
positions-browser-outbox@<client-slug>.service
positions-daily@<client-slug>.timer
```

The slug and loopback port exist only in the root-owned provisioning inventory
and service configuration. They never appear in browser task bodies. Backup
sets, retention, restore tests, and Notion credentials are per-client even when
the backup transport and code release are shared infrastructure.

During setup, the user connects the Chrome plugin from the Chrome profile they
want this client to use. The Codex worker selects that connected Chrome surface
and operates only agent-created or explicitly claimed tabs in that profile. It
does not enumerate local Chrome profiles, store a profile path, copy a user-data
directory, or attach to another user's extension instance.

`positions-client` maintains a current-user-only local browser-binding registry.
The Codex setup records a one-way fingerprint of the connected extension
instance against the immutable server-issued client identity; the raw browser/
extension identifier never leaves the computer. Version 1 gives that
fingerprint permanent single-client ownership, not merely an active alias.
Offboarding deactivates the binding but retains a non-secret ownership
tombstone, so the same Chrome cookies/session cannot later be assigned to a
different client. Re-enrollment is allowed only for the same server client
identity. A different client must use a different Chrome profile/extension
surface.

`doctor` fails closed on duplicate ownership, an alias/client mismatch, or an
unexpected fingerprint change until the user explicitly restores the same
client binding. The worker mutex is keyed by that fingerprint, so two Codex
tasks cannot operate the same connected Chrome surface concurrently.

One local worker mutex also prevents overlapping heartbeats for the same enrollment.
The VPS receives neither browser identifiers nor cookies, passwords, history,
rendered HTML, or screenshots. A shared VPS Chrome profile and cross-client
browser sessions are forbidden. Version 1 supports one active browser-worker
enrollment per client; adding another device requires an explicit credential
and scheduling extension rather than sharing the first device's state.

Local screenshots or HTML snapshots are disabled by default. A user may enable
a bounded local diagnostic capture after a blocked run; it has an explicit
retention limit and is never uploaded as a browser result.

### Versioned HTTPS API

The client initiates every HTTPS connection; the VPS never pushes directly to
a client computer. TLS terminates at Cloudflare Tunnel and the tenant process
binds only to its assigned loopback port. Version 1 exposes:

```text
POST /v1/enrollments/redeem
POST /v1/browser/tasks/claim
POST /v1/browser/tasks/{task_id}/heartbeat
POST /v1/browser/tasks/{task_id}/results
```

The one-time enrollment token is sent in the enrollment authorization header,
is stored hashed, expires, and can be redeemed once. Redemption creates a
random device credential. The client stores that credential in the OS
credential manager; local configuration contains only the endpoint, local
alias, and contract version. Server-side device credentials are hashed, scoped
to allowed task kinds, rotatable, and revocable.

Authenticated requests use `Authorization: Bearer`. Claim includes supported
contract versions and ordered capabilities (`detail` before `search`) and
returns `204` when no task is available. A claimed task carries
`contract_version`, `schema_version`, opaque `task_id` and `lease_id`,
`search|detail` kind, exact allowed Indeed URL, creation time, and lease expiry.
Search payloads also carry query/location display values, a maximum visible-card
count, and a server-generated search-scope occurrence; detail payloads carry
only minimal card provenance.

All task URLs are HTTPS and remain inside the configured Indeed domain family.
Email tracking links may redirect only to another validated Indeed host; a
cross-site redirect is `blocked`, not followed as a new source. The task never
contains mailbox credentials, message bodies, Chrome state, Notion state, or
personal CV/evidence.

The default lease is ten minutes and the worker heartbeats at least once every
two minutes while Chrome work is active.
Heartbeat extends only the current lease. `409` means the lease has been lost:
the worker stops browser processing and does not submit a result. Expired
leases atomically return to `pending`. A client-generated idempotency key is
written to its local journal before result submission and reused for every
retry of those same bytes.

`positions-client claim --json` first acquires a per-enrollment local mutex,
then stores the claimed envelope in
`%LOCALAPPDATA%/PositionsClient/<local-alias>/work.db` before printing the
non-secret task JSON to the Codex worker. `heartbeat` and `complete` resolve the
device credential from the OS credential store; the token is never printed or
placed in a prompt. `complete` first stores the exact result bytes and
idempotency key in the local journal, then submits them. A retry reads those
same bytes. The journal clears the active lease only after the server's durable
accepted response and retains a bounded local audit outcome.

Results contain the current lease identity, idempotency key, observation time,
and `complete|blocked|unavailable`. A successful search contains validated
cards; a successful detail contains the canonical Indeed reference, title,
company, location, and complete visible description. Terminal failures contain
a bounded reason code and message, not page content. The server returns `409`
for a stale lease, `422` for a schema/URL violation, and the original accepted
outcome for an exact idempotent replay.

The version 1 terminal payloads are:

```text
Search complete:
  task_id, lease_id, idempotency_key, status, observed_at
  cards[]: url, source_job_id, title, company_name, location_raw, context

Detail complete:
  task_id, lease_id, idempotency_key, status, observed_at
  canonical_url, source_job_id, title, company_name, location_raw, description

Blocked/unavailable:
  task_id, lease_id, idempotency_key, status, observed_at
  reason_code, message
```

Allowed terminal reason codes are `login_required`, `captcha`, `access_denied`,
`page_unavailable`, `unexpected_layout`, and `navigation_outside_allowlist`.
`lost_lease` is a local transport outcome and is not submitted as a browser
result. Error messages are length-bounded and contain no page HTML, screenshot,
cookie, token, or mailbox content.

### Durable queue and outbox

Each tenant queue is one SQLite database on VPS-local storage; network filesystems
are forbidden. Every operation opens a short-lived connection, enables foreign
keys and a bounded busy timeout, and uses an explicit write transaction for
claim, heartbeat, completion, enrollment, and credential rotation. Claim uses
one atomic compare-and-set transition; a production regression test must start
the HTTP server and queue writer as separate OS processes against the same
resolved database path.

Transport state and downstream state are separate:

```text
pending -> leased -> terminal(complete|blocked|unavailable)
                         |
                         +-> outbox pending -> applied|failed
```

The result request only validates the contract and atomically writes the
terminal result plus an outbox row. It does not call search expansion, the job
pipeline, or Notion inside the HTTP request. A tenant-local outbox worker then:

- expands a completed search into deduplicated detail tasks; or
- imports a completed detail through the existing canonical pipeline.

The outbox consumer is idempotent and records its own attempt/error state. An
HTTP retry therefore cannot duplicate downstream work, and a pipeline failure
cannot strand an already-terminal task. `blocked` and `unavailable` create no
pipeline job; their retention and any manual retry remain explicit operations.

An outbox row carries `pending|processing|applied|failed`, attempt count,
`next_attempt_at`, and a bounded `last_error`. The worker automatically retries
at most five times with capped backoff, then leaves a poison item in `failed`;
it never deletes or silently skips it. The approved administrative contract is:

```text
job-scraper browser status --json
job-scraper browser outbox list --state pending|failed
job-scraper browser outbox retry --event-id <id> --expect-count 1
```

These are target commands, not current-release claims. Status reports queue
counts, oldest age, active leases, outbox counts, and last applied event without
showing payloads. Manual retry requires an exact event and expected count,
reuses the original idempotency identity, and never edits the stored result.
Client enablement requires evidence that the calibration event reached
`applied` and that no unexpected `pending` or `failed` item remains.

Search and detail identity deliberately differ. A search is recurring work, so
its task identity includes the search-scope ID and scheduled occurrence window;
the same query/location can run again on a later day. A detail task is keyed by
the canonical Indeed `jk` reference, so repeated discovery converges on one
posting. An existing complete detail is never replaced by a weaker result.

### Local browser execution

For one claimed task, the Codex worker uses the connected Chrome plugin,
processes one page at a time, validates the final URL and visible fields, and
releases or closes its task tab when finished. It does not use direct HTTP,
export cookies, submit applications, or operate other sites.

Search handling is bounded to the configured first result page and maximum
visible-card count. The worker opens the server-supplied search URL, waits for
the rendered page, scrolls through visible cards, and reads their visible
title/company/location and `viewjob` link. Every card must resolve to an Indeed
`jk` reference and cards are deduplicated by that reference. It does not treat
an empty selector result as a valid empty search without first checking the
visible page for loading, login, consent, or access-block state.

For a detail task, the worker opens the exact allowed card URL, follows normal
Indeed navigation, expands the job description when the page presents an
expand control, scrolls through the complete visible description, and returns
the final Indeed reference, title, company, location, and full description.
Semantic visible-page interaction is preferred; coordinate interaction is a
fallback only when the same visible control cannot be addressed semantically.
There is no stealth mode, browser fingerprint manipulation, or attempt to
disguise automation.

Login walls, CAPTCHA, access blocks, consent state that cannot be handled by
normal visible interaction, and unexpected navigation produce `blocked` or
`unavailable`. They are recorded, never bypassed, and stop the source for a
bounded cooldown instead of creating a retry storm. A blocked tab may be left
as a user handoff and the client is notified; the unattended worker never solves
a CAPTCHA or submits an application.

### Indeed search and email convergence

Indeed browser search tasks are generated only from that client's own profiles,
queries, locations, and source policy. A completed search returns visible cards;
each validated card becomes a separate detail task. Search cards never enter
the job pipeline directly.

The browser-detail branch of the email channel is deliberately Indeed-only. It
extracts minimal message/card provenance from authorized Indeed recommendation
mail and creates a detail task for every Indeed card because the email payload
is not a complete posting. The browser resolves redirect links, obtains the
canonical Indeed job reference and full description, and returns a validated
detail. Non-Indeed recommendation mail retains its existing non-browser channel
behavior and provenance; this design does not retire or reroute it. Thus the
two Indeed entrances converge before normalization:

```text
Indeed search -> visible cards --+
                                  +-> browser detail -> canonical pipeline
Indeed email  -> email cards -----+
```

An incomplete, blocked, or unavailable detail remains durable but is not
screened, tailored, or published to Notion.

### Cold start and availability

Server-side `create-client` creates a new Linux service account, empty runtime
tree, tenant SQLite database, assigned loopback port and systemd instances,
Cloudflare ingress record, backup target, and single-use enrollment token. It
never copies another user's tracks, keywords, mail state, Notion destination,
CV/evidence, jobs, or cache. Shared release code is mounted read-only. The
user-facing setup procedure lives in the planned multi-client section of
`agent-deployment.md`; this spec defines its required outcome and gates.

`positions-client doctor` verifies HTTPS and contract compatibility, device
authentication, OS credential storage, and the local journal. The Codex worker
skill separately verifies its own account, Chrome-plugin connection, selected
Chrome surface, local mutex, and ability to create and clean up a harmless tab.
The guided Codex setup reports those checks as one doctor result without asking
the CLI to inspect Chrome.

The recurring heartbeat is attached to one dedicated Codex worker task. On
each wake it acquires the local mutex and processes detail before search. It
claims and completes tasks sequentially until three tasks or fifteen minutes,
whichever comes first. An empty queue ends the run immediately. A browser block,
lost lease, contract error, or repeated transport failure stops that wake. The
automation prompt contains workflow instructions only; client credentials stay
behind the `positions-client` CLI and OS credential store.

Production scheduling is enabled only after one authorized search, one
email-card detail, expired-lease recovery, an outbox replay, and downstream
client-specific screening/Notion publication pass. If the computer is powered
off, Codex local host is unavailable, Chrome is disconnected, or the client is
offline, VPS work remains pending while non-browser VPS sources continue.
Continuous browser availability requires a client-owned always-on Codex/Chrome
host, never a shared browser on the main VPS.

### Remaining implementation acceptance

- [ ] Server client creation, per-client OS/process/storage isolation, and
  reverse-proxy routing are implemented and cross-client access tests fail
  closed.
- [ ] One-time worker enrollment, credential rotation/revocation, versioned
  claim, heartbeat, expiry, local-journal idempotency, and terminal results have
  conformance tests.
- [ ] Result acceptance and downstream work use a transactional outbox with
  restart/replay tests; the HTTP handler performs no pipeline or Notion write.
- [ ] A real separate-process writer/server test resolves the earlier queue
  visibility failure and verifies recurring search occurrences.
- [ ] The Codex worker skill uses only the client user's connected Chrome
  plugin surface; `positions-client` contains no browser driver/profile reader
  and sends no browser identifier, profile path, cookie, credential, HTML, or
  screenshot to the server.
- [ ] Local browser-binding tests reject concurrent and sequential cross-client
  fingerprint reuse, retain ownership tombstones on offboarding, and allow
  credential rotation/re-enrollment only for the same immutable client identity.
- [ ] A real recurring Codex worker wake claims through `positions-client`,
  heartbeats during Chrome work, submits the same journaled idempotency key on
  retry, and exits cleanly when the queue is empty.
- [ ] Indeed search cards and every Indeed email card require a successful
  browser detail before pipeline admission.
- [ ] Offline/lost-lease recovery, blocked-page cooldown, outbox replay, and a
  bounded live browser validation pass before recurring production enablement.

## Verification

Add focused offline tests and run Ruff format/check, Pyright, and Pytest. This
is a non-trivial CLI and public workflow change, so an independent user-path
simulation is required. A small, user-authorised live browser validation is
separate from the default test suite.

## Follow-ups

After live validation establishes observed result-card stability, decide
whether search pagination is needed. It must extend the same occurrence/task
contract and bounded lane; it must not add another source or browser queue.
