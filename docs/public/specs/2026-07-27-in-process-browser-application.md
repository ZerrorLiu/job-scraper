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

## Phase-one source priority

The first implementation phase prioritizes the two supported job sources that
are already present in the discovery registry:

1. `linkedin_direct`: inspect the real LinkedIn destination and stop at
   authentication or other access-control boundaries.
2. `indeed_brightdata`: preserve the Bright Data acquisition path, verify the
   stored Indeed job/application URL, and inspect its real application CTA or
   downstream form flow.

Other sources remain discovery-compatible but are not application-adapted in
this phase. Source priority does not authorize bypassing login, consent,
CAPTCHA, anti-bot controls, or submitting an application.

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
- [ ] Batch preparation never clicks a submit control and caps each browser
  batch at 20 tabs.

## Design and constraints

The application runner is an in-process use case in this repository. It reads
accepted jobs from the existing local workspace database and writes application
state back to the same database. Domain and ports remain independent of
browser vendors, databases, and network transports; the real browser is an
adapter selected at runtime.

Private runtime configuration is stored outside the Git worktree. It contains
the browser profile path, confirmed candidate facts, policies, approved
documents, and evidence directory. Public fixtures use fictional values only.

### Long-running browser session

The application runner must own one long-lived dedicated browser session for a
run rather than launching and closing a browser for every inspection. The
session records its current job, URL, platform flow, and human-action state in
private runtime state so a login or CAPTCHA pause can resume in the same
profile. A human may complete authentication, consent, or CAPTCHA in the
visible browser; the runner must then continue from the recorded step without
restarting the session or asking for the same action again.

The session state is private runtime data and must never contain passwords,
cookies, tokens, or screenshots in the repository. A crashed runner may reopen
the dedicated profile and recover the last safe checkpoint, but it must stop
for review when the page, job identity, or submission outcome is uncertain.

The first live rollout targets one known platform or form-flow signature and a
small explicitly selected set of real jobs. Fixture tests verify contracts and
state transitions, but do not count as live browser validation.

### Batch preparation mode

The runner also supports a preparation-only batch. It opens at most 20 accepted
jobs in one dedicated browser context, one tab per job, follows supported
application links, fills only confirmed facts, and attaches only an approved
document. It leaves uncertain fields, CAPTCHA, consent, and every final submit
control untouched. The context remains open so the user can review and submit
each tab manually. A later batch may be selected with an explicit offset; the
runner does not claim that a tab was submitted merely because preparation
reached a form.

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
