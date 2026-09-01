# Cleanup-first unified screening workflow

## Outcome

Establish one understandable and supportable job-search workflow and expose it
through one server-hosted Web portal plus one local browser connector. The Web
portal is the only user-facing configuration and results surface. The local
connector is an implementation detail that launches bounded non-interactive
Codex CLI runs against the Chrome profile the user explicitly connected.

The work proceeds in this order:

1. Freeze the current production boundary and inventory what is actually live.
2. Remove or migrate dead code, superseded features, duplicate implementations,
   and stale documentation using explicit removal conditions.
3. Reconcile the private CV/evidence sources and declare one factual authority.
4. Unify acquisition, hard gates, track routing, agent screening, resume
   tailoring, artifact validation, and publication into one workflow.
5. Prove that workflow for one client locally and on the VPS.
6. Implement the physical split as one server codebase and one local client
   codebase without adding a separate front-end repository.

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
  - Add passwordless email accounts, tenant-resolved sessions, a resumable Web
    cold start, profile/evidence/resume administration, results, and worker
    status to the existing FastAPI server boundary.
  - Present those capabilities through one simple, white, task-focused
    dashboard shell with stable navigation, compact tables, and a dedicated
    resume-and-evidence analysis workspace.
  - Keep tenant-private runtime data outside tracked source and select a tenant
    before request-body parsing; an email address is a verified login handle,
    never the durable tenant identity.
  - Replace the visible Codex App heartbeat with a user-session background
    `positions-client` agent that wakes through the server, launches
    `codex exec`, and lets the installed Chrome plugin operate the connected
    external Chrome profile.
  - Prove a single-client local and VPS workflow before enabling more than one
    tenant in production.
- Out of scope for this change:
  - A third front-end repository, SPA build chain, native desktop UI, billing,
    or public self-service tenant provisioning.
  - Depending on experimental Codex app-server or remote-control protocols.
  - Inspecting or transferring Chrome cookies, profile paths, browsing history,
    screenshots, HTML, passwords, or other browser identity material.
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

The former cold start based on hand-editing TOML, copying a private workspace,
running `positions-client enroll`, and configuring a Codex App heartbeat is
superseded by the Web onboarding state machine and the background client agent.
Compatibility CLI commands remain only until the Web path passes fictional
end-to-end, restart, rollback, and bounded live acceptance.

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

### Complete component ledger

This is the checked-in Phase 1 inventory. `Owner` means the component that owns
the contract, not a person or a private installation. Private paths below are
roles; concrete installation paths remain private configuration.

| Component | Kind | Owner | Status and callers | Replacement / removal condition |
|---|---|---|---|---|
| `job-scraper` | public CLI entry point | Positions `cli/` | `KEEP`; operators, scheduler, and fine-screen durable handoff | No replacement planned |
| `jobs/run_all_tracks.py` | orchestration compatibility entry point | Positions application boundary | `MIGRATE`; called by the CLI and module compatibility users, translates typed requests | Remove module `main` only after the rollback window and an external-caller audit; retain the typed use case |
| `jobs/run_daily.py` | one-profile process runner | Positions application boundary | `MIGRATE`; called through typed profile requests and compatibility module execution | Move remaining concrete construction behind existing ports, then remove module execution only after caller audit |
| `jobs/ingest_email_recommendations.py` | email-channel process runner | Positions channel/application boundary | `KEEP`; called by typed orchestration and explicit recovery operation | Remove standalone parsing only when all recovery callers use the typed request |
| `jobs/run_brightdata_notion_e2e.py` | credentialed live acceptance utility | Positions test operations | `KEEP suspended`; explicit operator only, never scheduled | Remove only if the Bright Data product capability is explicitly retired |
| `fine-screen` | semantic screening and tailoring CLI | fine-screen package | `KEEP authoritative`; systemd and bounded operators | Future client worker may wrap this contract; no duplicate implementation is allowed |
| `fine-screen-release` | release manifest CLI | fine-screen release boundary | `KEEP`; deployment workflow and VPS runner | Remove only when an equivalent signed/versioned release verifier replaces it |
| `positions-daily.timer` | acquisition scheduler | VPS installation | `KEEP`; systemd starts `positions-daily.service` | Replace only through an operational migration with rollback and restart proof |
| `positions-daily.service` | acquisition service | VPS installation | `KEEP`; timer/manual start, triggers fine-screen on success | Replace only when a future server worker proves the same request and recovery contract |
| `fine-screen-daily.service` | finalized core-processing service | VPS installation | `KEEP`; `OnSuccess` from acquisition/manual recovery | Replace only after the future client/server worker proves cache, artifact, and sink parity |
| `Fine-Screen PDF Sync` | Windows sign-in task | local client installation | `KEEP`; downloads validated VPS artifacts, never deletes local PDFs | Replace after a versioned artifact-download client is live and migration replay finds no task caller |
| per-profile V1 SQLite databases | acquisition/source state | Positions storage adapter | `KEEP authoritative for source rows during migration`; acquisition, feed rehydration, publication | Stop new writes only after canonical V2 supplies every read/write contract; never delete in ordinary cleanup |
| `workspace.db` V2 | canonical merge, policy, screening state | Positions storage adapter | `KEEP` authoritative for canonical identity and durable screening | Future server storage must migrate idempotently and prove exact crosswalk/rollback before replacement |
| CSV exports | cumulative operator export | Positions CSV sink | `KEEP`; configured profiles and manual consumers | Remove only after a consumer audit and explicit product decision |
| Notion database bindings JSON | external identity state | Positions Notion adapter | `KEEP`; status import and idempotent publication | Migrate atomically with any sink replacement; never rediscover/create after uncertain failure |
| agent decision/tailoring caches | expensive deterministic cache | fine-screen private workspace | `KEEP`; screening and tailoring CLI | Invalidate by contract/evidence versions; delete only as regenerable cache, never as factual authority |
| screening result JSON | versioned handoff/audit artifact | fine-screen producer, Positions validator | `KEEP`; import and bounded publication | Replace only with a backward-compatible versioned envelope and migration fixture |
| release manifest and runtime locks/logs | deployment/runtime state | fine-screen operations | `KEEP`; deploy verifier, failover runner, service manager | Rotate logs by operations policy; replace manifest only with stronger release identity proof |
| editable `resume/variants/*.tex` | human-approved narrative/layout masters | private CV workspace | `RECONCILE`; fine-screen tailoring inputs | Becomes authoritative only with the evidence reconciliation approval below |
| `shared/evidence-library.json` and `shared/profile-notes.md` | factual evidence | private CV workspace | `RECONCILE`; fine-screen validation inputs | Human owner approves the reconciliation manifest; generated PDFs never replace these sources |
| generated applications and `CV/Fine-Screened` PDFs | output artifacts | private CV workspace | `KEEP as outputs`; uploader/sync/manual review | Retain per private policy; never ingest automatically as evidence |
| Notion | final display sink | Positions sink boundary | `KEEP replaceable`; finalized publication and manual status input only | A future display may replace it after external-ID/status migration and idempotency proof |
| mailbox/IMAP | acquisition channel | Positions email adapter | `KEEP`; explicit configured channel | Remove only by product decision with provenance/state migration |
| source websites/APIs | acquisition adapters | Positions source ports | `KEEP per public contract`; configured profiles | Each adapter removal requires capability-contract change and compatibility evidence |

The `job-scraper` command groups are all owned by the single CLI entry point:
`init`, `doctor`, `list`, `capabilities`, `plan`, `feed`, `config`, `run`,
`ingest-email`, and `db`. The `db` group owns `init`, compatibility `migrate`,
`status`, `import-screening`, and `publish-screening`. Compatibility `db migrate`
remains callable but hidden while V1-to-V2 rollback is live; its removal
condition is the V1 retirement gate, not lack of help text. Read-only commands
write nothing. Mutating authority is limited to private config bootstrap,
configured acquisition/storage/export, mailbox checkpoint updates, atomic
screening import, and explicitly authorized finalized publication. Network
acquisition and Notion are adapters, never import-time effects.

Every active specification is also inventoried so historical rationale cannot
silently compete with this program document:

