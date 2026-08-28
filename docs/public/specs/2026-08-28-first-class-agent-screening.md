# Cleanup-first unified screening workflow

## Outcome

Establish one understandable and supportable job-search workflow before adding
new service architecture.

The work proceeds in this order:

1. Freeze the current production boundary and inventory what is actually live.
2. Remove or migrate dead code, superseded features, duplicate implementations,
   and stale documentation using explicit removal conditions.
3. Reconcile the private CV/evidence sources and declare one factual authority.
4. Unify acquisition, hard gates, track routing, agent screening, resume
   tailoring, artifact validation, and publication into one workflow.
5. Prove that workflow for one client locally and on the VPS.
6. Only then design the physical split between a multi-client server project
   and local client projects.

The target workflow distinguishes the main career track from broader discovery
tracks. The main track may use agent screening and resume generation. Tracks
that do not match the client's professional evidence remain useful for market
discovery or manual review, but do not generate resumes automatically. Notion
is the final display sink, not the workflow coordinator or source of truth.

## Scope

- In scope now:
  - Inventory the current `Positions`, fine-screen, and private CV surfaces and
    classify each component as `KEEP`, `MIGRATE`, `DELETE`, `ARCHIVE`, or
    `RECONCILE`.
  - Remove confirmed dead code, retired switches, superseded documentation,
    and duplicate runtime paths without deleting user data or weakening a live
    production path.
  - Reconcile divergent resume masters, evidence files, profile notes, and
    compatibility names before declaring one private factual source of truth.
  - Define the stable boundaries between acquisition, hard gates, track
    routing, semantic screening, resume generation, artifact validation,
    durable state, and display sinks.
  - Support track-specific behavior so resume generation is enabled only where
    the client's evidence makes it credible.
  - Make Notion consume finalized durable state after screening and document
    processing rather than participate in those decisions.
  - Migrate through dry runs, replay, shadow operation, bounded cutover, and a
    documented rollback window.
  - Prove a single-client local and VPS workflow before adding multi-client
    infrastructure.
- Out of scope for this change:
  - Physically splitting the repository into server and client products.
  - Building multi-client identity, tenancy administration, billing, web UI,
    OAuth brokerage, or a public API.
  - Automatic applications, recruitment-site uploads, CAPTCHA bypass, or final
    submission.
  - Deleting databases, PDFs, caches, private workspaces, or historical source
    material merely because the current code no longer reads them.
  - Treating generated resumes or historic candidate PDFs as factual evidence.

## What this change replaces

This specification is the active design authority for unified screening. It
supersedes the former downstream-agent-orchestration specification. Its
still-valid compatibility behavior is incorporated below, in the screening
feed specification, and in `operations.md`; the superseded file is deleted
because Git history is the archive.

The existing public acquisition framework, standalone fine-screen package, and
private CV workspace remain migration inputs until their behavior and data have
been reconciled. This specification does not authorize deleting or archiving
either private workspace. After cutover there must be one runnable screening
authority, one resume-tailoring implementation, and one declared private
evidence authority.

The existing screening feed and legacy V1 storage remain compatibility
boundaries only while replay and rollback require them. Their removal is a
separate, evidence-backed cleanup action, not an assumption made during the
initial merge.

## Current assessment

The current system is not a dead-code swamp. Its public acquisition core,
adapter boundaries, offline tests, and production scheduling are valuable. The
main risk is accumulated ambiguity: overlapping CLI paths, legacy state beside
new state, duplicated screening code, divergent private CV sources, and active
documents that describe different future architectures.

The cleanup ledger below is the initial classification. Every implementation
phase must update it with references, evidence, and the final disposition.

