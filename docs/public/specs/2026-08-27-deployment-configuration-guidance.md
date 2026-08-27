# Deployment configuration guidance

## Outcome

A third-party deployment succeeded — the installation ran — but the agent
improvised everything inside the configuration. The runbook said to collect
queries, keywords, and destinations; nothing said how to turn a person's answers
into them, what the Notion workspace would end up looking like, what the mailbox
file needed beyond a host, which components are optional or paid or not built
here, or which steps are safe to run twice.

That is the wrong division of knowledge. The user cannot be expected to know any
of it, so the agent has to — and the agent only knows what the repository tells
it.

After this change an agent has a stated method for authoring a profile, the
exact external shapes the sinks produce, an honest status for every optional
component, and an idempotency table for redeployment.

## Scope

- In scope: a profile-design document; the Notion workspace and status contract
  and the mailbox key reference in the configuration reference; interview and
  workspace-generation pointers in the runbook; drift guards for the two shapes
  a downstream consumer depends on.
- Out of scope: any behavior change. No source file changes except a comment;
  no configuration key added, removed, or defaulted differently.

## What this change replaces

Nothing is superseded. Existing homes were checked first, as
`AGENTS.md` requires:

- `agent-deployment.md` owns the procedure and gained pointers, not content. It
  is already the longest document; folding authoring method into it would have
  buried the steps.
- `configuration.md` owns key-level reference, so the Notion structure and the
  mailbox defaults were added there rather than to the new document.
- `operations.md` is for an installation that already exists.

What remained — how to *decide* — matched no existing home, so
`profile-design.md` is a new document rather than a section appended to one that
does something else.

## Design and constraints

The keyword documentation records a distinction the code makes and nothing
stated: `target_keywords` accepts or rejects a job, while `include_keywords`
only annotates it with `keyword_hits` and seeds tech-stack extraction. `init`
seeds both from the same input, which is a reasonable default and a poor end
state. An agent tuning what it believed was a filter would have been editing an
annotation.

Optional components are listed with an honest status rather than as a flat menu.
Three are gated behind explicit environment flags precisely so that credentials
alone cannot spend money, and one — downstream screening — is an interface this
repository publishes but does not implement. Presenting these as equivalent
choices is how an agent promises a user something that does not exist.

The screening interface is described by its contract, not by naming any
consumer. The feed record, its version semantics, and the publication object
that lets a screener write back to the object the user already reads are the
whole public surface; the privacy boundary excludes identifying a private
consumer repository.

Idempotency is documented per command because the answers differ: `init`
refuses, the database commands are safe, `run` is duplicate-safe but not free,
and redeploying elsewhere is a workspace copy rather than a re-run. The refusal
is the important one — it is what prevents a repeated deployment from discarding
a user's accumulated tuning.

## Acceptance criteria

- [x] Splitting profiles, building the search matrix, and choosing each keyword
      field have stated methods, including what each field actually affects.
- [x] The Notion container, table title, seven properties, adaptive property
      types, and status vocabulary — including the aliases that round-trip — are
      specified.
- [x] Every key `init` writes into `email.toml` is documented, including why
      `tracks.config_paths` is empty and when it is not.
- [x] Every optional component carries a status: available, gated and paid,
      infrastructure-dependent, or interface-only.
- [x] The downstream screening interface is described by its contract without
      naming a private consumer.
- [x] Every deployment command states what happens when it is run twice.
- [x] The runbook points at the method at the interview and at workspace
      generation.
- [x] Documented Notion properties and feed fields are asserted against the
      code, and both assertions were observed failing against a deliberate
      omission.

## Verification

- `uv run ruff format --check .`, `uv run ruff check .`, `uv run pyright`,
  `uv run pytest`.
- `test_documented_notion_properties_match_what_the_sink_writes` compares the
  documented property table with the keys `build_daily_properties` returns.
- `test_documented_feed_record_matches_the_published_contract` compares
  `ScreeningFeedRecord` fields with the names the design document mentions.
- Both were verified by removing a documented name and observing the failure.
- Every factual claim was read out of the implementation rather than recalled:
  the property builder, the status mapping, the bootstrap templates, the policy
  translation, and the CLI's mailbox wiring.

## Follow-ups

`init` seeding `target_keywords` and `include_keywords` identically is now
documented as a starting point. Whether the two should be seeded differently, or
whether `include_keywords` should default to a broader vocabulary, is a
behavior question left open.
