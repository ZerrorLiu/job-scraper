# Employer-direct source coverage

## Outcome

Every built-in acquisition source reads a *search or aggregation surface*:
a listing search, a proxied aggregator, or a recommendation mailbox. Those
surfaces share a failure mode — a large and measurable share of what they
return does not name an employer at all. The company field holds a job board,
a staffing agency, or a crowd-work platform that re-listed someone else's
posting. Downstream, a screener cannot tell those apart from employers, and a
publication sink writes them into the daily table as if they were.

Two observable results:

1. A job whose company names a publisher rather than an employer is rejected by
   the pipeline, for every source, under a reason that says so.
2. Acquisition can read employer applicant-tracking boards directly, so a
   posting reaches the pipeline without a search surface having to rank it
   first.

## Scope

- In scope:
  - A configured publisher denylist evaluated by the existing `company`
    pipeline step.
  - A new `ats_direct` source reading public applicant-tracking board
    endpoints for a configured list of board tokens.
  - Repairing `company_name` extraction in the email channel, where card body
    text currently leaks into the field.
- Out of scope:
  - Any search-engine surface as an acquisition source. The reasoning is
    recorded under [Design and constraints](#design-and-constraints) so the
    question is settled rather than reopened.
  - Discovering board tokens automatically. The token list is workspace
    configuration, supplied the same way profiles and watchlists are.
  - Changing any profile's queries, target roles, or filter policy. This
    changes *where* postings come from, not *which* postings are wanted.

## What this change replaces

`is_publisher_company` in `integrations/email_recommendations.py` — a hardcoded
three-value set, private to one channel, consulted at two call sites. It is
deleted here rather than left beside the configured policy, and its values move
into the configuration reference as an example denylist.

That function is the only existing home for the concept. The survey:

```bash
rg -n "aggregator|publisher|denylist|excluded_compan" src public_tests docs
```

`CompanyStep` is the only place company identity is judged, and it implements
an allowlist only (`filters.company_names` → `policy.allowed_companies`). There
is no denylist anywhere, and the one publisher check that exists sits below the
pipeline, inside an adapter, where only one source can reach it. Extending the
step is therefore the change; a new step is not.

The survey also found `adapters/jobposting_jsonld.py`, a shared schema.org
`JobPosting` extractor already used by both the LinkedIn collector and the
email channel. `ats_direct` reuses it for providers that serve JSON-LD rather
than JSON, and a new extractor is not written.

Nothing else is superseded. `linkedin_direct`, `indeed_brightdata`, and
`email_imap` keep their current behavior; this adds a fourth source beside
them.

## Acceptance criteria

- [x] `[filters] excluded_company_names` rejects a job whose company matches a
  configured publisher, under a `RejectionReason` distinct from
  `COMPANY_NOT_ALLOWED`, so the two are separable in run statistics.
  `RejectionReason.COMPANY_IS_PUBLISHER` (`domain/decisions.py`).
- [x] The denylist is evaluated for every source, including sources that do not
  exist yet, because it lives in the step and not in an adapter. Checked
  first in `CompanyStep.evaluate` (`pipeline/steps.py`), ahead of the
  allowlist, so a publisher veto wins even over a matching allowlist entry.
- [x] Matching is case- and whitespace-insensitive, and does not match a
  publisher name occurring as a substring of a longer employer name.
  `company_matches_denylist` (`pipeline/role_filter.py`) is exact match on
  normalized text, deliberately not the word-boundary substring helper
  `company_matches_allowlist` already uses.
- [x] An empty denylist is the default and changes no existing behavior.
- [x] `is_publisher_company` no longer exists, and the email channel gets the
  same rejection through the pipeline as every other source. Its old
  three-value set is documented as an example `excluded_company_names` list
  in `configuration.md` instead. Separately, `infer_company`'s generic
  fallback now rejects a captured candidate that looks like role text, a
  salary figure, a `(m/w/d)`-style marker, or a call-to-action phrase
  (`looks_like_malformed_company`, `integrations/email_recommendations.py`) —
  a different defect than publisher-name leakage, fixed alongside it since
  both land in the same `company_name` field.
- [x] `ats_direct` issues one request per configured board token, so cost
  scales with the configured company count and not with the query matrix.
- [x] A token that is unknown to its provider, returns an error, or returns a
  payload shape the adapter does not recognize, fails that token only and
  leaves the rest of the run intact.
- [x] `ats_direct` emits the same `JobRecord` shape as existing sources,
  including full description text, so no downstream consumer learns which
  source a record came from.
- [x] Provider support is a table of provider id → endpoint template → payload
  adapter. Adding a provider adds a row and an adapter; it does not add a
  source or a configuration section. `_PROVIDERS` in `collectors/ats.py`, two
  rows: `personio` (native XML feed) and `jsonld` (reuses
  `adapters/jobposting_jsonld.py`).
- [x] The email channel no longer emits a `company_name` containing role text,
  a salary figure, an `(m/w/d)`-style marker, or a call-to-action phrase.
- [x] `job-scraper capabilities --json` lists every new component id.
- [x] Offline tests use fake transports and fictional board tokens; no test
  reaches a real board.

## Design and constraints

Dependency direction is unchanged. `ats_direct` is a Source adapter behind the
existing Source port, registered in `registry/builtins.py`, constructed by the
composition root, and configured only in the private workspace. The extension
workflow in [`extension-guide.md`](../extension-guide.md) applies without
amendment; this spec adds no mechanism, only an adapter and a policy key.

**Why a denylist belongs in the pipeline, not in an adapter.** The same
publisher reaches acquisition through more than one surface — a board that
re-lists a posting also buys placement on the search surface that a different
source reads. A check that lives in one adapter therefore has to be written
again in the next one, and the two copies drift. `CompanyStep` already owns the
question "is this company acceptable", and answering "no, because it is not an
employer" is the same question.

**Why a source rather than a collector.** Collectors are the fixture-and-search
machinery for surfaces that must be paged through. An applicant-tracking board
is not paged: a board token maps to one endpoint returning that employer's
whole current list. There is nothing to collect, only to translate, which is
what the adapter boundary is for.

**Cost shape.** These endpoints are public, unauthenticated, and return
structured payloads, so a run costs one request per configured employer. The
existing search-surface sources cost pages × queries and return a ranked subset
of what exists. The two are complements, not substitutes: the search surfaces
find employers that are not yet on the token list, and the token list returns
everything from employers already known.

**Provider validation (measured 2026-08-27).** The assumptions above were
probed live against one provider — the applicant-tracking system most common
among German small and mid-sized employers — using eight board tokens gathered
by hand. All eight served a structured feed at the provider's documented
per-board path, with no credential, no rate limiting encountered, and no
challenge: 8 of 8 reachable, 51 open positions in total. The payload carries
the identifiers, employment type, seniority, schedule, category, and creation
timestamp the pipeline needs, and full description text of 2–5 KB. One trap is
worth recording: the description is a nested element containing titled
sections, not a text node, so a flat text read returns an empty string rather
than failing.

Two findings qualify the value. First, the population is what this spec
predicts — every board was a small employer, and the non-technical share was
41 of 51. Second, and against the framing that motivated the survey, all 36
descriptions carrying text were written in the local language. These boards
are a small-employer channel, not a foreign-language-friendly one; those are
independent properties and only the first is what the token list buys. The
language consequence is specified in
[`2026-08-27-description-language-policy-defect.md`](2026-08-27-description-language-policy-defect.md).

**Why no search engine is an acquisition source.** Three different things get
called "searching the web", and they fail differently:

- *Scraping a general web search engine.* There is no supported interface, the
  result is blocked or served a challenge under automation, and it is against
  the terms of the service being read. Not viable. Confirmed in passing during
  the provider validation above: the token-discovery query was served a
  human-verification challenge on first request, and the tokens were only
  obtained once a person cleared it. That is exactly the boundary this spec
  draws — a person doing discovery, not a source doing acquisition.
- *A paid programmable-search API.* Viable but quota-bound, and it returns a
  ranking derived from a restricted index rather than the public one — recall
  is not what the query appears to promise, and the discrepancy is invisible
  from the results.
- *An aggregated jobs surface reached through the existing proxy vendor.*
  Technically viable. It aggregates the same applicant-tracking boards this
  spec reads directly, at a per-request cost, without full description text,
  and with a second deduplication problem against the direct read.

A search engine remains useful for *discovering board tokens* — a one-off
activity performed by a person against workspace configuration, on the order of
once a quarter. That is a way of writing a config file, not a source, and it
does not appear in `registry/builtins.py`.

**Failure behavior.** An unknown provider id is a configuration error raised at
build time, before any request. A board returning HTML where JSON was expected
is an adapter-boundary rejection for that token, converted to a `Decision`, and
recorded — not an exception that ends the run. This matches the reliability
bounds in
[`2026-08-12-acquisition-reliability-hardening.md`](2026-08-12-acquisition-reliability-hardening.md).

**Privacy.** Board tokens name the employers a particular person is targeting,
which is search strategy. They live in the ignored workspace beside profiles
and watchlists, and no token, employer name, or denylist entry drawn from a
real installation may appear in this repository, its tests, or its fixtures.

## Verification

Offline tests cover: the denylist's matching rules including the substring
case; the empty-denylist default; one provider adapter per supported provider
against a recorded fictional payload; the per-token failure isolation; and the
email `company_name` repair against the specific malformed shapes that motivated
it. The full quality gate in [`AGENTS.md`](../../../AGENTS.md) applies.

An independent user-path simulation is required for `ats_direct`, because the
per-token failure isolation is the criterion most likely to pass in a unit test
and fail in a run.

**Implementation note (2026-08-27).** Done: `public_tests/test_ats_direct_source.py`
covers the denylist, both provider rows, per-token isolation for connection
errors/HTTP errors/malformed payloads, and a registry-built simulation with
three failure modes plus one success in a single run
(`test_independent_user_path_simulation_through_the_production_registry`).
The `personio` provider's field names (`position`, `jobDescriptions`,
`office`, `createdAt`, the `{token}.jobs.personio.de/xml` and `/job/{id}`
URL shapes) follow the provider's publicly known but undocumented XML
schema and have **not** been validated against a live board from this
environment — every test uses a fictional payload, per this spec's own
requirement. Confirm against one real board token before enabling
`provider = "personio"` in a production profile.

## Follow-ups

- A generic careers-page reader for employers whose board is not on a supported
  provider, reusing `adapters/jobposting_jsonld.py`. Deferred until the
  provider table is in place, because most configured employers will be on a
  provider and the generic path needs a per-site failure policy that the
  provider path does not.
- A public board API with no per-employer token, added as one more provider
  once the provider table exists. It is a row, not a source, which is the point
  of the table.
- Board-token discovery stays manual *by choice*, not by necessity. Board
  tokens can in fact be enumerated in bulk and for free from a public web-scale
  crawl index, which accepts a subdomain wildcard and returns every crawled URL
  under a host — measured, one crawl yielded thousands of tokens across the
  supported providers. What the measurement also showed is that having the
  list does not help: the boards it enumerates carry the wrong populations for
  the roles a track like this one selects. The reason not to automate discovery
  is therefore yield, not feasibility.
  [`2026-08-27-token-free-board-sources.md`](2026-08-27-token-free-board-sources.md)
  carries both measurements and adds the sources that need no per-employer
  configuration. This source remains the cleanest way to *read* an employer
  already configured.