| Surface | Initial class | Required action | Removal condition |
|---|---|---|---|
| Public acquisition adapters and pipeline | `KEEP` | Preserve current behavior while refactoring orchestration | Not applicable |
| Former hidden/deprecated `run` switches | `DELETE` complete | Removed `--all`, `--init-db`, and `--enable-indeed`; `run` defaults to enabled profiles, `db init` owns initialization, and profile composition plus the paid-source circuit breaker own Indeed activation | Local/VPS service and history audit found no active caller; focused CLI and full offline tests pass |
| `run_daily.py` / `run_all_tracks.py` orchestration overlap | `MIGRATE` advanced | CLI and scheduler now translate once into typed `AllTracksRequest` / `ProfileRunRequest`; thin module entry points remain for compatibility | Rollback window ends and the remaining job-module compatibility entry points have no external callers |
| V1 feed/store beside canonical V2 state | `KEEP` during migration | Define which state is authoritative per phase and build replay/crosswalk evidence | V2 replay, shadow comparison, bounded cutover, and rollback acceptance are complete |
| Frozen or unknown database tables | `ARCHIVE` as data | Document ownership and stop new writes where appropriate | Never drop through ordinary cleanup; deletion needs a separate migration and owner authorization |
| Standalone fine-screen runtime | `MIGRATE` | Use as the behavior baseline and rollback path while reusable pieces move | Unified path passes parity, shadow, controlled publish, and rollback-window acceptance |
| Duplicate screening code inside the private CV workspace | `MIGRATE` | Compare contracts, tests, prompts, and operational behavior before choosing the canonical implementation | All required behavior exists in the unified path and no scheduler imports the duplicate |
| Divergent resume masters and evidence sources | `RECONCILE` | Produce a claim-by-claim and file-by-file human review; declare one authority | Owner approves the reconciliation manifest; no automatic deletion |
| Legacy artifact filenames and Notion title compatibility | `KEEP` temporarily | Record live consumers and add migration aliases only where required | Existing artifacts/pages have migrated and compatibility replay finds no consumer |
| Superseded or contradictory specs | `DELETE` after merge | Move current facts to canonical docs and close or remove obsolete plans | Links and public-contract tests pass; Git history remains the archive |
| Browser-assisted acquisition | `KEEP` as local/manual | Keep it outside the unattended VPS critical path unless separately proven | Not a deletion target in this program |
| Suspended provider adapters and E2E tests | `KEEP` unless retired explicitly | Distinguish unavailable credentials/vendor service from dead code | Product decision explicitly retires the capability and public contract is updated |

The ledger is intentionally conservative. A component is not dead merely
because it is disabled, has no current credential, or is not used by one
profile. Deletion requires evidence that it has no runtime, migration,
compatibility, or data-recovery role.

### Implementation status (2026-08-28)

- Phase 0 local baseline is recorded: the component registry exposes one
  channel, eight pipeline steps, two sinks, and seven acquisition sources.
  Offline verification reports 371 passing tests, clean Ruff lint, and clean
  Pyright. Ruff format has one pre-existing working-tree failure in
  `adapters/storage/notion_bindings.py`; it is not reformatted by this cleanup
  because that file contains unrelated active work.
- Phase 1 is in progress. Reference searches confirm that `run_daily.py`,
  `run_all_tracks.py`, the feed, and frozen V2 tables still have code or test
  callers. The three hidden `run` compatibility switches had no active local or
  VPS caller and have been removed with their forwarding code and tests.
- The first workflow boundary is implemented: search tracks declare
  `processing_mode = core|review|discovery`; feed schema 2 carries that policy;
  dry-run screening reads unpublished jobs; only `core` can tailor a resume;
  and fine-screen emits a versioned result handoff that Positions validates
  against current profile policy and stores atomically/idempotently in
  canonical workspace state before artifacts or Notion. The bounded
  `publish-screening` transition now verifies that exact durable state and uses
  the existing idempotent sink; fine-screen apply publishes only after all PDFs
  validate, then refreshes page IDs before attachments. Production scheduling
  remains on the compatibility path until a bounded live cutover succeeds.
- Documentation cleanup removed an abandoned, never-implemented market-analysis
  proposal and superseded specifications. Current feed compatibility, gap/track
  semantics, filename compatibility, Bright Data safety, CLI behavior, and the
  30-day `Not Interested` behavior remain in active specifications or canonical
  public documentation.
- VPS service definitions and recent status were inspected read-only. The
  active Positions unit runs the public `run` path and triggers the separate
  fine-screen unit on success; the latter was terminated by signal during a
  large batch rather than completing, which is deployment evidence to fix, not
  a successful cutover. No unbounded restart was attempted.
- A local seven-day discovery replay processed 659 rows with zero agent and
  tailoring calls, persisted 648 canonical results after source duplicates
  merged, and remained idempotent on repeat. A one-job bounded `core` replay
  made exactly one agent call and persisted its validated result without
  drafts or Notion writes; its repeat was a cache hit with zero model calls. A
  one-job bounded `review` replay made one screening call, zero tailoring calls,
  and persisted no resume plan. The workspace database was backed up before
  schema migration 7.