| Active specification(s) | Contract owner | Status / callers | Replacement / removal condition |
|---|---|---|---|
| `2026-08-28-first-class-agent-screening.md` | unified workflow | `KEEP authoritative`; implementation and completion audit | Superseded only by an explicitly approved successor that removes this file in the same change |
| `2026-08-27-downstream-screening-feed.md` | feed contract | `KEEP supporting`; Positions/fine-screen integration | Merge/remove only when feed compatibility ends and fixtures migrate |
| `2026-08-26-production-hardening-cleanup.md`, `2026-08-12-acquisition-reliability-hardening.md`, `2026-08-11-external-run-lifecycle-bounds.md` | reliability/history | `KEEP rationale`; tests and operations | Remove only after all still-open criteria are resolved or merged into canonical architecture/operations docs |
| `2026-08-26-notion-database-id-bindings.md`, `2026-08-26-notion-internal-connection-restoration.md`, `2026-08-28-orphaned-notion-status-mappings.md` | Notion identity/auth/status | `KEEP rationale`; Notion adapter and tests | Remove after behavior is fully represented in canonical docs and no open migration remains |
| `2026-08-27-not-interested-history-filter.md` | manual-decision history | `KEEP active`; pipeline/status import | Remove only with an explicit policy replacement and state migration |
| `2026-08-26-brightdata-suspension.md` | paid-source circuit breaker | `KEEP active`; source registry/config | Remove only if provider capability is retired or safely re-enabled under a successor spec |
| `2026-08-27-browser-indeed-acquisition.md`, `2026-08-27-browser-indeed-search-discovery.md` | browser-assisted local acquisition | `KEEP active/manual`; local operator and tests | Remove only after a separately proven unattended replacement or explicit retirement |
| `2026-08-27-employer-direct-source-coverage.md`, `2026-08-27-token-free-board-sources.md`, `2026-08-27-public-employment-agency-source.md` | direct-source coverage | `KEEP active`; registry, profiles, tests | Remove per adapter only when the public capability contract changes |
| `2026-08-28-de-nontech-track-goes-live.md`, `2026-08-27-deployment-configuration-guidance.md`, `2026-08-27-deployment-documentation-restructure.md` | track/deployment documentation rationale | `KEEP supporting`; profile/deployment docs | Remove after rationale has no open migration and canonical docs contain all current behavior |
| `2026-08-27-description-language-policy-defect.md`, `2026-08-06-engineering-role-targeting.md` | filtering policy rationale | `KEEP supporting`; pipeline tests | Remove only with an explicit policy successor |
| `2026-08-03-platform-links-only.md`, `2026-08-06-efinancial-email-metadata.md`, `2026-08-06-efinancial-neighbor-country-scope.md`, `2026-08-06-linkedin-email-card-metadata.md` | source identity/metadata rationale | `KEEP supporting`; adapters and storage tests | Remove after canonical docs and compatibility migrations fully absorb the contracts |
| `2026-07-25-agent-development-workflow.md`, `2026-07-25-vibe-project-bootstrap.md` | repository development governance | `KEEP historical rationale`; AGENTS/workflow docs | Remove after confirming no unique rationale or active migration remains |

The Phase 1 deletion record is recoverable from Git commit `4c4894b`:

| Deleted specification | Reference/replacement evidence | Verification and recovery |
|---|---|---|
| `2026-07-30-fine-role-skill-analysis.md` | Implemented screening/tailoring contract is owned here and in fine-screen; no runtime referenced the proposal | Repository/spec reference search and full offline gates passed; recover from Git |
| `2026-08-04-brightdata-snapshot-recovery.md` | Current provider safety is owned by acquisition reliability, Bright Data suspension, operations, and tests | Capability and reference search retained the live adapter; recover from Git |
| `2026-08-05-recent-not-interested-suppression.md` | Current 30-day manual-decision behavior is owned by `2026-08-27-not-interested-history-filter.md` and canonical operations/configuration docs | Status/filter tests passed; no data or Notion objects were deleted; recover from Git |
| `2026-08-24-downstream-agent-screening-orchestration.md` | Superseded and merged into this authoritative workflow plus the feed contract | Link/reference search and workflow tests passed; recover from Git |
| `2026-08-27-deferred-cli-consolidation.md` | Typed CLI/application request convergence and remaining removal condition are recorded here | CLI orchestration tests and full gates passed; recover from Git |
| `2026-08-27-repository-accumulation-controls.md` | Repository law moved to `AGENTS.md`; program-specific cleanup policy remains here | Documentation inventory/reference search passed; recover from Git |

The private `cv-cover-workspace` is the declared technical authority for editable
variants, shared evidence, profile notes, and quick-learn policy. Revision
`946828a` records that authority and the precedence rules inside the private
workspace. The former `CV_Cover` workspace no longer contains a runnable
screener; its generated/manual PDFs are outputs or reference material, not
evidence. File comparison found the editable/shared factual material equivalent
apart from line endings at the reconciliation point. This does not authorize an
agent to resolve a future factual conflict or delete private material: divergent
claims still require the human owner, and irreversible cleanup remains outside
this program.

The owner-approved mother-master criterion is now satisfied. The legacy
`CV/Mother-Resume-Candidates/README.md` maps the reference PDFs to the seven
editable masters under `resume/variants/`. Direct normalized-text comparison
shows all seven corresponding `.tex` files in `cv-cover-workspace` are identical
to those masters. Shared style, profile notes, and the evidence library are also
identical after newline normalization. This establishes `cv-cover-workspace` as
the private factual/editable authority; the mother PDFs remain comparison
references, not ingestible evidence.

### Implementation status (2026-08-28)

- Phase 0 local baseline is recorded: the component registry exposes one
  channel, eight pipeline steps, two sinks, and seven acquisition sources.
  The completed local verification reports 377 passing tests and clean Ruff
  format/lint and Pyright gates while preserving unrelated working-tree edits.
- Phase 1 cleanup is complete for the approved scope. Reference searches confirm that `run_daily.py`,
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
  now uses this finalized-state path for `core`; `review` and `discovery` keep
  their intentionally visible, non-tailoring compatibility publication.
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
- The release at fine-screen `daca749` makes finalized display state explicit:
  each bounded result receives `Fine-screened`, `Fine-screen rejected`,
  `Fine-screen blocked`, or `Fine-screen error`; only a selected result with a
  validated artifact receives the successful tag. Offline verification passed
  92 tests with one environment-dependent TeX test skipped, plus clean Ruff and
  Pyright gates. The standard VPS service verified the release manifest, reused
  all 117 semantic decisions, replayed the 119-job finalized window, published
  118 bound pages with explicit terminal status and zero Notion failures, and
  exited 0. Reconciliation also cleared one stale display tag. Windows sync then
  copied eight cumulative PDFs without deleting local files.
- Backup recovery was exercised without touching production state. The
  pre-migration backup copied to a temporary restore location with matching
  SHA-256, passed SQLite `integrity_check`, and exposed the expected ten tables
  and five historical migrations. A separate online post-cutover backup at
  `/srv/positions/data/backups/workspace-post-cutover-20260828T153442Z.db`
  passed `integrity_check` and contains 528 durable screening results.
- Whole-host reboot was not performed. The VPS also runs unrelated production
  and user services, so rebooting it is a materially broader external action.
  Service-level stop/start, service-user authorization, release verification,
  scheduled chaining, and failure recovery are proven; host-reboot recovery
  remains a maintenance-window acceptance item.
- Fine-screen `7569518` removes the last sibling-checkout assumption from its
  public CLI. It defaults to a standalone `job-scraper` argv with no working
  directory; checkout-backed installations opt into an explicit JSON argv and
  root. Fine-screen also stopped parsing the Positions `.env`: authorized
  Notion credentials arrive through the process environment. Offline gates
  passed 94 tests with one environment-dependent TeX test skipped, plus clean
  Ruff and Pyright. A bounded VPS dry run then omitted `--positions-root`, used
  the installed job-scraper executable with its private config directory,
  resolved exactly one core job from cache, made zero model/external writes,
  and exited 0. The checkout-backed service runner was separately replayed as
  its service user, imported one bounded cached result, and exited 0.
