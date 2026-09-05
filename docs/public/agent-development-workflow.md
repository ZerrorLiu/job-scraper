# Agent development workflow

This guide offers optional practices. `AGENTS.md` holds the binding project
rules for architecture, privacy, data safety, and relevant verification.
No workflow skill, fixed sequence, specification, or independent reviewer is
a prerequisite for every change.

## Choose the process

Start from the user's intended outcome and inspect the relevant existing code.
For a small fix, implement it directly and check the affected behavior. For a
complex contract or migration, record decisions and acceptance criteria using
the optional [spec template](spec-template.md). Extend an existing specification
when it already covers the concern.

Prefer existing modules and Ports. Explain a new abstraction when it materially
affects the design. Update public documentation when the public contract changes;
keep private runtime configuration and real payloads out of examples and tests.

## Verification

Use focused offline tests for meaningful behavior. For code changes spanning
multiple components, run the full project checks:

```powershell
uv run ruff format --check .
uv run ruff check .
uv run pyright
uv run pytest
```

For narrower changes, choose the relevant checks. Documentation-only changes
need diff, link, and consistency checks rather than unrelated Python tests.
Use the configured `uv` environment. If a check cannot run, explain the actual
obstacle and continue work that does not depend on it; never claim a pass.

An independent user-path or code review can help with unfamiliar interfaces,
complex changes, or consequential migrations. Use it when useful or requested,
not as an unconditional approval gate. Reviewers receive no private runtime
data and do not mutate the executor's files concurrently.

## Handoff

State what changed, the verification actually performed, and material remaining
limitations. Distinguish source changes from deployment and observed runtime
behavior. Keep the response proportional to the task.
