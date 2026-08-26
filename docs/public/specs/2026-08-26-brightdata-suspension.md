# Bright Data suspension switch

## Outcome

Operators can suspend all Bright Data requests without removing the adapter,
credentials, stored snapshots, or historical job data. Direct Indeed collection
is disabled in local profile configuration, and email-detail enrichment remains
off unless the operator explicitly opts in after the provider is stable.

## Scope

- In scope:
  - Require an explicit environment opt-in before any direct Bright Data
    collection, including the one-off live E2E utility.
  - Make email-derived Indeed detail enrichment require an explicit environment
    opt-in in addition to its existing credentials.
  - Document the opt-in and remove the direct Bright Data source from the
    current profiles' inherited active source list while retaining its runtime
    configuration.
- Out of scope:
  - Deleting Bright Data code, credentials, snapshots, CSV files, or SQLite
    data.
  - Retrying, bisecting, paying for, cancelling, or repairing existing Bright
    Data snapshots.
  - Changing LinkedIn, IMAP, Notion, or CSV behavior.

## Acceptance criteria

- [x] Direct Bright Data source collection is disabled by the affected local
  profiles' effective `sources` list and an explicit environment opt-in,
  without removing its configuration.
- [x] Email detail enrichment returns disabled unless
  `BRIGHTDATA_EMAIL_DETAIL_ENRICHMENT_ENABLED=true` and both existing Bright
  Data credentials are present.
- [x] Public tests cover disabled, enabled, and missing-credential cases with
  fictional environment values.
- [x] Public configuration documentation describes how to resume deliberately.

## Design and constraints

Profile composition decides whether a direct source adapter runs, so suspension
belongs in the ignored profile/default `sources` list rather than only in the
runtime source table. `BRIGHTDATA_DIRECT_COLLECTION_ENABLED` is a second,
default-off circuit breaker for an accidentally selected source, the legacy
`--enable-indeed` override, and the one-off live E2E utility. Email detail enrichment previously used credential
presence as its only activation condition; a separate explicit flag avoids
accidental paid requests when credentials remain configured. The flag defaults
to disabled and accepts only the literal case-insensitive value `true`.

## Verification

Run focused offline tests plus the repository formatting, lint, type, and
public-test gates. An independent user-path simulation is required because this
changes a documented configuration and activation contract.

## Follow-ups

When the provider is stable, restore `indeed_brightdata` to each intended
profile's effective `sources` list, enable its runtime source table, and set
`BRIGHTDATA_DIRECT_COLLECTION_ENABLED=true`. Set
`BRIGHTDATA_EMAIL_DETAIL_ENRICHMENT_ENABLED=true` separately and deliberately.
Do not restore email detail enrichment through an implicit credential-only
check.
