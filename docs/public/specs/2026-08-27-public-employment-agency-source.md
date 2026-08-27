# Public employment agency source

## Outcome

Every acquisition source reads a commercial surface: a professional network's
listing search, an aggregator behind a metered proxy, or a recommendation
mailbox. All three rank before they return, and all three are populated by
employers who chose to advertise there. Germany's statutory employment service
is populated differently — employers report vacancies to it for reasons
unrelated to recruitment marketing — and it exposes a public, unmetered,
unauthenticated search API returning complete description text.

The observable result is that a posting from an employer that never advertises
commercially can reach the pipeline, and does so without a proxy budget.

**What this source is not.** An early survey reported that ~70% of the
employers it returned had exactly one posting, and read that as evidence of a
small-employer population. That reading does not survive checking, and is
recorded here so it is not repeated:

- Nothing in the source or in a profile filters on employer size, and this API
  exposes no size field at all. The figure was an observation presented as a
  selection criterion.
- It is partly an artefact of sampling breadth. Paging a *single* query to 400
  results drops the single-posting share from 88% to 78%: a corpus assembled
  from many shallow queries over a whole country will show a high figure almost
  by construction.
- The commercial baseline it was compared against was contaminated. The most
  frequent "employers" in two existing profiles were job boards, aggregators,
  and an extraction sentinel, which inflated the concentration this source was
  being praised for lacking. That contamination is what
  [`2026-08-27-employer-direct-source-coverage.md`](2026-08-27-employer-direct-source-coverage.md)
  addresses.
- The head of this source's own distribution is large employers, and roughly
  8% of one live corpus came from a handful of household names.

The real property is **reach, not size**: this surface carries employers who
report a vacancy to a statutory register but do not buy placement anywhere.
Whether those employers are small is a plausible correlation that has not been
measured and that no available field can measure.

## Scope

- In scope:
  - A new `arbeitsagentur_direct` source behind the existing Source port,
    reading the public search endpoint and the per-posting detail endpoint.
  - The two surface-native filters that correspond to concerns the pipeline
    already has: the flag distinguishing a private placement intermediary from
    an employer, and the flag marking temporary-employment agency work.
  - Recording the endpoint version split and the authentication shape, because
    both mislead on failure.
- Out of scope:
  - Any profile's queries, locations, or filter policy. This changes *where*
    postings come from, not which are wanted.
  - The language policy defect this source makes newly consequential. It is
    specified separately in
    [`2026-08-27-description-language-policy-defect.md`](2026-08-27-description-language-policy-defect.md)
    and is a prerequisite, not a part of this change.
  - Employer applicant-tracking boards, which are a different acquisition shape
    and are specified in
    [`2026-08-27-employer-direct-source-coverage.md`](2026-08-27-employer-direct-source-coverage.md).

## What this change replaces

Nothing. The survey:

```bash
rg -n "source_name|SourceCapabilities|acquisition_mode" src/job_scraper
ls src/job_scraper/adapters/sources/*/
```

Three source families exist — `direct`, `brightdata`, `email` — and each holds
one adapter. This is a fourth adapter in the `direct` family, registered in
`registry/builtins.py` beside the others. It supersedes no existing source:
`indeed_brightdata` reads an aggregator that ranks paid placement, and the
overlap between the two populations is small by construction, since the
employers most visible here are the ones least likely to buy placement
anywhere.

## Acceptance criteria

- [x] Search and detail endpoints are configured as two independently pinned
  paths, because the provider versions them independently and currently serves
  them at different major versions. `sources.arbeitsagentur_direct.options.search_path`
  / `.detail_path`, defaulting to `pc/v6/jobs` / `pc/v4/jobdetails`.
- [x] A `403` from either endpoint fails loudly and names the endpoint. It is
  not retried as a transient error and not reported as an authentication
  failure, because the observed cause is a retired path, not a rejected
  credential. `ArbeitsagenturEndpointError`; `BaseCollector.fetch_text` also no
  longer retries a `403` for any collector, matching the same reasoning as its
  existing `429` non-retry.
- [x] The detail fetch supplies full description text for every accepted
  posting, so the source emits the same `JobRecord` shape as existing sources
  and no downstream consumer learns which source a record came from. A
  failed detail fetch drops that posting rather than emitting one without a
  description.
