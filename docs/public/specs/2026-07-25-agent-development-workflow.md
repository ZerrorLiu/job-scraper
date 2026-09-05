# Standardize the agent development workflow

Historical rationale. Superseded on 2026-09-05 by the task-proportional rules
in `AGENTS.md`: workflow skills, routine specifications, and independent reviews
are no longer mandatory. The duplicate project skill has been removed.

## Outcome

Make future repository work follow one lightweight, documented lifecycle:
discuss the requirement, document the intended change, implement and test it,
then independently examine the user path and report improvements.

## Scope

- In scope: project-level instructions, a reusable public specification
  template, the workflow documentation, and links from the mandatory agent
  guide.
- Out of scope: private runtime configuration, application behavior, external
  service configuration, and automatic changes to any agent platform.

## Acceptance criteria

- [x] `AGENTS.md` requires outcome discussion, a documentation-first spec,
  implementation/testing, and risk-based independent user-path review.
- [x] A public-safe spec template is available for future changes.
- [x] One concise document and a project-local skill define phases, quality
  gates, extension guidance, simulation rules, and handoff expectations.
- [x] The workflow defines how to handle ambiguous outcomes, unavailable
  tooling, and post-review improvement proposals.
- [x] The workflow retains existing privacy, architecture, and offline-test
  boundaries.

## Design and constraints

`AGENTS.md` is the mandatory harness entry point because every repository agent
already reads it. The workflow document holds procedural detail so the guide
remains concise, while the project-local skill makes the procedure reusable.
User-path simulation is mandatory only where a user can
meaningfully encounter changed behavior; this avoids needless agent overhead
for mechanical work. The simulation must receive no private runtime data or
expected solution.

## Verification

- Inspect cross-links and repository-visible paths.
- Ask an independent subagent to follow the documented workflow for a realistic
  non-trivial task and identify ambiguity or friction.
- Run the standard quality gates; documentation-only changes do not require
  live integrations.

## Follow-ups

Collect findings from the first few feature changes. Promote recurring friction
into the workflow or a focused project skill only when it is demonstrated.
