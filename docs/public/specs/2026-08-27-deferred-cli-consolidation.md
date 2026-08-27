# Deferred CLI consolidation

## Outcome

`job-scraper run` still accepts three hidden pre-CLI flags, and two pre-CLI
entry modules still carry their own argument surfaces that `cli/main.py` drives
by assembling argument strings. None of it is reachable from `--help`, and none
of it is wrong today, but it is a second way to invoke the same behavior — the
kind of thing that survives indefinitely because no single change ever owns
removing it.

`AGENTS.md` now requires that anything kept for compatibility carry a stated
removal condition in its spec. This is that record. It removes nothing; it
makes the deferred work visible, bounded, and safe to pick up later.

## Scope

- In scope: record the deprecated surface and the condition that ends each
  item; pin the hidden-flag set with a test so it cannot silently grow; point
  at this record from the code where an agent would meet it.
- Out of scope: removing any flag, module, or behavior. Every item here is
  deliberately left working.

## What this change replaces

Nothing is superseded. The two Follow-ups recorded in
[the accumulation-controls spec](2026-08-27-repository-accumulation-controls.md)
are replaced by a pointer to this document, so the deferred work has one home
rather than a description in a completed spec's tail.

A separate register document was considered and rejected. `AGENTS.md` says a
deprecation's removal condition lives in its spec, and the archived pre-v2
`development_backlog.md` is the precedent for what a standing to-do list
becomes: a document nobody updates, superseded by `specs/`. This is a spec for
work that has not happened yet, which is the normal state of a spec.

## The deferred surface

### Hidden `run` flags

Declared with `argparse.SUPPRESS` in `cli/main.py`, forwarded by
`_legacy_run_flags`.

| Flag | Current behavior | Removal condition |
|---|---|---|
| `--all` | Accepted, and rejected in combination with `--profile`. Running every enabled profile is already the default, so the flag selects nothing. | Remove once no local scheduler, timer unit, or shell history in an active installation passes it. It is inert, so removal changes only the error path for `--all --profile`. |
| `--init-db` | Forwarded to `run_daily`, which initializes the database before acquiring. | Remove once `db init` is the documented way to do this, which it now is. Removal needs one release where operators have seen the replacement. |
| `--enable-indeed` | Forwarded to `run_daily`, which refuses the run with exit `2` unless `BRIGHTDATA_DIRECT_COLLECTION_ENABLED` is true. | Remove together with the flag's guard test, once source activation is understood to come only from the private profile. It is already incapable of enabling a paid request on its own. |

The set is closed and asserted by
`test_deprecated_run_flags_are_a_closed_set`. A fourth hidden flag fails that
test; removing one of these three requires editing it, so neither direction can
happen silently.

### Pre-CLI entry modules

`jobs/run_daily.py` and `jobs/run_all_tracks.py` predate the CLI. `cli/main.py`
invokes them by building argument lists, so the same options are declared
twice: once as CLI arguments and once as module arguments.

Consolidation means moving their orchestration behind the application layer and
calling it directly, leaving the modules as thin shims or removing them. The
condition for doing it: when a change needs to add or alter a `run` option, and
would therefore have to edit both surfaces anyway. Doing it speculatively risks
a large behavior-preserving refactor of the most load-bearing path in the
project for no immediate gain.

`jobs/run_brightdata_notion_e2e.py` is a live-verification utility, not a
duplicate entry point, and is out of scope here.

## Acceptance criteria

- [x] Every hidden `run` flag is listed with its current behavior and the
      condition that ends it.
- [x] The hidden-flag set is asserted as closed, and the assertion was observed
      failing against an added fourth flag.
- [x] `cli/main.py` points at this document where the flags are declared.
- [x] The accumulation-controls spec points here rather than restating it.
- [x] No flag, module, or behavior changed; the quality gates pass.

## Verification

- `uv run ruff format --check .`, `uv run ruff check .`, `uv run pyright`, and
  `uv run pytest`.
- The closed-set assertion was verified by adding a fourth suppressed flag to
  `run`, observing the failure, and reverting.
- No user-path simulation: nothing observable changed.

## Follow-ups

The two items above are the follow-ups. This document is where their status is
updated when either is picked up.