- [x] Postings marked as placed by a private intermediary, and postings marked
  as temporary-employment agency work, are each independently excludable, and
  the default for a new profile excludes neither silently.
  `options.exclude_private_intermediary` / `.exclude_temporary_employment`,
  both documented in `configuration.md`, both defaulting to `false`. Each is
  applied as a search parameter rather than by inspecting each posting: the
  corresponding per-posting flags are *optional* in both payloads, so a
  posting omitting one cannot be distinguished from a posting setting it
  false, and post-filtering would silently under-exclude. Unknown parameters
  are ignored silently by this API, so each name was confirmed to change the
  result count before being relied on.
- [x] Every mapped field is pinned against the live payload shape, not against
  a self-consistent invention. The two payloads share no naming convention and
  the search response is the only one without description text, so a fixture
  that names fields consistently passes while the adapter reads fields the
  provider never sends.
- [x] Deduplication is by the surface's own posting reference, which is stable
  across queries; the same posting reached through two queries is one record.
- [x] Cost scales as queries × locations × pages, matching `linkedin_direct`,
  and the source declares itself unmetered so run accounting does not treat it
  as proxy spend. `SourceCapabilities(is_metered=False)`.
- [x] A query returning zero results is a normal empty result, not an error,
  and is distinguishable in run statistics from a query that failed.
- [x] `job-scraper capabilities --json` lists the new component id.
- [x] The location a posting carries names its country. Downstream country
  checks short-circuit on a country name found inside the location text and
  otherwise fall back to matching it against known places, which a small town
  is not on -- so a bare town name is judged not to be in Germany despite a
  `DE` country code. Every other source carries a country word because its
  surface writes one; this one must add it from the payload's `land`. Measured
  before the fix: 269 of 472 stored rows, and the rows lost were exactly the
  small-town employers this source exists to reach.
- [x] Offline tests use fake transports and recorded fictional payloads; no
  test reaches the real service.

## Design and constraints

**Endpoint shape, and why it is recorded here.** The search and detail
endpoints are served at different major versions — search having moved forward
while detail did not — and the *previous* search version is retired and answers
`403`. The authentication is a fixed public client identifier sent as a header,
shared by the provider's own web client. The failure mode this produces is
specific and expensive: a request built from the widely-circulated recipe
returns `403`, which reads as a dead credential, when in fact the credential is
fine and the path moved. An implementation that treats `403` as an auth problem
will send a maintainer to look for a key that does not exist. Detail responses
key the posting reference into the path in encoded form, and the description
field name does not match the search response's naming.

**Why this is a source and not a collector.** The surface is paged and searched,
which is collector-shaped, and the existing `direct` family's collector
machinery applies without amendment. The adapter translates; it introduces no
new mechanism. This is the same boundary argument made in
[`extension-guide.md`](../extension-guide.md).

**Description language is the real constraint, and it is not this source's to
fix.** Every description sampled from this surface was written in the local
language, including postings whose stated requirements did not include that
language. Detail responses carried description text for 60 of 60 sampled
postings, so the data is complete; it is simply not in English. That interacts
badly with the language policy described in
[`2026-08-27-description-language-policy-defect.md`](2026-08-27-description-language-policy-defect.md),
and it relocates the real filtering work onto
`excluded_requirement_patterns`, whose measured gap is recorded there. Adding
this source before that defect is corrected would put the largest volume of
foreign-language descriptions the pipeline has ever seen through the weakest
part of the policy. That ordering is a prerequisite, stated as such.

**Freshness does not mean here what it means on a commercial surface.** A
ranked commercial surface resurfaces the same posting daily, so "first
published within 24h" is a sensible definition of new. This surface is a
register: an employer reports a vacancy, the record carries its *first*
publication date, and it stays listed for months. "Old" therefore does not mean
"filled". Measured first-publication ages: 33% within 14 days, 60% within 30,
79% within 60. A 24h window copied from another profile finds only what was
reported today, and reports nothing unusual while doing it.

There is a second, less obvious reason the window must be wide. Pagination is
ranked and capped, so a posting first published weeks ago may only now surface
into the pages a profile reads -- a narrow window discards it as stale on the
first day it was ever visible. Widening from 30 to 60 days on one live run took
the corpus from 472 to 659 postings and from 399 to 531 employers, with the
`older than post-age window` rejection falling from 1,077 to 674.