- The same revisions were deployed to the VPS (`Positions` 4c4894b,
  fine-screen 882748a, private workspace 590f313). After a verified backup and
  schema migration, a 411-row discovery shadow and one bounded core cache replay
  persisted successfully with no Notion writes. A transient systemd acceptance
  unit repeated the discovery path as the service user and exited 0. The old
  scheduled apply unit remains unchanged and its earlier signal failure is not
  reclassified as healthy; host-reboot proof and publication cutover remain
  open gates.
- A one-job VPS `core --apply` acceptance run used a cached rejection and
  proved the new order end to end: unpublished-capable feed, zero new model
  calls, atomic durable import, zero artifacts, bounded finalized publication,
  page-ID refresh, and preservation of unrelated Notion Screen state. A
  selected-job PDF/attachment cutover and host reboot remain separate gates.
- Core publication cutover is now configured with rollback copies: the private
  `ai` and `cpp` profiles override their acquisition sinks to CSV, so the
  existing daily fine-screen unit owns their first Notion publication through
  the finalized-result path. `review` and `discovery` profiles retain immediate
  Notion compatibility while their semantic backlog is shadowed.
- The first production cutover run completed acquisition successfully and
  triggered fine-screen through `OnSuccess`. Core C++ and AI wrote CSV only;
  compatibility tracks retained their existing Notion path. Fine-screen
  evaluated 119 core rows with bounded cache/model budgets and failed closed
  before publication when all six initial tailoring plans violated the strict
  editorial contract. That run exposed and fixed canonical duplicate-result
  verification (three source-alias groups mapped to one durable job) and made
  the tailoring label/size contract explicit.
- A bounded eight-job recovery then ran as the service user on the corrected
  releases. It reused six cached decisions, refined two, imported all eight
  results atomically, produced three validated tailored PDFs, published the
  exact eight-job slice through the finalized Notion transition, preserved
  unrelated Notion state, and exited 0. Windows sync copied six cumulative VPS
  PDFs without deleting local files. A subsequent run through the installed
  `fine-screen-daily.service` reused all 117 semantic decisions and three valid
  tailoring plans, generated one additional validated PDF, performed an
  idempotent 118-row finalized publication with zero Notion failures, and exited
  0. The final Windows sync saw seven cumulative PDFs. Both scheduled units are
  now inactive with a successful manager state; the acquisition unit description
  no longer says screening is paused. Host-reboot recovery remains the only
  unproven Phase 9 operational gate and is intentionally not inferred from
  service-level proof.

## Acceptance criteria

### Cleanup and authority

- [ ] A checked-in component ledger lists every runtime entry point, scheduler,
  datastore, external write, private-workspace dependency, and active design
  document with its owner, status, callers, replacement, and removal condition.
- [ ] Each deletion has a reference search, replacement or reason, recovery
  story, focused verification, and a reviewable commit; user databases and
  private artifacts are never included in bulk cleanup.
- [ ] Superseded docs are consolidated into their canonical homes and removed;
  no two active documents claim authority over the same workflow.
- [ ] One private evidence/CV authority is declared only after divergent
  masters, shared facts, and compatibility artifacts are reconciled by a human.
- [ ] Exactly one screening implementation and one application orchestration
  path remain runnable after the migration window.

### Workflow

- [ ] A canonical candidate passes acquisition, canonical merge, deterministic
  hard gates, client-specific track routing, and the configured processing mode
  before any external publication.
- [ ] A `core` track can run exactly one validated semantic screening decision
  and, when authorized, resume tailoring and artifact validation.
- [ ] `review` and `discovery` tracks can remain visible without creating a
  resume that overstates the client's relevant experience.
- [ ] Resume tailoring consumes a validated decision and cited evidence; it
  cannot issue a second independent fit decision or invent experience.
- [ ] Agent, evidence, or artifact failure fails closed into a durable review or
  error state and is visible to the display sink without being presented as a
  successful tailored application.
- [ ] Notion is updated from finalized durable state after upstream processing.
  Reconciliation is idempotent and preserves the existing uncertain-create
  safety policy.
- [ ] Commands support bounded job IDs/count assertions for costly or mutating
  runs. No workflow step submits an application.

### Deployment evolution