- After explicit maintenance authorization, the VPS completed a confirmed
  down/up reboot cycle. It returned with `systemctl is-system-running=running`,
  no failed units, the Positions timer enabled and active with its next run
  scheduled, both workflow services in successful inactive state, user linger
  restored, and all three repository revisions unchanged. Release-manifest
  verification passed after boot. A one-job service-user recovery replay then
  reused its cached decision, imported exactly one durable result, performed
  zero model and external-display writes, and exited 0.
- Indeed/browser acquisition was intentionally not folded into the unattended
  VPS critical path. The registered `indeed_brightdata` adapter remains a normal
  acquisition source. Browser-visible Indeed discovery is a local, explicitly
  authorized queue that must feed the existing normalization, canonical merge,
  track routing, and screening flow; it is not a second pipeline. It remains
  unregistered until a bounded live browser validation proves visible-card
  stability, block handling, and resumability without bypassing access controls.

## Acceptance criteria

### Cleanup and authority

- [x] A checked-in component ledger lists every runtime entry point, scheduler,
  datastore, external write, private-workspace dependency, and active design
  document with its owner, status, callers, replacement, and removal condition.
- [x] Each deletion has a reference search, replacement or reason, recovery
  story, focused verification, and a reviewable commit; user databases and
  private artifacts are never included in bulk cleanup.
- [x] Superseded docs are consolidated into their canonical homes and removed;
  no two active documents claim authority over the same workflow.
- [x] One private evidence/CV authority is declared only after divergent
  masters, shared facts, and compatibility artifacts are reconciled by a human.
- [x] Exactly one screening implementation and one application orchestration
  path remain runnable after the migration window.

### Workflow

- [x] A canonical candidate passes acquisition, canonical merge, deterministic
  hard gates, client-specific track routing, and the configured processing mode
  before any external publication.
- [x] A `core` track can run exactly one validated semantic screening decision
  and, when authorized, resume tailoring and artifact validation.
- [x] `review` and `discovery` tracks can remain visible without creating a
  resume that overstates the client's relevant experience.
- [x] Resume tailoring consumes a validated decision and cited evidence; it
  cannot issue a second independent fit decision or invent experience.
- [x] Agent, evidence, or artifact failure fails closed into a durable review or
  error state and is visible to the display sink without being presented as a
  successful tailored application.
- [x] Notion is updated from finalized durable state after upstream processing.
  Reconciliation is idempotent and preserves the existing uncertain-create
  safety policy.
- [x] Commands support bounded job IDs/count assertions for costly or mutating
  runs. No workflow step submits an application.

### Deployment evolution

- [x] One local clone plus one private workspace can run the complete
  single-client workflow without a sibling code checkout or hard-coded path.
- [x] The single-client workflow passes offline replay, shadow comparison,
  controlled publication, and the repository quality gates before VPS cutover.
- [x] VPS completion separately proves service configuration, authorization,
  bounded execution, backup/restore, and recovery after service and host
  restart.
- [x] The server/client split is based on versioned contracts proven by the
  single-client workflow, not on duplicated business logic.

### Web cold start and CLI browser agent

- [ ] A fictional new user can verify an email, upload an allowlisted resume,
  approve extracted evidence, approve one or more track policies, enroll a
  device, run a no-write calibration, and reach `active` without editing TOML,
  environment files, or a Git workspace.
- [ ] Every onboarding transition is durable, idempotent, resumable, and
  version-aware; changing approved evidence or policy invalidates dependent
  resume/calibration state.
- [ ] Server-rendered pages expose onboarding, jobs, profiles, resumes,
  integrations, browser devices/tasks, runs, account settings, and privacy
  controls with no separate front-end build.
- [ ] Authenticated pages use one route-aware dashboard shell with five primary
  destinations (`Overview`, `Jobs`, `Search directions`, `CV & evidence`, and
  `Activity`) plus bottom-anchored `Connections & settings`; onboarding appears
  as a resumable setup action only while the tenant is not `active`.
- [ ] The resume-and-evidence page uses a two-column analysis layout at desktop
  widths, never invents an ATS or fit score, and renders persisted provenance,
  evidence, analysis, track, and artifact state before an approval action.
- [ ] The dashboard remains fully usable at 1440, 1024, and 390 CSS-pixel
  viewport widths with keyboard navigation, visible focus, semantic tables and
  forms, text equivalents for status color, and no externally hosted visual
  dependency.
- [ ] Email is a verified login handle while opaque user, tenant, session,
  device, request, and artifact identifiers enforce authorization. Session
  cookies are server-side, `HttpOnly`, `Secure` in production, `SameSite=Lax`,
  rotated after authentication, and protected by CSRF on mutations.
- [ ] Resume uploads validate size, extension, signature, and content hash,
  store random filenames outside the Web root, and never become approved
  evidence without an explicit user action.
- [ ] `positions-client agent` runs in the interactive user session, waits for
  work without model calls, launches at most one bounded `codex exec` worker,
  and preserves existing claim/heartbeat/result idempotency and CAPTCHA/login
  terminal behavior.
- [ ] A closed-Desktop test and a Windows sign-in/restart test prove whether
  the installed Desktop runtime is packaging-only. Failure keeps that runtime
  as a documented prerequisite; it does not restore a visible App heartbeat.
- [ ] Fictional cross-tenant tests prove that a Web session, device credential,
  task, document path, artifact, and integration reference from one tenant
  cannot select or read another tenant.
- [ ] The VPS runs only the Positions server checkout and a tenant-private
  runtime. The local machine runs only the positions-client distribution plus
  the supported Codex/Chrome runtime; fine-screen and private CV repositories
  are no longer runtime dependencies after cutover.

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

### Phase 10: Web server/local client split

This is now an approved implementation phase. The browser-task slice has an
approved concrete contract in
`2026-08-27-browser-indeed-search-discovery.md`; this specification owns the
remaining identity, onboarding, profile, evidence, screening, artifact, and
administration decisions so no second general server/client design is added.

- The VPS/server codebase owns the server-rendered Web portal, account and
  tenant resolution, acquisition, canonical job storage, profile/evidence
  versions, screening/tailoring orchestration, scheduling/workers, durable
  lifecycle state, artifacts, and final external sinks.
- The local client codebase owns enrollment credentials, a user-session
  background agent, durable local browser-task journaling, bounded `codex exec`
  invocation, and browser-result transport. It does not own a second copy of
  profile, screening, tailoring, or artifact business logic.
- The former `fine-screen` Python package is bundled into the Positions
  distribution during cutover. Its compatibility commands, validated Agent
  contract, tailoring, PDF, and release behavior remain available without a
  separate runtime checkout. The private CV workspace is data, not a codebase,
  and stays outside the public repository.
- Shared, versioned contracts must cover canonical jobs, track policy,
  screening requests/results, artifact manifests, synchronization, idempotency,
  authorization, and compatibility.
- Business decisions are implemented once behind those contracts. The split
  must not create server and client copies of screening or tailoring logic.
- Only code, schemas, and fictional conformance fixtures are shared between
  clients. Profiles, source inputs, raw/canonical jobs, task queues, databases,
  model caches, CV/evidence, artifacts, credentials, logs, backups, and sinks
  are isolated per client instance; there is no shared job catalog or
  cross-client cache.

**Entry gate:** at least the single-client lifecycle and its operational costs
are proven. Each physical slice receives an approved threat model, isolation
model, data ownership, authentication, API, storage, and migration contract
before repository separation begins. The browser slice satisfies that design
gate through the linked browser spec; this is not evidence that its code or
deployment is complete.

#### Versioned contract seams

Physical repository separation outside the browser worker is deferred, but
these logical contracts are fixed enough to prevent duplicated business logic.
Every durable envelope carries `schema_version`, `contract_version`,
`request_id`, `created_at`, and an idempotency key. Business-workflow envelopes
also carry the server-assigned `client_id`. Browser HTTP bodies deliberately do
not: reverse-proxy routing selects one isolated tenant process before parsing,
so accepting a caller-supplied client selector would weaken the boundary.
Unknown major versions fail closed; unknown additive minor fields are ignored
and preserved when relayed. A client is never inferred from a track,
filesystem path, Notion page, provider profile, or Chrome profile.