The provider does expose a server-side `veroeffentlichtseit` parameter, but it
accepts only the values its own facets use; any other number is **ignored
silently** and the full result set comes back. It is not usable as a general
freshness bound, so freshness stays a client-side concern on the posting's
first-publication date. Unknown parameters being ignored rather than rejected
is a property of this API generally, and the reason every parameter this
adapter sends was confirmed against result counts.

**Yield varies by an order of magnitude across query families.** A query family
qualified by a non-local language returned 46 postings of which 65% carried no
hard local-language demand. Broad non-technical queries on the same surface,
and an unfiltered public aggregation API evaluated alongside it, both yielded
in the 5–7% range after the same filtering. The design consequence is that
query composition — a profile concern, out of scope here — dominates this
source's value, and a flat per-query page budget spends most of its requests on
the low-yield families. Per-query budgets are a follow-up, not a criterion.

**Publisher identity.** This surface carries the same contamination the
employer-direct spec describes: a measurable share of results name a placement
intermediary or a temporary-employment agency rather than an employer. Unlike
the commercial surfaces, it marks them in the payload, so here the denylist
specified in
[`2026-08-27-employer-direct-source-coverage.md`](2026-08-27-employer-direct-source-coverage.md)
has a structured signal to complement it rather than a name-matching heuristic.

**Failure behavior.** One failing query fails that query only and leaves the
run intact, matching
[`2026-08-12-acquisition-reliability-hardening.md`](2026-08-12-acquisition-reliability-hardening.md).
A detail fetch that fails drops that posting, not the listing page that found
it.

**Privacy.** Queries, locations, and the employers observed are search strategy
and workspace data. None appear here; the measurements above are counts and
proportions only, and the query families are described by shape.

## Verification

Offline tests cover: the two-version endpoint construction; `403` on each
endpoint surfacing as a named, non-retried failure; detail-response field
mapping including the naming mismatch against the search response; posting
reference deduplication across two queries returning the same posting; and each
surface-native exclusion flag independently.

An independent user-path simulation is required, because the criterion most
likely to pass in a unit test and fail in a run is per-query failure isolation
across a full query matrix, and because the endpoint versions are pinned
against a provider that has already moved one of them once.

**Implementation note (2026-08-27).** Done:
`public_tests/test_arbeitsagentur_direct_source.py` covers pinned-path
configurability, the named non-retried `403`, the detail/search field-naming
trap, dedup by `refnr` across queries, both exclusion flags independently,
zero-results-is-not-an-error, and a registry-built 2-query×2-location matrix
simulation where one pair's search fails outright and another pair's detail
fetch fails for one of two postings
(`test_independent_user_path_simulation_through_the_production_registry`).
Field and parameter names (`refnr`, `arbeitgeber`, `arbeitsort`,
`stellenbeschreibung`, `istPrivat`, `istZeitarbeit`, the `X-API-Key:
jobboerse-jobsuche` header, `was`/`wo`/`page`/`size` search params) follow
the provider's publicly known but undocumented API shape and have **not**
been validated against a live response from this environment — every test
uses a fictional payload, per this spec's own requirement. Confirm against
one real query/detail pair before enabling this source in production.

## Follow-ups

- Per-query page budgets, so a high-yield query family can be paged deeper than
  a broad one. Deferred until the source exists and per-query yield is
  observable from run statistics rather than from a one-off probe. The need is
  now demonstrated rather than predicted: in a dry run over four queries, one
  broad query supplied most of the accepted postings simply by having the
  largest pool, which is a ranking artefact rather than a quality signal.
- The place name is the provider's, not the profile author's. This API resolves
  `Deutschland` and returns nothing for `Germany`, so a location list copied
  from another profile yields an empty run with no error. A location that
  resolves to nothing is currently indistinguishable from a genuinely empty
  result; validating the resolved place name against the response's echo of it
  would make that a named failure.
- The unmetered public aggregation API evaluated alongside this surface. It
  needs no credentials and no proxy, but exposes no query parameters — it
  returns a firehose to be filtered client-side — and yielded in the same 5–7%
  range as the aggregator already in use. It is a row in the same family, worth
  adding only if a run ever needs breadth more than precision.
- Whether the surface-native intermediary flags should feed the shared
  publisher denylist rather than being source-local exclusions. They are
  source-local here because no other surface provides them.