- [ ] One local clone plus one private workspace can run the complete
  single-client workflow without a sibling code checkout or hard-coded path.
- [ ] The single-client workflow passes offline replay, shadow comparison,
  controlled publication, and the repository quality gates before VPS cutover.
- [ ] VPS completion separately proves service configuration, authorization,
  bounded execution, backup/restore, and recovery after service and host
  restart.
- [ ] A future server/client split is based on versioned contracts proven by
  the single-client workflow, not on duplicated business logic.

## Vocabulary and policy

Use these terms consistently:

- **Client**: the owner of private job-search policy, evidence, credentials,
  artifacts, and processing state. Initially there may be one client; the
  design must not confuse a client with a search track.
- **Search track**: a group of acquisition rules and processing policy for one
  client, such as a main technical track or a broader discovery track.
- **Resume variant**: a human-curated document template selected only for a
  track/job supported by the evidence library.
- **Provider profile**: provider-specific runtime configuration. Do not use the
  bare word `profile` when client, search track, resume variant, or provider
  configuration is intended.

Each search track declares processing capability explicitly instead of
inferring it from its name:

| Mode | `agent_screen` | `resume_generation` | Intended use |
|---|---:|---:|---|
| `core` | yes | yes | Main career track with credible supporting evidence |
| `review` | yes | no | Adjacent roles needing semantic assessment but no automatic CV |
| `discovery` | no | no | Market visibility and manual exploration |

These are workflow policies, not claims that every client must use all three.
Hard safety and explicit user-decision gates apply to every mode.

## Target function flow

```text
acquisition
  -> normalize and canonical merge
  -> integrity, explicit user-decision, and hard-preference gates
  -> client and search-track routing
       -> core: agent screen -> tailoring -> artifact validation
       -> review: agent screen -> durable review state
       -> discovery: durable retained state
  -> finalize durable job/run/artifact state
  -> authorized display sinks, including Notion
```

Acquisition finds candidates; it does not make the final semantic fit decision.
Retrieval keywords, title terms, city-only locations, and soft language signals
may improve recall or supply context, but cannot become accidental final
rejections before the configured screening mode runs.

Agent screening owns semantic fit and routing for modes that enable it. Its
validated result contains a disposition, recommended track and resume variant,
cited evidence identifiers, honest gaps, confidence, and a tailoring brief.
There is at most one semantic result for a canonical job, client evidence
version, track policy version, and screening-contract version; cache hits reuse
that result.

Tailoring is a document-authoring function. It sees the selected editable
variant, cited evidence, canonical job, and decision-owned brief. It may adapt
emphasis and wording but cannot reclassify fit or create unsupported facts.
All expected PDFs are generated and validated before Notion reconciliation.

An honest gap list is audit evidence, not a numeric rejection rule, and track
membership is routing rather than a quota. A qualifying job is not discarded
because another job in the same track ranked higher. During artifact migration,
legacy generated filenames remain recognizable through a stable job-identity
suffix and comparison key; filename compatibility ends only after the artifact
ledger shows that no live consumer depends on it.

Notion is a replaceable display sink. It receives finalized status, decision
summary, gaps, and validated artifact references. If screening or document
generation fails, Notion may show that failure/review state, but it must not
receive a false success state or a missing attachment presented as complete.

Persist acquisition, gating, routing, screening, tailoring, validation, and
publication as distinct idempotent phases. External object creation retains
the no-retry-after-uncertain-create policy. Reconciliation must be able to
resume without repeating completed model calls or document generation.

## Boundaries for the current repository

Extend the existing architecture rather than creating a parallel subsystem:

- `domain/` owns canonical job values, track policy, decisions, evidence
  references, document plans, artifacts, and run states.
- `ports/` owns stable interfaces for sources, repositories, screening,
  private evidence access, artifact storage, and sinks.
- `application/` owns the end-to-end use case and phase transitions through
  Ports. It constructs no concrete adapter and does not assemble another CLI.
- `pipeline/` owns small deterministic gates and transformations, not network
  calls, documents, or external writes.
- `adapters/` translate provider, filesystem, database, model, PDF, and Notion
  behavior at the boundary.
- `registry/` remains the composition mechanism; no new dependency-injection
  framework is introduced.
- `cli/` exposes bounded operations over the same application use cases used
  by the scheduler and future workers.

