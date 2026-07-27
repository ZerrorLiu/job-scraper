# In-process browser application delivery

## Outcome

Add real browser-based application delivery directly to `Positions`. The
discovery pipeline remains responsible for accepted-job selection, while an
application runner uses a private browser profile and private candidate data
to complete supported application forms.

## Scope

- In scope:
  - A local application queue backed by the existing workspace database.
  - Eligibility checks, duplicate protection, explicit application states, and
    immutable private attempt records.
  - Real Chrome/Chromium control through a dedicated persistent profile.
  - Structured, evidence-backed answers from private runtime facts and
    approved documents.
  - Platform-specific adapters and a conservative accessible-form fallback.
  - Confirmation detection, honest `submission_unknown` handling, and bounded
    retry of safe technical failures.
  - CLI commands for inspection, doctor checks, single-job execution, batch
    execution, retry, and status reporting.
- Out of scope:
  - A separate `Positions-Apply` project or handoff export/import contract.
  - CAPTCHA solving, anti-bot bypass, access-control bypass, or fingerprint
    evasion.
  - Inventing or guessing personal facts.
  - Committing private facts, documents, browser profiles, credentials,
    screenshots, mail, databases, or application evidence.

## Acceptance criteria

- [ ] Only jobs already accepted by the discovery pipeline can enter the
      application queue.
- [ ] The runner prevents duplicate application attempts and never retries a
      submitted or `submission_unknown` job automatically.
- [ ] Real browser execution uses a dedicated persistent profile outside the
      repository.
- [ ] Every submitted answer and selected document has a private source
      reference or the job is held for review.
- [ ] Unsupported fields, missing facts, challenges, and uncertain outcomes
      become explicit review states rather than guessed submissions.
- [ ] A clear page confirmation or application reference is required for
      `submitted_confirmed`.
- [ ] Every attempt preserves an immutable private snapshot of input,
      answers, browser flow, and terminal evidence.
- [ ] The default test suite remains offline and credential-free; live browser
      execution is opt-in and separately reported.

## Design and constraints

The application runner is an in-process use case in this repository. It reads
accepted jobs from the existing local workspace database and writes application
state back to the same database. Domain and ports remain independent of
browser vendors, databases, and network transports; the real browser is an
adapter selected at runtime.

Private runtime configuration is stored outside the Git worktree. It contains
the browser profile path, confirmed candidate facts, policies, approved
documents, and evidence directory. Public fixtures use fictional values only.

The first live rollout targets one known platform or form-flow signature and a
small explicitly selected set of real jobs. Fixture tests verify contracts and
state transitions, but do not count as live browser validation.

## Verification

- Run the repository's offline quality gates before handoff.
- Exercise `apply doctor` against a fictional or empty private workspace
  without submitting an application.
- For live validation, inspect the real browser session, form fields, final
  confirmation, and private attempt snapshot separately from offline tests.

## Follow-ups

- Select the first real platform/form-flow signature.
- Choose the browser connection method and private runtime directory.
- Define the initial confirmed-fact and approved-document schemas.
