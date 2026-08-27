# First-class agent screening with private profile evidence

## Outcome

Make validated agent screening the primary job-fit decision stage after
acquisition and before publication. The reusable orchestration belongs in this
repository; personal resumes, evidence, generated application material, and
provider credentials remain in a private profile workspace.

## Scope

- In scope:
  - Migrate the reusable fine-screen orchestration, structured response
    contract, cache/replay behavior, and cross-track routing into the job
    discovery workflow.
  - Let acquisition terms improve recall and guide an agent; do not use a
    literal role-keyword miss as the final fit decision before screening.
  - Preserve explicit user decisions and source-integrity outcomes as hard
    inputs to the workflow.
  - Treat ambiguous location evidence as screening context, not an automatic
    non-target-country rejection.
- Out of scope:
  - Storing a person's resume, evidence library, generated documents,
    credentials, private workspace paths, or agent account settings in this
    repository.
  - Automatic job applications or submissions.

## What this change replaces

This supersedes the forward-looking direction in
[`2026-08-24-downstream-agent-screening-orchestration.md`](2026-08-24-downstream-agent-screening-orchestration.md),
which kept all semantic screening in a separate downstream project. Its
completed historical behavior remains documented there; no parallel screening
engine should remain after migration.

The existing `feed` contract remains the closest stable acquisition boundary.
It is extended rather than replaced: private profile evidence is supplied to a
generic screening boundary without making it public runtime configuration.

## Acceptance criteria

- [ ] A candidate can reach agent screening even when its title misses a
  configured retrieval keyword, provided it passes explicit integrity and user
  decision checks.
- [ ] The agent receives the full job record plus externally supplied private
  profile evidence, and returns validated structured routing and fit results.
- [ ] A result includes a decision, one or more recommended tracks, supporting
  job/evidence references, honest gaps, and a confidence or review state.
- [ ] A malformed, unavailable, or unvalidated agent result fails closed and
  never silently publishes a job.
- [ ] A confirmed non-target country remains excluded, while unknown or
  ambiguous location evidence is represented for screening rather than treated
  as proof of exclusion.
- [ ] No resume content, profile facts, credentials, private paths, or live
  payloads enter source control, fixtures, documentation, or defaults.
- [ ] The previous downstream fine-screen implementation is removed or reduced
  to the private profile-data and document-generation responsibility; reusable
  orchestration has one home.

## Design and constraints

The flow becomes:

```text
acquisition -> canonical candidate and hard safety checks -> agent screening
            -> validated routing decision -> publication
```

Acquisition source, URL, explicit country evidence, browser/detail state, and
manual `Not Interested` decisions remain deterministic evidence. Retrieval
queries and title terms improve candidate recall but are not a substitute for a
resume-grounded fit assessment. An agent may route a candidate across tracks or
return `Review`; it must not invent experience that is absent from the supplied
private evidence.

The public engine owns an agent-provider-neutral request/response schema,
validation, deterministic cache keys, replay tests, and failure behavior. A
private profile workspace owns resume variants, evidence content, private
provider setup, and generated documents. The boundary must use validated data
and argv-style invocation where an external process is needed; it must not
depend on shell command construction or write to an acquisition database.

This replaces duplicated semantic decisions with one screening authority while
preserving the architecture's dependency direction and privacy boundary.

## Verification

Before implementation, add public fictional fixtures for a keyword-miss that
is accepted by evidence, an honest-gap `Review`, an explicit non-target-country
rejection, an ambiguous-location pass-through, and malformed agent output.
Run focused coverage and the standard Ruff, Pyright, and Pytest quality gates.
An independent user-path simulation is required because this changes the main
acquisition-to-publication workflow.

## Follow-ups

Implement incrementally: first correct location-evidence propagation and add a
screening boundary; then migrate reusable fine-screen orchestration; finally
replay previously screened candidates before switching publication authority.