Runtime search policy, evidence, credentials, private paths, databases,
generated artifacts, and destination IDs remain in ignored private workspaces.
The public repository contains only generic contracts, fictional fixtures, and
provider-neutral examples.

The evidence library is the factual authority. Editable resume variants are
human-approved narrative/layout templates. Generated PDFs and manual candidate
PDFs are outputs or reference material and never become evidence automatically.

## Project governance

This specification is the single active program document. It contains phase
gates and the cleanup ledger; do not create a parallel roadmap, backlog, or
`_v2` design. Everyday procedures stay in `operations.md`, configuration keys
in `configuration.md`, architecture contracts in `architecture.md`, and
extension instructions in `extension-guide.md`.

Implementation is divided into small branches or commits with one concern,
explicit acceptance checks, fresh evidence, and a rollback point. Cleanup and
new behavior do not share the same commit unless the behavior is the direct
replacement that makes removal safe. Never stage the repository wholesale in
artifact-heavy workspaces.

The ledger status meanings are:

- `KEEP`: active and authoritative.
- `MIGRATE`: still required until its stated replacement and cutover gate pass.
- `DELETE`: tracked, regenerable, or superseded material removed in the same
  change that establishes its replacement; Git history is the archive.
- `ARCHIVE`: untracked, irreplaceable private material moved only with owner
  authorization and an ignored manifest.
- `RECONCILE`: conflicting sources require human judgment before either can be
  authoritative or removed.

No feature implementation begins while its prerequisite authority or cleanup
gate is unresolved. A phase may expose new evidence and send an item back to
`RECONCILE`; schedule pressure is not a reason to guess.

## Delivery phases and gates

### Phase 0: Freeze and baseline

- Record Git state, deployed revisions, service entry points, schedules,
  commands, databases, external sinks, private workspaces, and rollback assets.
- Run current offline tests and bounded production diagnostics without changing
  external state.
- Freeze unrelated architecture work until the active paths are known.

**Gate:** every live entry point and external write has an owner and recovery
path; existing failures are recorded rather than silently attributed to later
changes.

### Phase 1: Complete the component ledger

- Trace imports, CLI registrations, scheduler/unit references, documentation,
  configuration, database writers/readers, and Git history for candidate dead
  surfaces.
- Compare the standalone and private-workspace screening implementations by
  behavior, contract, fixtures, and tests.
- Inventory all divergent CV masters, shared evidence, notes, assets, generated
  artifacts, and compatibility names without exposing private contents.

**Gate:** every candidate cleanup item has a disposition, evidence, replacement,
removal condition, and rollback plan. Unknown items are not deleted.

### Phase 2: Safe cleanup

- Delete regenerable repository artifacts and confirmed unreferenced code.
- Retire hidden CLI flags and duplicate wrappers only after callers migrate.
- Consolidate contradictory or superseded documents into their canonical homes
  and remove the obsolete files.
- Stop new writes to frozen compatibility surfaces where safe; preserve source
  data and read/replay capability.

**Gate:** public contracts, CLI help, focused tests, full quality gates, and
current scheduling still pass. The cleanup diff contains no private data or
new behavior hidden among deletions.

### Phase 3: Reconcile private CV and evidence authority

- Produce a private reconciliation manifest covering each active master,
  factual claim, evidence ID, profile note, shared asset, and compatibility
  filename.
- Resolve conflicts with explicit human approval; do not choose by timestamp or
  filename alone.
- Declare the canonical editable variants and evidence library and mark all
  other material as migration input, generated output, or authorized archive.

**Gate:** the owner approves the manifest and authority declaration. Source
workspaces remain recoverable; no automatic factual ingestion from PDFs occurs.

### Phase 4: Unify screening implementation

- Move reusable fine-screen behavior behind the existing domain, Port,
  application, adapter, and registry boundaries.
- Preserve provider-neutral model execution, strict schema validation, safe
  process invocation, evidence citations, deterministic cache versioning, and
  fail-closed review behavior.
- Keep one temporary compatibility entry point only where replay or rollback
  requires it; state its removal condition in the same change.

**Gate:** offline parity tests and redacted replay cover all retained behavior;
the standalone path remains available only as an explicit rollback mechanism.

### Phase 5: Converge orchestration and state

- Replace CLI-to-script argv assembly with one application use case shared by
  CLI and scheduler.
- Define authoritative state per phase and an explicit legacy-to-canonical
  identity/decision crosswalk; do not compare unrelated cache keys directly.