| Contract | Producer -> consumer | Required identity and payload | Authority / retry rule |
|---|---|---|---|
| `AcquisitionRequest` | client policy -> server worker | client, enabled track-policy versions, source IDs, time/window bounds, cost limits | Server may retry idempotent reads; request key prevents duplicate runs |
| `CanonicalJob` | server acquisition -> durable store/client | canonical job ID, immutable source references, normalized facts, provenance, first/last seen | Server owns canonical identity; raw provider data is untrusted and retained at the adapter boundary |
| `TrackPolicy` | client -> server routing | client-scoped track ID, `core|review|discovery`, hard gates, policy version | Client owns policy; server validates but does not invent personal preferences |
| `ScreeningRequest` | server orchestrator -> client/private worker | canonical job snapshot/hash, track-policy version, screening contract, evidence version, cost deadline | Exactly one semantic decision per complete version tuple; stale inputs fail closed |
| `ScreeningResult` | client/private worker -> server | disposition/status, score, variant, cited evidence IDs, honest gaps, rationale, source/cache identity | Client worker owns evidence-grounded semantics; server validates schema/version and stores atomically |
| `TailoringRequest` | server orchestrator -> client/private worker | validated decision ID/hash, editable variant ID/version, cited evidence subset, artifact constraints | May be emitted only for `core`; cannot make a second fit decision |
| `ArtifactManifest` | client/private worker -> server/artifact store | artifact ID, canonical job/client IDs, decision/tailoring hashes, media type, size, content hash, validation status | Bytes are accepted only after hash/PDF validation; manifest commit is idempotent and precedes display publication |
| `PublicationRequest` | finalized state -> sink worker | exact canonical-job set/count, final statuses, external binding IDs, artifact manifests | No publication before durable verification; uncertain creates are not replayed automatically |
| `SyncCheckpoint` | server <-> local client | client-scoped monotonic cursor, acknowledged contract versions and artifact hashes | At-least-once transport with idempotent apply; checkpoint advances only after durable acknowledgement |
| `BrowserTask` | client server instance -> local client worker | task/lease IDs, `search|detail`, exact allowed URL and minimal provenance, contract version, expiry | Local worker pulls one task; an expired lease returns to the same client's queue; Chrome identity never leaves the client |
| `BrowserResult` | local client worker -> client server instance | task/lease IDs, idempotency key, `complete|blocked|unavailable`, validated visible fields, observed time | Server accepts only the worker credential bound to that client and the current lease; blocked access is terminal, never bypassed |
| `WorkerEnrollment` | client server instance -> local client | one-time token, server endpoint, client-bound device credential, allowed task types | Enrollment token is single use; the resulting credential cannot name or access another client |

The multi-client server runs one isolated runtime per client: separate configuration,
database, browser queue, agent cache, CV/evidence workspace, artifact store,
credentials, Notion bindings, logs, backups, locks, service, and timer. A
process mounts only one client's runtime. Authentication resolves the client
instance before request-body parsing; a caller-supplied `client_id` can never
select another instance. Search policy, raw jobs, evidence, credentials, resume
masters, generated artifacts, and destination state are never shared. Logs and
metrics contain opaque task/run IDs and remain inside that instance.

#### Client-owned Chrome worker

Browser work is asynchronous because a client computer may be offline or
behind NAT. The VPS stores tasks durably. A user-session `positions-client
agent` maintains a bounded wait against its enrolled server and launches one
ephemeral `codex exec` run only when work is available. That run loads the
installed Positions worker skill, claims leased work through `positions-client`,
drives the Chrome extension surface selected by the user, heartbeats while it
is running, and submits a versioned result. One local lock prevents overlapping
agent or Codex runs.

The standalone CLI path is supported by fresh local evidence: a non-interactive
`codex exec` selected browser type `extension`, opened and read a neutral page
in the connected external Chrome, and closed only its task tab. The same
extension instance was selected on a second bounded run. A security-check page
kept the site-login result unknown and was not bypassed. Closing the Desktop UI
and rebooting Windows remain explicit deployment acceptance gates; until they
pass, the installed Codex Desktop runtime may remain a packaging dependency,
but its visible task/heartbeat is not part of the product workflow.

The server never receives Chrome profile paths, cookies, credentials, HTML,
screenshots, browsing history, or extension identity. Each enrolled worker is
bound to one tenant and device credential. The selected external browser is the
Chrome profile in which the user installed and connected the extension; the
worker never enumerates other profiles.

An offline client leaves tasks `pending`. A lost worker lease expires back to
`pending`; duplicate result delivery is idempotent. Login walls, CAPTCHA,
access blocks, and unavailable pages produce `blocked` or `unavailable` results
and are not retry-escalated or bypassed. A continuously available browser path
requires a client-owned always-on machine with that user's connected Chrome
surface available, not a shared browser on the main VPS.

Indeed search creates browser `search` tasks from that client's own track/query/
location configuration. Valid visible cards expand into one browser `detail`
task per posting. The email channel's Indeed browser-detail branch reads only
authorized Indeed mail, extracts minimal card/message provenance, and creates a
browser `detail` task for every Indeed card because email content is incomplete.
Other supported recommendation mail keeps its existing non-browser path.
Neither Indeed search cards nor Indeed email cards become jobs. Only a validated
detail result enters that client's existing normalization, canonical merge,
track routing, screening, artifact, and Notion flow.

#### Cold start

An administrator provisions an empty isolated server runtime. The user verifies
an email login and receives an opaque user/tenant identity through a server-side
session. The server-rendered Web portal then advances one resumable onboarding
run through `account_verified -> resume_uploaded -> evidence_approved ->
tracks_approved -> search_preferences_approved -> integrations_configured ->
connector_enrolled -> browser_calibrated -> pipeline_calibrated -> active`. A
failed or stale step never advances the state; changing approved evidence or
track policy invalidates dependent calibration and pauses activation.

Cold start is conversational rather than a raw configuration form. After a
resume upload the server starts resume analysis in the background and the page
asks one plain-language question at a time. Durable answers cover desired work,
location and remote constraints, working languages, and employment constraints.
When analysis and the initial answers are both ready, the Agent combines them
into proposed tracks, representative retrieval keywords, and normalized search
preferences. The user may refine that proposal with further natural-language
messages before one explicit approval. Raw TOML, JSON, comma-separated component
IDs, and internal profile fields are not part of the new-user path.

The portal accepts a bounded allowlisted resume upload, stores it outside the
Web root under the resolved tenant, records its hash and provenance, and
presents proposed evidence for explicit approval. Only approved evidence may
produce track policies or resume variants. The portal gathers locations,
languages, role families, queries, per-track `core|review|discovery` policy,
source choices, budgets, schedules, and optional integrations. Account email
and an IMAP acquisition mailbox are separate concepts.

The browser step creates a single-use enrollment token. `positions-client
enroll` binds one device credential, and the background agent connects the
user's chosen Chrome extension profile. A combined transport/browser doctor and
bounded calibration starts with no external writes. One explicitly authorized
browser search and one optional Indeed-email detail prove the configured
browser boundary. The remaining activation gate is derived from approved track
modes and enabled sinks: every tenant proves durable screening/routing and
portal display; a tenant with a `core` track validates one core artifact; a
`review|discovery`-only tenant instead proves that no resume artifact is
generated; and an external publication is required only for each sink the user
actually enabled. A tenant with no optional external sink uses the authenticated
portal as its final display surface and does not fabricate a Notion or other
publication. Only after the applicable gates pass may tenant timers and the
local background agent process scheduled work.

The initial portal uses semantic HTML, a white task-focused dashboard, compact
tables, forms, and restrained buttons rendered by the existing FastAPI server.
It has no SPA, Node build, WebSocket requirement, or separate front-end
repository. JSON API contracts remain versioned so another UI can be added
later without moving the business authority.

#### Dashboard interface contract

This subsection is the approved target for the first styled portal. It records
a design decision, not deployment evidence: until the acceptance checks below
pass, the current basic HTML remains the implemented state. The visual reference
is the general layout of a white resume-analysis application: persistent app
navigation, a compact top bar, a list/work area, and a separate analysis area.
No third-party brand, copy, image, icon, source code, or distinctive ornamental
element is copied. Product names, metrics, states, and actions come only from
Positions contracts and durable state.

