# Downstream agent-screening orchestration

## Outcome

Allow a private downstream workspace to run semantic job screening immediately
after a successful `job-scraper run` without making the public job-scraper
framework depend on a person's resumes, agent CLI, credentials, or sibling
workspace layout.

## Scope

- In scope:
  - Preserve `job-scraper run` as the acquisition and publication boundary.
  - Document that downstream automation may invoke that command and continue
    only when it exits successfully.
  - Keep downstream agent prompts, resume facts, generated files, caches, and
    external-workspace reconciliation outside this repository.
- Out of scope:
  - A built-in resume-screening component, agent provider, or external command
    hook in job-scraper.
  - Runtime configuration containing a resume workspace path or agent account.
  - Automatic applications or submissions.

## Acceptance criteria

- [x] The private downstream command can optionally run `job-scraper run`
  before screening and stops if acquisition returns a non-zero exit code.
- [x] A screening-only mode can reuse already acquired SQLite data without
  network acquisition.
- [x] No personal resume content, agent credentials, private paths, or live
  external payloads are added to this repository.
- [x] Job-scraper remains independently runnable and its public CLI contract is
  unchanged.
- [x] Downstream selection does not impose a per-track quantity cap after a job
  meets its configured score and metadata requirements.

## Design and constraints

The dependency direction is deliberately one-way:

```text
private downstream orchestrator -> job-scraper CLI / read-only SQLite outputs
job-scraper                      -X-> private downstream workspace
```

This keeps the public composition boundary described in
[`../architecture.md`](../architecture.md) intact. The downstream process owns
agent-provider selection, structured-output validation, cost limits, caching,
resume generation, and any external-workspace reconciliation. It must not
modify or delete source SQLite databases.

Gap lists are downstream audit evidence, not a numeric rejection rule. A
downstream screen may retain hard or interview-risk gaps for review, but must
not use the count of a valid gap list to reject a job. The structured-response
contract may bound list size for safe transport and require the agent to
consolidate overflow; that contract bound is not a candidate threshold.

Likewise, track membership is a routing and reporting dimension, not a quota.
After a job meets the configured score and metadata requirements, downstream
generation must not discard it because another job in the same track ranked
higher.

Downstream operators may expose a dedicated generated-resume directory through
an operating-system shortcut or directory link. Generated filenames begin with
the normalized company name, reserve separate bounded components for company and
role, preserve Unicode letters and digits, and include a stable job-identity
suffix. Normal replacement and reset both retain backward-compatible recognition
of earlier generated names for safe external-workspace cleanup.
External workspaces that strip Unicode filename characters are matched through a
stable ASCII comparison key; the job hash remains part of that key to prevent
cross-job cleanup collisions.

## Verification

- Confirm the downstream orchestrator constructs an argv list and does not use
  a shell command string.
- Test that a failed acquisition prevents screening.
- Test screening-only mode without network or credentials.
- The independent user-path simulation is required because this introduces a
  cross-workspace operational flow even though the job-scraper CLI is unchanged.

## Follow-ups

Superseded as the forward-looking architecture decision by
[`2026-08-28-first-class-agent-screening.md`](2026-08-28-first-class-agent-screening.md).
This specification remains the record of the previous one-way downstream
orchestration and its completed acceptance criteria.
