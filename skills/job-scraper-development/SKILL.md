---
name: job-scraper-development
description: Develop, repair, extend, or refactor the Job Scraper repository through its documented lifecycle. Use for any non-trivial code, public contract, CLI, configuration, adapter, pipeline, or architecture change in this repository.
---

# Job Scraper development

Follow [`AGENTS.md`](../../AGENTS.md) and the
[`agent development workflow`](../../docs/public/agent-development-workflow.md).
They are authoritative for architecture, privacy, lifecycle, and quality gates.

1. Discuss and confirm the outcome, scope, acceptance criteria, and material
   trade-offs.
2. Before code changes, create or update a public-safe spec in
   `docs/public/specs/` from
   [`spec-template.md`](../../docs/public/spec-template.md). Update linked
   public contract documentation at the same time.
3. Identify the narrowest affected boundary. Preserve the repository dependency
   direction and use the Port -> adapter/pure step -> registry workflow for
   extensions.
4. Implement the smallest composable change and add offline, credential-free
   focused tests using fictional inputs.
5. Run the required quality gates. For qualifying non-trivial changes, have an
   independent subagent exercise the documented user path without private data
   or a leaked expected answer.
6. Handoff with result, validation evidence, user-path findings, and clearly
   separated improvement suggestions.

Do not put runtime configuration, searches, identities, credentials, service
payloads, or external workspace identifiers in source, documentation, examples,
or tests.
