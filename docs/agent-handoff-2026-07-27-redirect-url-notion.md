# Agent handoff: resolved application URLs into Notion

Date: 2026-07-27  
Repository: `ZerrorLiu/job-scraper`  
Branch: `main`  
Latest pushed commit: `29dbe51 feat: prepare accepted jobs in browser batches`

## User decision

The next implementation phase should improve the three active source families
(LinkedIn, Indeed/Bright Data, and Email) so Notion receives the real application destination whenever it can be
confirmed, rather than treating the original listing URL as the application
URL.

Source policy:

- LinkedIn: automate the listing CTA and follow the external company/ATS
  destination. Fill and upload only from confirmed private runtime data.
- Indeed: do not automate Indeed forms or submissions. Use Indeed for
  discovery only. If a reliable external company/ATS URL is available from
  Bright Data, pass only that URL to the external-site application flow.
- Email: preserve Email as the source. An email recommendation may contain a
  link hosted by eFinancial Careers, Indeed, LinkedIn, or another job platform;
  the host is a platform detail, not a fourth active source. Do not automate
  Email-hosted eFinancial Careers or Indeed pages. Resolve a confirmed
  external company/ATS destination only when the email link or a permitted
  enrichment step provides one.

The original listing URL must remain available as `Job URL` for audit and
deduplication. It must not be mislabeled as `Apply URL` when no external
destination has been confirmed.

## Required outcome

For each job, distinguish these values:

```text
source_url                 original listing/discovery URL
resolved_application_url   confirmed company career page or ATS URL
```

Notion behavior:

- `Apply URL` points only to `resolved_application_url`.
- `Job URL` points to `source_url`.
- If no external destination is confirmed, `Apply URL` is empty or `N/A`; do
  not silently fall back to the original listing URL.
- The title link should use the resolved destination only when one exists.
- Deduplication must consider both URLs without confusing a source URL with an
  application URL.

## Current code facts

Important files:

- `src/job_scraper/domain/models.py`: `RawJobRecord.application_url` and
  `JobRecord.application_url` currently exist, but the field is overloaded as
  both source fallback and application destination.
- `src/job_scraper/collectors/data_integration_adapter.py`:
  `normalize_upstream_entry()` currently searches
  `reference_url`, `url`, `job_url`, `jobUrl`, `apply_link`, and other names in
  one list. This can conflate a listing URL with an application URL.
  `_to_raw_job()` currently assigns the normalized reference URL as both
  `source_url` and `application_url`.
- `src/job_scraper/collectors/linkedin.py`: direct LinkedIn collection starts
  with the listing URL and currently defaults `application_url` to it.
- `src/job_scraper/adapters/sinks/notion_payload.py`: `build_job_title()`
  falls back from `application_url` to `source_url`; `build_children()` emits
  both `Apply URL` and `Job URL`.
- `src/job_scraper/adapters/sinks/notion_daily.py`: existing-page matching
  considers application, source, and canonical URLs.
- `src/job_scraper/browser/chrome_cdp.py`: real browser inspection already
  follows external `_blank` application destinations and records the final
  URL privately.
- `src/job_scraper/browser/session.py`: real long-running LinkedIn/ATS batch
  preparation exists. It is not the Indeed/eFinancial automation path.

Existing public tests include:

- `public_tests/test_indeed_application_url.py`
- `public_tests/test_browser_inspection.py`
- `public_tests/test_application_batch.py`
- `public_tests/test_application_session_state.py`

## Bright Data direction

Do not assume the current standard Indeed dataset returns the external
application destination. The local stored Indeed payload inspection on this
handoff found no reliable application URL field in recent normalized records.

Implement two distinct extraction paths:

1. Standard payload compatibility: recognize possible fields such as
   `external_application_url`, `application_url`, `apply_link`, and known
   nested equivalents, but keep them separate from the listing URL.
