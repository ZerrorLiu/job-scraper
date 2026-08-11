# Reliable Metadata for Email Job Links

## Outcome

Email-ingested jobs from a supported job-board link should retain the job's
own company and location metadata. A publisher name or a location found in an
unrelated part of the email must not be stored as the job's metadata.

## Scope

- In scope: link-specific candidate extraction, redirect-aware detail
  retrieval, structured and visible-HTML detail parsing, conservative metadata
  fallback, existing-record metadata repair, and duplicate handling for
  tracking links.
- Out of scope: live mailbox access, credential changes, browser automation,
  and changes to unrelated email providers.

## Acceptance criteria

- [x] A supported financial-jobs-board URL is parsed for its title/location
      hints before sender-domain fallback is considered.
- [x] The final URL after an HTTP redirect is retained as detail provenance and
      is used for platform-specific parsing.
- [x] A missing company or location does not cause the publisher name or an
      unrelated email-wide location to be stored as the job value.
- [x] Detail status does not report complete metadata when the detail page only
      yielded a description.
- [x] The visible company/location row below an HTML job heading is parsed when
      structured metadata is absent.
- [x] When a detail page is unavailable, the selected email card can split its
      company from the URL-anchored city and retain the card's full location.
- [x] Reprocessing can update an existing job's company and country by its
      source or canonical URL when the new detail response is more complete.
- [x] Tracking-link variants do not create separate identities for the same
      canonical job when a stable job URL is available.
- [x] Offline, credential-free regression tests cover the original symptom.

## Design and constraints

Keep the behavior at the email adapter boundary. Use a pure URL metadata helper
for the supported board's public slug convention, pass the final redirect URL
into the existing detail parser, and preserve the generic path for other
providers. Prefer an empty/unknown field over a value with a known wrong
provenance. The implementation must not store mailbox contents, credentials,
or user-specific search strategy in public code or tests.

## Verification

- Run a focused offline regression test with fictional message data and a
  non-networking detail fixture.
- Run the configured formatting, lint, type, and public test gates.
- Inspect the resulting diff and confirm no private runtime data is added.

## Follow-ups

If a future board-specific page format cannot expose a reliable company field,
add an explicit adapter-level metadata status rather than reusing sender or
email-wide inference.