- Add idempotent, dry-run-first migration that never modifies or deletes source
  databases.

**Gate:** replay proves canonical identity, decisions, track routing, and
external-write bounds. Exactly one path owns new state writes.

### Phase 6: Finalize the implementation specification

- Convert the proven function boundaries into the smallest implementation
  slices, configuration changes, state transitions, test cases, and migration
  steps.
- Update `architecture.md`, `configuration.md`, `operations.md`, and the
  extension guide only where their existing contract changes.
- Run an independent user-path review of the proposed CLI/configuration flow
  before implementation changes that expose the new workflow.

**Gate:** no unresolved product term, authority boundary, deletion dependency,
or external-write ambiguity remains. Each slice has acceptance evidence and a
rollback point.

### Phase 7: Implement the track-aware workflow

- Add explicit `core`, `review`, and `discovery` processing policies.
- Run one agent decision only where enabled; route only `core` decisions into
  authorized resume generation.
- Validate all artifacts before finalizing state and update Notion last.
- Fail closed on invalid model output, stale evidence, missing documents,
  credential failure, or ambiguous external creation.

**Gate:** focused branch coverage for changed core behavior is at least 90%,
all offline gates pass, and fictional end-to-end fixtures prove every mode and
failure route.

### Phase 8: Shadow and controlled cutover

- Replay historical redacted jobs and compare semantic decisions through the
  crosswalk.
- Run old and new paths in shadow without duplicate external writes.
- Authorize a bounded list/count for the first real artifact and Notion run;
  inspect every resulting state and file.

**Gate:** acceptance thresholds are met, discrepancies are resolved, rollback
is tested, and the owner explicitly grants publication authority to the new
path.

### Phase 9: Single-client VPS proof

- Deploy the same verified application use case with private configuration,
  service-user permissions, durable state, backups, and secret handling.
- Prove bounded execution, service restart, host restart, scheduler recovery,
  artifact handling, and Notion reconciliation without secret logging.
- Retire old production scheduling only after the rollback window.

**Gate:** the single-client deployment is configured, live-verified,
restart-proven, and recoverable. Source workspaces and rollback snapshots remain
available for the agreed retention period.

### Phase 10: Future multi-client server/client split

This is a new program after Phase 9, not part of the immediate refactor.

- The future VPS/server project may own acquisition, canonical job storage,
  scheduling/workers, tenant orchestration, durable lifecycle state, and final
  external sinks.
- A future local client project may own private CV/evidence authoring,
  client-specific policy, local review, credential approval, and artifact
  download or export.
- Shared, versioned contracts must cover canonical jobs, track policy,
  screening requests/results, artifact manifests, synchronization, idempotency,
  authorization, and compatibility.
- Business decisions are implemented once behind those contracts. The split
  must not create server and client copies of screening or tailoring logic.

**Entry gate:** at least the single-client lifecycle and its operational costs
are proven. The multi-client threat model, isolation model, data ownership,
authentication, API, storage, and migration requirements receive their own
approved specification before repository separation begins.

## Verification plan

Cleanup verification includes reference searches, import and CLI inventories,
documentation-link checks, public-contract tests, and the standard Ruff,
Pyright, and Pytest gates. Deletion evidence records what replaced an item and
how it can be recovered from Git or an authorized private archive.

Workflow fixtures remain fictional and cover:

- a retrieval-keyword miss accepted by evidence;
- explicit non-target-country rejection;
- ambiguous-location pass-through;
- `core`, `review`, and `discovery` routing;
- unsupported experience remaining an honest gap;
- invalid agent output and stale evidence references;
- missing or invalid resume output;
- cache reuse and version invalidation;
- idempotent source-preserving migration;
- bounded, resumable Notion reconciliation after finalized state.

Live VPS verification is separately authorized. It proves deployment state,
service-user execution, configuration and credentials, bounded no-submission
behavior, backup/restore, and recovery after service and host restart. A source
checkout, configured service, successful live run, and restart proof are four
different completion states and must be reported separately.

## Follow-ups

After the single-client workflow is stable, write a separate approved spec for
the multi-client server/client product boundary. That follow-up chooses concrete
identity, tenant isolation, storage, secret management, synchronization, API,
and deployment mechanisms. This document deliberately defines the contract
seams without pretending those future infrastructure decisions are already
made.