2. Bright Data custom scraper: configure a scraper that opens the Indeed job
   detail page, locates the application CTA, and returns the external `href` or
   final browser `location.href`. Store this as an untrusted candidate until it
   passes URL validation and an explicit external-destination policy.

Bright Data documentation:

- [Scraper Studio interaction functions](https://docs.brightdata.com/datasets/scraper-studio/functions)
  supports navigation, clicks, and current browser location.
- [Scrapers Library quick start](https://docs.brightdata.com/datasets/scrapers/scrapers-library/quickstart)
  describes output schema selection and asynchronous batch collection.
- [LinkedIn Jobs collect by URL](https://docs.brightdata.com/api-reference/scrapers/social-media-apis/linkedin-jobs-collect-by-url)
  documents LinkedIn's `apply_link` field; do not assume the same field exists
  in the Indeed dataset.

The custom Bright Data output should use an unambiguous name such as
`external_application_url`, not `url`, so the normalizer cannot overwrite the
original listing URL.

## URL safety and confidence rules

- Accept only public HTTPS URLs.
- Reject credentials, localhost, loopback, private IPs, data URLs, and
  javascript URLs.
- Treat a URL as resolved only when it is visibly an external company/ATS
  destination or a Bright Data field explicitly documents it as such.
- Do not infer an external URL from company text, job-description text, or an
  unverified redirect parameter.
- Keep the original URL and resolution evidence in `raw_payload` or private
  evidence; never commit real payloads or screenshots.
- A failed or blocked Indeed/eFinancial resolution is a normal unresolved state,
  not an exception that should fabricate an apply link.

## Suggested implementation sequence

1. Update the public spec and domain contract to separate source and resolved
   application URLs.
2. Add a pure URL-resolution value/normalization helper with fictional tests.
3. Update LinkedIn normalization and browser inspection to persist the final
   external destination without overwriting `source_url`.
4. Update the Bright Data normalizer and `_to_raw_job()` to preserve distinct
   listing and external URL fields.
5. Add the custom Indeed Bright Data output contract/fixture. Do not run it
   against private credentials in the default test suite.
6. Keep Indeed and Email-hosted eFinancial application pages out of the
   automatic form runner. Only enqueue a confirmed external destination for
   the supported company/ATS flow.
7. Update Notion payload generation and existing-page matching. Add tests for:
   resolved URL, unresolved URL, source-only URL, duplicate matching, and
   unsupported source policy.
8. Run the required gates and perform a small real LinkedIn/ATS validation.

## Verification commands

From `C:\Users\zach\Desktop\Positions`:

```powershell
uv run ruff format --check .
uv run ruff check .
uv run pyright
uv run pytest
```

Live validation must separately prove:

- LinkedIn CTA reaches the external destination.
- Indeed Bright Data returns an external URL when the custom field is
  available, without submitting or filling Indeed.
- Email remains the source for email recommendations, including links hosted by
  eFinancial Careers; those platform pages remain discovery-only.
- Notion `Apply URL` is the resolved URL and `Job URL` remains the original.

## Current runtime and browser state

Private runtime files are outside the repository and must not be copied into
source control. The previous live batch opened 20 tabs and left them paused for
manual review. Do not restart or interact with those tabs unless the user asks;
the next implementation should not automate Indeed or eFinancial.

For the current development phase, use only the latest explicitly selected
source batches. The latest local batches observed on this handoff were Email
(244 records), LinkedIn (3 records), and Indeed (40 records). Do not use the
historical 1,000+ rows for Notion refresh or live testing; keep them for audit
only. A future refresh command should require a source plus batch timestamp or
an equivalent explicit selector.

The repository working tree before creating this handoff had only the ignored
`outputs/` directory untracked. Never commit `.env`, runtime facts, documents,
browser profiles, cookies, tokens, databases, screenshots, or application
payloads.

## Non-goals

- CAPTCHA solving or bypass.
- Automatic submission on any platform.
- Cookie/password extraction or replay.
- Treating a platform listing URL as an application destination merely because
  it is the only URL available.