The styled portal replaces the current unstructured navigation line, raw
snapshot `<pre>`, loose lists, and browser-default controls. It does not replace
the routes, authorization, CSRF checks, onboarding state machine, upload
validation, or tenant boundaries that already exist.

##### 2026-08-30 approved redesign amendment

This amendment supersedes the earlier deep-green visual tokens, permissive
pre-onboarding route behavior, summary-only Jobs table, and multi-question
onboarding presentation later in this subsection. The rest of the privacy,
state, read-model, security, and deployment contract remains authoritative.
It is a planning decision only until implementation and deployment evidence is
recorded separately.

The visual reference supplied for this amendment is used only for general
product language: a cold neutral canvas, dark navy controls, sparse blue data
accents, small line icons, flat white panels, and a high-density B2B dashboard.
Positions does not copy the reference brand, copy, logo, illustrations, chart
data, exact component geometry, or code. There are no gradients, glass panels,
purple-blue template treatments, or green primary controls.

The replacement palette is:

| Token | Value | Use |
|---|---|---|
| `--canvas` | `#f3f6f8` | Cold blue-grey application background |
| `--surface` | `#ffffff` | Sidebar, cards, tables, and forms |
| `--surface-subtle` | `#eef3f6` | Selected navigation and grouped controls |
| `--text` | `#102531` | Primary navy text and icons |
| `--text-muted` | `#6f7d85` | Helper text, metadata, and inactive icons |
| `--border` | `#dce4e8` | Panel, input, table, and divider borders |
| `--accent` | `#287fbd` | Primary action, selected state, links, focus |
| `--accent-hover` | `#1e669a` | Hover and active action state |
| `--accent-soft` | `#e8f2f9` | Selected rows and icon discs |
| `--ink-action` | `#062536` | High-emphasis CTA and table-header action |
| `--coral` / `--coral-soft` | `#f15a57` / `#fff0ef` | Rejected, blocked, destructive, or Drop data |
| `--teal` / `--teal-soft` | `#24a8a3` / `#e9f8f6` | Completed, healthy, or accepted data |
| `--warning` / `--warning-soft` | `#a36a00` / `#fff6dc` | Waiting and attention states |

Coral, teal, and blue encode persisted categories only and always have visible
text or an accessible name. The default primary action is navy or blue, never
green. Icons are local `18x18` or `20x20` inline SVG using `currentColor`.
Descriptive statistic tiles use a meaningful icon, the numeric value, and a
short accessible label such as `Seen`, `Accept`, or `Drop`; they do not contain
sentence-length explanations. Icon-only controls require `aria-label` and a
visible tooltip/focus description. No external icon library or asset request is
introduced.

###### Mandatory onboarding gate

After passwordless verification, every browser HTML route authenticated by the
portal session except `/onboarding`, logout, and the exact upload/answer/
finalize endpoints evaluates one shared `dashboard_access_granted` predicate.
If false, GET requests return `303 /onboarding`; portal mutations outside
onboarding fail closed. The original destination is not accepted as an
arbitrary redirect target. `/login`, `/auth/verify`, `/healthz`, and
`/v1/browser/*` retain their own public, token, or device-bearer contracts. The
HTML gate is never whole-app middleware and an API request never receives an
onboarding HTML redirect. A user cannot reach or infer Dashboard, Jobs,
Activity, CV outputs, or settings data before the gate passes.

`dashboard_access_granted` is durable account-onboarding completion, not the
same claim as system `active`. It becomes true only when all of these are true:

1. the account email is verified;
2. a genuine resume is uploaded and analysis is `ready`;
3. every required natural-language question has a non-empty durable answer;
4. the complete Agent proposal, search scope, sources, track modes, and evidence
   proposed for approval have been displayed;
5. the user explicitly confirms the displayed proposal and evidence.

Connector enrollment, browser calibration, pipeline calibration, optional
sinks, and schedules remain independent post-onboarding statuses under
`Connections & settings`. They may prevent `active`, but they do not trap a
user outside the Dashboard after their account profile is complete. Replacing
the authoritative resume or invalidating approved evidence revokes Dashboard
access and returns the user to the exact onboarding review step.

The review and confirmation are version-bound. The review carries an opaque
server-issued tuple over the current resume document, answer set, proposal, and
evidence versions. Finalize atomically compares that exact tuple before writing
approvals and granting access; a mismatch returns to a freshly rendered review.
Editing any answer invalidates the proposal, review, final confirmation, and
gate. Replacing the resume additionally invalidates analysis-derived evidence.
Refinement creates a new proposal version and invalidates only the prior review
and confirmation. Historical artifacts remain outputs but cannot authorize the
new gate state.

`/onboarding` is a dedicated full-page flow without the Dashboard sidebar. It
uses a narrow centered work area, one flat card, and one primary action. The
header contains the Positions product mark, `Step n of N`, a text step name,
and a semantic progress bar. Only one required question or approval decision is
shown per response. Back is permitted after the upload step and preserves
answers; Continue validates and persists only the current step through
Post/Redirect/Get. Refreshing or signing in on another device resumes the first
incomplete step from durable state.

The ordered screen sequence is:

1. Welcome and privacy boundary.
2. Resume upload and extraction status.
3. Desired role/direction question.
4. Location, remote, hybrid, and relocation question.
5. Working-language question.
6. Employment type, mandatory conditions, and exclusions question.
7. Analysis waiting or recoverable failure screen when necessary.
8. Agent proposal review, including every value that approval will persist.
9. Optional one-message refinement followed by the revised review screen.
10. Explicit final confirmation and transition to the Dashboard.

The question screen uses a plain-language prompt, optional short helper copy,
one textarea or purpose-specific input, Back, and Continue. It never shows raw
JSON, TOML, source IDs, comma-separated internal fields, or multiple unrelated
forms. Progress is based on durable completed screens rather than time or
analysis guesses. Waiting/failure and optional refinement are conditional
states inside the current numbered step, not extra steps that change `N` or
make progress move backward. Pending analysis never silently advances approval.

###### Full Jobs data table

`Jobs` is a tenant-scoped, server-side table over the complete canonical job
projection, not the newest CSV and not an arbitrary 200-row slice. “All” means
all jobs authorized for the tenant and retained by current data policy. Default
ordering is `first_seen_at desc, opaque_job_id desc`; pagination is stable and
does not load all rows into browser memory.

The table columns are job title, company, location, search
direction, source, validated score, decision, first seen, artifact state, and a
row-detail action. Missing values render as an em dash. Long values truncate
visually but remain available in the accessible name and detail page. The
header is sticky inside the scroll region; density is approximately `48-52px`
per row. Mobile retains semantic table markup and horizontal scrolling rather
than silently discarding columns.

One GET form owns filtering and sorting so its state is represented in the URL,
bookmarkable, and preserved across pagination and detail navigation. It offers:

- free-text search across title, company, normalized location, and canonical
  description text through the read model;
- multi-select decision (`Accept`, `Review`, `Drop`, failed/unknown), search
  direction, source, country/location, processing mode, artifact state, and
  publication state;
- first-seen date range, optional validated-score range, and a `has CV` filter;
- a whitelist of sortable columns: title, company, location, track, source,
  validated score, decision, and first-seen time;
- ascending/descending sort via accessible header buttons, a clear-all action,
  active-filter chips, result count, and page sizes `25`, `50`, or `100`;
- opaque cursor pagination carrying the deterministic sort tuple. The cursor is
  integrity-protected and bound to tenant, filter/query hash, sort key and
  direction, page size, and schema version. Seek pagination defines next and
  previous behavior and uses the opaque job ID tie-breaker to avoid duplicates
  as newer rows arrive. Arbitrary SQL expressions, raw column names, unbounded
  offsets, and client-supplied tenant identity are rejected.

