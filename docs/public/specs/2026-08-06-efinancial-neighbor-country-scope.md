# eFinancial neighboring-country location scope

## Outcome

Email recommendations from eFinancialCareers may be accepted for Germany and
Germany's land-bordering countries, while an explicitly non-target location
such as Singapore is rejected. Other acquisition sources keep their existing
country scope.

## Scope

- Apply the expanded location scope only when the source URL is an
  eFinancialCareers URL.
- Treat the following ISO country codes as in scope for eFinancialCareers:
  `DE`, `NL`, `BE`, `FR`, `LU`, `AT`, `CH`, `CZ`, `PL`, and `DK`.
- Infer a country from explicit location text before allowing an unknown
  country through the incomplete-metadata fallback.
- Do not use unrelated jobs mentioned elsewhere in an email's recommendation
  context to classify the selected job.
- Existing database and Notion rows are not rewritten automatically; the
  corrected rule applies to new ingestion or an explicit reprocess.

## Acceptance criteria

- A selected eFinancialCareers job in Amsterdam, Brussels, Paris, or another
  listed neighboring-country location can pass the country step.
- A selected eFinancialCareers job in Singapore fails the country step even if
  an upstream/default country field says `DE`.
- A non-eFinancial job in Singapore fails the existing Germany-only filter.
- An email body that merely mentions Singapore in other recommendations does
  not change the selected eFinancial job's location.
- Tests use fictional URLs and payloads only.

## Verification

- Focused offline tests cover the source-specific policy and the explicit
  city/country mismatch.
- The repository format, lint, type, and offline test gates are run before
  handoff.
