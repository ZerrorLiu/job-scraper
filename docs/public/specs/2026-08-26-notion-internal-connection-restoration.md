# Notion Internal Connection Restoration

## Outcome

The supported Notion authentication path is the existing Internal connection
token supplied through the private `NOTION_INTEGRATION_TOKEN` environment
variable. The repository and deployment no longer contain a browser-login
service or its runtime dependencies.

## Scope

- In scope: remove the temporary browser-auth command surface, token storage,
  deployment assets, dependency, and documentation; restore the Internal token
  path; retain the database-ID binding and write-safety work.
- Out of scope: modifying any Notion content, rotating a token, changing the
  configured Notion target, deleting CSV or SQLite data, or changing Bright
  Data behavior.

## Acceptance criteria

- [x] A configured Internal token enables the Notion client.
- [x] No browser-auth broker, callback, token vault, or related command remains
  in the public package or deployment assets.
- [x] The existing ID-binding and non-idempotent-write safeguards remain
  covered by offline tests.
- [x] The full repository quality gates pass.

## Design and constraints

`NOTION_INTEGRATION_TOKEN` is private runtime configuration and must not be
stored in TOML, documentation examples, test fixtures, or version control. The
Notion destination identifiers remain separate non-secret configuration. See
the public configuration reference for the runtime contract.

## Verification

Run focused offline Notion tests and the quality gates from `AGENTS.md`. The
rollback must not make live Notion calls: no live verification is required.

## Follow-ups

None.
