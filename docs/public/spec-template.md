# Change specification template

Use this optional template in `docs/public/specs/YYYY-MM-DD-<slug>.md` when a
change needs durable rationale or a migration contract. Keep examples fictional and omit all runtime data,
credentials, search preferences, and external workspace identifiers.

# <Change title>

## Outcome

State the user or maintainer problem and the observable result.

## Scope

- In scope:
- Out of scope:

## What this change replaces

Name the existing file, module, document, test, or flag this change supersedes,
and confirm it is removed here rather than left beside its replacement. If
nothing is superseded, say where you looked for an existing home and why adding
a new one is correct. If something must stay for compatibility, state the
condition that ends it.

## Acceptance criteria

- [ ]

## Design and constraints

Describe public contracts, affected layers, compatibility, failure behavior,
and relevant privacy or data-safety constraints. Link to the authoritative
architecture or configuration document rather than duplicating it.

## Verification

List relevant tests and checks. Include independent review when it adds value.

## Follow-ups

Record deferred work, trade-offs, or `None`.