Filter values are parsed into a typed `JobTableQuery`; unknown fields, invalid
dates/scores, unsupported sort keys, excessive list lengths, and malformed
cursors return a bounded validation error without querying. The
`PortalReadModel.jobs(query)` method applies tenant scope before every filter
and returns rows plus an exact total when the indexed query remains within its
bounded budget; otherwise the count is explicitly labelled capped and the
current result window remains exact. Sorting is deterministic,
case-normalized where appropriate, and defines null placement. A reset link
returns to the canonical `/jobs` URL. No table action applies for a job.

Implementation extends the existing `portal.py`, `portal_store.py`, canonical
job/run store, and `PortalReadModel` plan in this specification. It does not add
a front-end framework, a parallel job database, or a second onboarding state
machine. Minimal progressive enhancement may be considered later, but the
complete filter, sort, pagination, back/resume, and gate behavior must work
with server-rendered HTML and ordinary forms first.

###### Redesign acceptance and rollout

Offline tests use fictional tenants and cover palette/icon markers, every
onboarding screen and resume point, direct-route redirects before the gate,
gate revocation after evidence invalidation, no redirect loops, proposal values
shown before approval, all Job filters and sort directions, null ordering,
stable cursor pagination without duplicates, malformed-query rejection,
cursor tamper/cross-filter/cross-tenant rejection, stale-finalize compare-and-
set, Back/edit invalidation, device APIs remaining outside the HTML gate,
tenant isolation, escaping, CSRF, and manual-application language. Statistic
icons that repeat visible labels are `aria-hidden="true"`; accessibility tests
assert one readable label/value announcement. Query-plan
tests prove indexed bounded retrieval for the default and high-use filters.

Manual review uses `1440x900`, `1024x768`, and `390x844`, keyboard-only
navigation, 200 percent zoom, long values, zero/one/many-page tables, empty and
invalid filters, analysis pending/failure/retry, Back/resume, and direct URLs
before and after the gate. Deployment is a new immutable wheel release with
installed markers, service restart, public login/onboarding checks, one
fictional completed gate, and authenticated Jobs filter/sort verification.
The current green release remains the deployed state until those checks pass.

##### Information architecture and routes

The authenticated application uses one sidebar and one top bar. The sidebar is
navigation, not a collection of marketing calls to action. The top bar shows
the current page title, a compact state indicator when useful, and the account
menu. There is no permanent global primary action: each page owns the action
that advances its current task.

| Sidebar destination | Canonical route | Contents |
|---|---|---|
| `Overview` | `/` | Current onboarding/activation state, the next required action, the latest run's `Seen`, `Accept`, and `Drop`, browser-task status, active search directions, recent accepted jobs, and recent artifacts |
| `Jobs` | `/jobs` | Candidate jobs in a compact table with filters, durable decision state, track, source/provenance, Agent reasoning, and links to available artifacts; it always states that job application is never automatic |
| `Search directions` | `/profile` | Approved and proposed tracks, `core|review|discovery` policy, representative retrieval keywords, locations, countries, languages, employment constraints, sources, and the Agent summary/refinement boundary |
| `CV & evidence` | `/resumes` | Uploaded source resumes, analysis state, approved factual evidence, generated tailored resumes, provenance, validation state, and downloads in the two-column workspace defined below |
| `Activity` | `/runs` | Acquisition, screening, browser-task, tailoring, artifact, and sink runs with timestamps, status, counts, bounded error summaries, and artifact references |
| `Connections & settings` | `/settings` | Local connector/enrollment status, browser-worker availability, optional Email/Notion integration state, schedules/budgets, account/privacy controls, and sign out |

`/settings` gains a `GET` representation while the existing mutation remains a
CSRF-protected `POST`. `/resumes/{filename}` remains the authenticated download
route and highlights `CV & evidence`. The onboarding flow remains at
`/onboarding`, but it is not a permanent sixth primary destination. While the
tenant is not `active`, a setup module immediately below the product mark shows
`Continue setup`, the current human-readable step, and `n of 5`; it links to
`/onboarding`. It disappears after activation, while settings retains a link to
review the completed configuration. Direct navigation to an authorized route
continues to work independently of the sidebar.

The five visible onboarding milestones remain:

1. Email verified.
2. Resume and goals supplied.
3. Search proposal approved.
4. Local connector enrolled.
5. Calibration complete.

They are a presentation over the durable state sequence; they do not introduce
a second state machine. A later invalidation returns the setup module and names
the exact step that must be repeated.

##### Page content contracts

`Overview` defaults to the most recent completed run and labels its time range.
Its three summary values use existing run statistics without reinterpretation:

- `Seen` is `jobs_seen`.
- `Accept` is `jobs_new + jobs_updated`; it is not labelled or described as
  only newly discovered jobs.
- `Drop` is `jobs_filtered`; failed records remain separately visible as an
  error state and are not silently counted as a policy rejection.

When there is no completed run, the metric strip shows em dashes and an honest
empty-state sentence rather than zeroes that imply a completed search. The
overview's next-action panel has precedence over metrics while onboarding is
incomplete. Browser queue health, external-sink publication, and artifact
generation are separate statuses; one cannot be used as proof of another.

`Jobs` uses a table rather than one card per job. The initial columns are title,
company, location, search direction, source, score when a validated score
exists, decision, and first-seen time. Filters cover `Accept`, `Review`, and
`Drop`, track, and source. A job detail view shows canonical facts, raw source
provenance, deterministic gate results, the validated Agent decision and
reasoning, evidence references, tailoring/artifact state, and publication
state. Missing values render as an em dash. No button, badge, or empty state may
imply that Positions submitted an application.

`Search directions` presents each track once. Track cards are permitted here
because each track is an independent policy object, but the cards are flat,
compact, and arranged as a list rather than a decorative grid. Each track shows
its label, mode, representative keywords, locations, languages, employment
scope, enabled sources, and whether resume generation is allowed. Refinement is
plain-language Agent input. Before approval, the page renders every value that
will be persisted; after approval, changes explain which calibration and
artifact states will be invalidated.

`CV & evidence` is the closest adaptation of the resume-analysis reference:

- The left work area lists the authoritative uploaded resume and other retained
  versions with filename, media type, observed time, provenance, analysis
  status, and default/authority state. It contains the accessible file input,
  upload constraints, and generated tailored-resume list. Generated PDFs are
  visibly labelled as outputs, never evidence.
- The right analysis area shows `pending|failed|ready|approved` state, extracted
  evidence grouped by experience/skill/education or another persisted category,
  missing or ambiguous information, supported search directions, artifact
  validation, and the next approval/refinement action.
- The right area uses disclosure sections for long analysis. Section headers
  contain a label and a persisted count or status, not a decorative numeric
  score. A gauge or overall score is forbidden until a named, versioned,
  deterministic or validated Agent contract persists that value and documents
  its meaning.
- Approval is explicit and follows a complete review surface. The page never
  treats upload, extraction, analysis completion, evidence approval, resume
  generation, PDF validation, or publication as interchangeable states.

`Activity` uses a fixed-column table with run type, source/track, start and end
time, state, `Seen`, `Accept`, `Drop`, failed count, and a short detail field.
Run detail groups acquisition, filtering, Agent screening, tailoring, artifact,
browser, and sink stages. Raw logs and private payloads are not rendered into
the Web page. Long errors are summarized and given an opaque run/task reference
for authorized server-side diagnosis.

`Connections & settings` displays server-known state only. It may create a
single-use enrollment token and show commands/instructions, but it does not
enumerate Chrome profiles, choose a Chrome window, read browser identity data,
or claim to control the extension. Connector, browser calibration, Email
acquisition, Notion publication, schedule, and account security are separate
sections with separate statuses.

The login page is a narrow, centered, single-purpose form without the
authenticated sidebar. It explains passwordless email sign-in, never asks for a
password, and uses the same typography, controls, focus state, and privacy
language as the dashboard.

##### Visual system

The interface is white and neutral, with a restrained deep-green action color.
Purple/blue template gradients, glassmorphism, blurred floating panels,
marketing hero sections, oversized display type, excessive pills, and rounded
cards inside rounded cards are excluded. Decorative gradients are not used in
the initial portal. A future chart may use solid semantic segments, but color
must encode persisted data and must have a text equivalent.

The exact initial tokens are:

| Token | Value | Use |
|---|---|---|
| `--canvas` | `#f5f7f5` | App background behind content surfaces |
| `--surface` | `#ffffff` | Sidebar, top bar, panels, tables, and forms |
| `--surface-subtle` | `#eef2ef` | Active navigation and grouped rows |
| `--text` | `#18201d` | Primary copy and headings |
| `--text-muted` | `#65706a` | Secondary copy, timestamps, and helper text |
| `--border` | `#dfe5e1` | Panel, input, table, and divider borders |
| `--accent` | `#245e52` | Primary action, selected control, and focus ring |
| `--accent-hover` | `#194a40` | Primary hover/active state |
| `--accent-soft` | `#e7f1ed` | Selected rows and positive neutral emphasis |
| `--success` / `--success-soft` | `#257052` / `#edf7f1` | Completed and healthy states |
| `--warning` / `--warning-soft` | `#8a6400` / `#fff7d6` | Waiting and attention states |
| `--danger` / `--danger-soft` | `#9b3a3a` / `#fff0f0` | Failed state and destructive actions |

The font stack is `Inter, ui-sans-serif, system-ui, -apple-system,
BlinkMacSystemFont, "Segoe UI", sans-serif`; the application never downloads
Inter or another remote font. Type sizes and line heights are `12/16`, `14/20`,
`16/24`, `20/28`, and `28/36` CSS pixels. `28/36` is the page title, `20/28`
the section title, `16/24` emphasized body text, `14/20` the default control and
table text, and `12/16` metadata. Font weights are limited to `400`, `500`,
`600`, and `700`; body copy is never justified or letter-spaced for decoration.

Spacing uses a `4, 8, 12, 16, 20, 24, 32, 40, 48` pixel scale. Buttons are
`36` pixels high on desktop and at least `44` pixels high on touch layouts;
inputs are `40` pixels high, textareas have a `120` pixel minimum height, table
headers are `40` pixels high, and normal data rows target `52` pixels. Corners
are `4` pixels for buttons, `6` for inputs and status controls, and `8` for
panels. Fully rounded shapes are reserved for short status badges. Panels have
a one-pixel border and no default shadow; only menus/dialogs may use
`0 8px 24px rgb(24 32 29 / 12%)`.

The desktop sidebar is `224` pixels wide and full height. The top bar is `64`
pixels high. Main content has a `1440` pixel maximum width, `32` pixel desktop
padding, and `24` pixel gaps. The resume-analysis workspace uses
`minmax(520px, 3fr) minmax(360px, 2fr)`. Page headers use no hero treatment and
remain close to the task content. The active navigation row has a subtle solid
background and a three-pixel accent edge; inactive rows are transparent. Icons
are simple `18x18` inline SVGs using `currentColor`, marked decorative when the
text label is present. No external icon font or third-party image is loaded.

One task region has at most one solid primary button. Secondary actions use a
one-pixel border; tertiary actions are text buttons. Destructive actions use
danger text and require an explicit confirmation step. Status badges always
include text. Empty states use one plain sentence and, only when useful, one
next action; they do not use illustrations.

##### Responsive and interaction behavior

The layout has four explicit modes:

| Viewport width | Behavior |
|---|---|
| `>= 1280px` | Full `224px` sidebar, `64px` top bar, and two-column analysis workspace |
| `960-1279px` | `72px` icon rail with accessible labels/tooltips; two columns remain only while both minimum widths fit |
| `720-959px` | `72px` rail and a single content column; the analysis area follows the work area |
| `< 720px` | Sidebar becomes a top-bar menu implemented with semantic disclosure; content padding is `16px`, controls meet the `44px` touch target, and all workspaces are one column |

Tables keep semantic table markup at every width. A labelled overflow wrapper
allows horizontal scrolling on narrow screens; columns are not silently hidden
unless the same values are available in an explicit row-detail view. The mobile
menu, long analysis sections, and account menu use native `<details>` and
`<summary>` in the initial implementation, so navigation and disclosure do not
depend on JavaScript. Mutations remain normal forms followed by
Post/Redirect/Get. Background analysis displays an honest pending state and a
manual refresh action; there is no hidden polling or WebSocket.

Hover and focus transitions last `120-160ms`; content does not animate into
place. `prefers-reduced-motion: reduce` removes non-essential transition and
spinner motion. A spinner is never the only status signal.

##### Accessibility, privacy, and rendering constraints

Every page provides a skip link, `header`, `nav`, `main`, and one `h1`. The
active navigation link uses `aria-current="page"`. Form controls have visible
labels; help and error text are connected with `aria-describedby`. Tables use
captions when their purpose is not already named, `scope` on header cells, and
text for every status. Keyboard focus uses a two-pixel accent outline with a
two-pixel offset and is never removed. Normal text and controls meet WCAG AA
contrast; color is not the sole carrier of state.

The first implementation extends the existing
`src/job_scraper/adapters/server/portal.py` renderer rather than adding a second
front-end home. The existing `_page` shell becomes the shared dashboard shell;
one `PORTAL_CSS` constant and small semantic render helpers supply the tokens,
navigation, panels, statuses, forms, and tables. This is the closest existing
home and is sufficient for the initial server-rendered UI, so no template
engine, static-asset pipeline, JavaScript bundle, or new UI module is added.

The HTML contains no remote font, analytics, image, script, or stylesheet
request. All icons are local inline SVG. All tenant, resume, job, source, Agent,
error, and filename values are escaped at output. GET routes do not mutate;
forms remain CSRF protected; file access stays resolved under the authorized
tenant workspace. UI convenience never weakens upload validation, session
cookies, authorization, artifact validation, or browser-worker isolation.

The portal sends a restrictive response policy compatible with the inline
style block: `default-src 'self'; style-src 'self' 'unsafe-inline'; img-src
'self' data:; script-src 'none'; object-src 'none'; base-uri 'none';
frame-ancestors 'none'; form-action 'self'`. It also sends
`Referrer-Policy: no-referrer`, `X-Content-Type-Options: nosniff`, and denies
framing. No private payload appears in HTML comments, data attributes, CSS,
client logs, or analytics.

##### Implementation prerequisites and staged rollout

The styled shell must not conceal incomplete product wiring. As of the design
approval, the existing Web path reaches connector enrollment but does not yet
contain server-verified transitions for `browser_calibrated`,
`pipeline_calibrated`, or `active`. The current jobs and resume pages also read
CSV filenames and generated artifacts that do not carry every field required by
the page contracts above. Styling these surfaces alone is not completion.

Implementation proceeds in this order:

1. **Correct the state model.** Add `search_preferences_approved` between
   `tracks_approved` and `integrations_configured`. Saving locations, countries,
   languages, employment scope, and sources advances only to
   `search_preferences_approved`. `integrations_configured` requires durable,
   tenant-scoped runtime policy plus the explicitly enabled optional
   integrations; it does not mean merely that a preferences form was saved.
   The idempotent schema migration maps existing `integrations_configured`
   portal rows back to `search_preferences_approved` unless the portal store has
   durable evidence for every required integration/configuration binding.
2. **Complete activation transitions.** Enrollment redemption advances only to
   `connector_enrolled`. A server-validated browser calibration result advances
   to `browser_calibrated`; a bounded authorized pipeline calibration with
   separately verified acquisition and screening outcomes advances toward
   `pipeline_calibrated`. Artifact and sink gates are mode-aware and
   configuration-aware: validate one artifact only when an approved `core`
   track enables resume generation; assert no artifact for
   `review|discovery`-only policy; validate publication only for enabled
   external sinks; otherwise validate durable authenticated portal display.
   Only the final activation transaction enables schedules/worker operation and
   records `active`. `/onboarding` renders waiting, failed, retry, skipped-as-
   not-applicable, and next-action content for every state.
3. **Quarantine source resumes.** Upload stores the original document, hash,
   extracted text, and analysis under a tenant-scoped unapproved document
   version. It must not create or select a resume variant used by screening or
   tailoring. Explicit evidence/authority approval creates a new versioned
   authoritative variant. Re-upload creates another source-document version and
   invalidates dependent proposal/calibration state; it never silently retains
   the first imported variant as authority. Existing compatibility workspaces
   are migrated without deleting source documents or generated artifacts.
