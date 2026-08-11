# Agent development workflow

This is the project-local harness for making changes. It combines the task
specification, the repeatable development skill, existing repository rules,
and verification into one small lifecycle. `AGENTS.md` makes it mandatory.
The triggerable project-local skill is
[`skills/job-scraper-development/SKILL.md`](../../skills/job-scraper-development/SKILL.md).

## The lifecycle

```text
Discuss outcome -> write or update spec -> implement -> verify -> independent
user-path review -> handoff with follow-ups
```

### 1. Discuss outcome

Begin by confirming the problem, desired result, acceptance criteria, scope,
and constraints. Identify material choices and ask only when they would change
the result or create risk. Do not request or record private runtime inputs
unless they are essential to a user-authorized local operation.

Define potentially ambiguous outcome words (for example, whether "resolved"
means configured values or a fully composed runtime result) in the spec. Do
not let an implementation silently choose that boundary.

### 2. Record the specification

For a behavior, contract, architecture, configuration, or extension change,
copy [the specification template](spec-template.md) to
`docs/public/specs/YYYY-MM-DD-<slug>.md` and complete it before code changes.
Update the relevant public documentation when that contract changes. A typo,
format-only edit, or provably behavior-preserving refactor may skip a new spec;
record the reason in the handoff.

The spec answers *why* and *what*. It is concise, reviewable, and public-safe;
it never stores credentials, personal search choices, live payloads, or
external workspace identifiers.

### 3. Implement with the project skill

Use this repeatable development procedure:

1. Read `AGENTS.md`, the active spec, and affected public contract documents.
2. Inspect the smallest relevant code path and select the appropriate
   architectural boundary or Port.
3. Make the smallest composable change that satisfies the acceptance criteria.
4. Add or adjust focused offline, credential-free tests using fictional data.
5. Keep the dependency direction and privacy boundary intact.

For extensions, follow the existing Port -> adapter or pure step -> registry ->
private runtime configuration -> contract test sequence. Do not introduce
import-time side effects or concrete adapters in application orchestration.

### 4. Verify

Run focused tests while implementing, then run the repository quality gates:

```powershell
uv run ruff format --check .
uv run ruff check .
uv run pyright
uv run pytest
```

For Domain, Pipeline, or configuration behavior changes, retain at least 90%
focused branch coverage for the changed core behavior. Run live tests only
when explicitly requested and credentials are available.

If `uv` is unavailable, stop executable Python verification and report the gate
as blocked. Do not substitute system Python or direct virtual-environment
executables, and never report an unavailable gate as passed.

### 5. Simulate the user path

For a non-trivial CLI, configuration, public extension/API, or architecture
change, ask an independent subagent to perform the intended user task using
only the request and repository artifacts. Give it no expected conclusion or
private context. Have it inspect the natural entry points, follow the documented
path, and report friction, ambiguity, safety concerns, and improvement ideas.

Treat this as evaluation, not approval: the implementing agent remains
responsible for validating the result. A mechanical behavior-preserving change
can skip the simulation; document why in the handoff.

### 6. Handoff and improve

Lead with the result. Report the spec and documentation updated, code and tests
changed, commands run and outcomes, user-path findings, and clearly separated
follow-up suggestions. Fix a finding immediately only when it violates the
active acceptance criteria or safety rules; otherwise present it as a proposed
follow-up for the requester to prioritize. Turn accepted recurring feedback
into a spec or this workflow; keep one-off preferences out of public repository
defaults.

## Trigger guide

| Change type | Spec | User-path simulation |
| --- | --- | --- |
| Typo, formatting, generated lock refresh | Optional | No |
| Bug fix with observable behavior | Update or add | When the flow is non-trivial |
| CLI/configuration/API/extension change | Required | Required |
| Domain, pipeline, or architecture change | Required | Required |
| Internal behavior-preserving refactor | Optional, state why | No |

## Responsibilities

- **Spec:** captures the change outcome and acceptance criteria.
- **Skill:** the implementation and verification procedure in this document.
- **Harness:** `AGENTS.md`, repository tooling, privacy rules, tests, and the
  lifecycle above.
- **Subagent review:** independently exercises the user experience and returns
  evidence-based improvement suggestions.