4. **Provide one tenant-scoped read model.** Introduce a server-side
   `PortalReadModel` projection assembled from the authoritative portal store,
   canonical job/run store, browser task store, approved evidence state, and
   artifact manifests. It is a read projection, not a second source-of-truth
   database and not a cache of raw private payloads. The first contract contains:

   | Projection | Required fields |
   |---|---|
   | `overview` | activation state, next action, latest completed run ID/time range, `jobs_seen`, `jobs_new`, `jobs_updated`, `jobs_filtered`, `jobs_failed`, active track summaries, browser queue summary, recent accepted-job IDs, recent artifact IDs |
   | `jobs` | opaque job ID, title, company, location, track, source, optional validated score, durable decision/disposition, first-seen time, Agent-reason reference, artifact references |
   | `tracks` | opaque track ID/version, label, mode, keywords, locations/countries, languages, employment scope, sources, resume-generation policy, approval state |
   | `documents` | opaque document ID/version, escaped display filename, media type, upload time, content hash reference, provenance, authority state, analysis state |
   | `evidence` | opaque evidence ID/version, category, claim text, source reference, approval state, ambiguity state |
   | `artifacts` | opaque artifact ID, source job/document reference, kind, created time, validation/publication state, authorized download reference |
   | `activity` | opaque run/task ID, kind, source/track, times, stage/state, `Seen`, `Accept`, `Drop`, failed count, bounded error summary |
   | `connections` | connector enrollment, browser calibration, pipeline calibration, Email/Notion state, schedule state, and last verified time as independent fields |

   The job table links to `/jobs/{opaque_job_id}`; that route resolves the
   opaque ID through the tenant before reading detail. The renderer does not
   infer `decision`, source, location, or authority from a filename or from a
   CSV column that has another meaning. Unknown data remains unknown.
5. **Close the Web security gaps.** Logout and every other mutation require the
   session-bound CSRF token and Post/Redirect/Get. The shared response wrapper
   supplies the CSP and headers above. Existing tenant/path/upload checks remain
   fail-closed. These controls are prerequisites for styled-interface
   acceptance, not visual polish to defer.
6. **Stage the user-visible claim.** Before the prerequisites pass, public docs
   call the Web path a beta that covers login, resume analysis, conversational
   proposal review, preference approval, and connector enrollment. Operator
   bootstrap/calibration remains the compatibility path. Only a fictional
   end-to-end test through `active`, followed by restart and authorization
   checks, permits the docs and dashboard to call the Web flow complete.

##### Interface verification

Offline tests use fictional tenant, resume, track, job, run, and artifact data
and verify:

- all authenticated pages render the shared landmarks and correct active
  navigation destination;
- incomplete tenants see one resumable setup module and active tenants do not;
- `Seen`, `Accept`, and `Drop` bind to `jobs_seen`,
  `jobs_new + jobs_updated`, and `jobs_filtered` respectively;
- the CV/evidence page distinguishes source documents, approved evidence,
  generated outputs, and every analysis/approval state;
- empty, pending, failed, ready, and approved states have visible text and a
  valid next action;
- untrusted content is escaped, POSTs enforce CSRF, cross-tenant IDs and paths
  fail closed, and no external asset URL is emitted;
- the sidebar, two-column workspace, one-column breakpoints, table overflow,
  focus treatment, reduced-motion rule, and print-safe download links are
  present in the generated HTML/CSS.

Manual browser review uses fictional data at `1440x900`, `1024x768`, and
`390x844`. It checks visual hierarchy, overflow, keyboard-only navigation,
focus order, zoom at 200 percent, error messages, long company/job/filename
values, and the full upload -> analysis -> refinement -> approval path. No
personal screenshot becomes a committed golden fixture. The standard Ruff,
Pyright, and pytest gates still run because the renderer is Python code.

Repository separation is permitted only after fictional conformance fixtures
exercise major-version rejection, minor-version forwarding, duplicate delivery,
stale evidence/policy rejection, cross-client access denial, artifact hash
mismatch, uncertain sink creation, checkpoint replay, and rollback to the
single-client implementation. The existing feed schema 2 and screening-result
schema 1 are migration inputs, not silently rebranded multi-client APIs.

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

### 2026-08-30 Dashboard deployment evidence

- The server-rendered Dashboard shell, responsive navigation, honest empty-run
  metrics, proposal/approval labeling, CSRF-protected logout, and security
  headers passed the repository's offline Ruff, Pyright, and Pytest gates.
- A wheel built from that verified workspace was installed into the immutable
  VPS release `/opt/positions/releases/20260830-dashboard/.venv`. The installed
  package was checked for Dashboard and CSP markers before service cutover.
- `positions-browser-api.service` was switched from the prior conversation
  release to that Dashboard release. Its loopback `/healthz` and the public
  deployment endpoint returned HTTP 200 with contract version `1.0`; the public
  login response contained the new auth shell and the
  expected CSP, `Referrer-Policy`, and frame-denial headers.
- An explicit service stop/start restored loopback and public responses, while
  the Cloudflare Tunnel service remained active. This is service-restart proof,
  not a whole-host reboot or proof of a completed client browser calibration.
- The deployed Dashboard remains honest about the current Web beta boundary:
  client-owned Chrome enrollment/calibration and a complete tenant-scoped run
  read model are separate acceptance gates and are not inferred from public UI
  or health availability.

### 2026-08-30 Dashboard redesign deployment evidence

- The white B2B shell, blue/ink palette, icon-led summary metrics,
  one-question onboarding sequence, mandatory HTML-route onboarding gate, and
  canonical SQLite-backed Jobs table passed Ruff format/check, Pyright, and the
  complete offline test suite (`474 passed, 1 skipped`).
- The Jobs page reads all matching canonical rows from the configured jobs
  database, provides validated search/source/location/decision filters,
  whitelisted column sorting, exact totals, and server pagination; active
  filter state is preserved across sorting and pagination.
- The verified wheel was installed into immutable release
  `/opt/positions/releases/20260830-redesign/.venv`. The production unit was
  switched to that executable with explicit
  `--job-db /srv/positions/data/jobs.db`; the prior unit was retained as
  `/etc/systemd/system/positions-browser-api.service.pre-redesign-20260830`.
- After service restart, `positions-browser-api.service` and `cloudflared` were
  active, loopback `/healthz` returned contract version `1.0`, the public login
  returned HTTP 200 with the redesign marker, and unauthenticated public
  `/jobs` redirected to `/login`. Existing tenant onboarding state was not
  modified during deployment verification.

#### Onboarding navigation correction

- A tenant may retain up to five source resumes. The upload control accepts a
  batch, validates the complete batch before persistence, and reports the
  retained-document count. Adding another source does not overwrite an
  existing private workspace configuration.
- Every question after the resume screen has a Back action. Returning to an
  answered question pre-fills its durable answer; saving an edit regenerates
  the proposal once all required answers and resume analysis are ready.
- The first question can return to the resume screen to add another source.
  Final confirmation redirects directly to `/`; it must not strand the user
  on an empty or completed onboarding page.
- A retained resume with a missing analysis row is a recoverable historical
  state, not an empty review screen. The final step shows an explicit
  re-analysis action; the POST recreates pending state from retained extracted
  text and regenerates the proposal without requiring another upload.
- Pending analysis renders a live status region, reduced-motion-safe activity
  indicator, explanatory copy, and a bounded four-second document refresh.
  Failures retain a bounded internal diagnostic and distinguish temporary
  Agent usage-capacity exhaustion from a generic unavailable service without
  exposing resume text or credentials.
- An installation may configure an ordered list of separately authorized
  `CODEX_HOME` directories. Resume analysis and proposal refinement retry the
  next identity only for explicit usage/rate/quota capacity errors. Invalid
  output, authentication errors, timeouts, and other failures remain fail-
  closed. Logs record only the one-based identity slot, never its path or auth
  material.

## Follow-ups

The browser-task spec now fixes the concrete browser runtime, profile model,
identity, tenant isolation, queue/outbox, HTTPS API, cold start, and scheduling
mechanisms. Before separating screening, tailoring, artifacts, or administration
into further products, extend this document or the single closest owning spec;
do not create another general server/client design alongside these two homes.
