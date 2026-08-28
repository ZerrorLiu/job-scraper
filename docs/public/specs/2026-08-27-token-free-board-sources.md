# Token-free board sources

## Outcome

`ats_direct` reads an employer's board by its board token — the employer's
public slug on an applicant-tracking system. It is the cleanest read available,
and it cannot find an employer that is not already configured. Measurement
(2026-08-27) established that the token for a given employer cannot be
discovered automatically:

- No board host publishes an index of its own boards. Two serve a marketing
  page at their root with zero board links, one serves an application shell
  where a sitemap would be, and one — the system most common among German small
  employers — has no parent host at all: the bare hostname does not resolve in
  DNS, while a per-employer subdomain of it does.
- Acquisition's own stored URLs cannot supply tokens either. Across four
  installed databases, no `apply_url`, `canonical_url`, `source_url`, or
  `company_url` value pointed at any board host; every one pointed back at the
  search surface that found it.
- A public web-scale crawl index *can* supply them. Its URL-index API accepts
  a subdomain wildcard and returns every crawled URL under a host, which yields
  board tokens in bulk, free and unauthenticated — one crawl produced thousands
  across the supported providers. This is recorded because the first two
  findings read as "discovery is impossible", and it is not; see
  [Design and constraints](#design-and-constraints) for why the list is still
  not worth having.

So a token list can be built. What it cannot do is find employers hiring for
the roles a track selects, which is a property of who uses each board rather
than of how the list is obtained.

The result: acquisition gains sources that need no per-employer configuration.
Each reads a public board that can be queried or paged without naming a company
first, so new employers arrive without anyone maintaining a list.

## Scope

- In scope:
  - `workable_direct`: a token-free search across one applicant-tracking
    vendor's hosted boards, taking a query and a location.
  - `arbeitnow_direct`: a token-free national board feed, paged in full.
  - `berlinstartupjobs_direct`: a token-free regional startup board, paged in
    full through its content API.
- Out of scope:
  - Removing or disabling `ats_direct`. It stays registered and correct; this
    spec changes what it is *for*, which is recorded in its own spec's
    follow-ups rather than duplicated here.
  - A search-engine surface. Unchanged from
    [`2026-08-27-employer-direct-source-coverage.md`](2026-08-27-employer-direct-source-coverage.md).
  - Any change to filter policy, profiles, or which postings are wanted.

## What this change replaces

Nothing is deleted. One standing decision is corrected: that spec's follow-up
"Board-token discovery stays manual. If the configured list outgrows what a
person will maintain by hand, revisit it as a workspace tool — still not as a
source" described a list that grows. The measurement above shows the list
cannot grow without a person, and the use it was written for does not reward
re-reading a known board. That follow-up is rewritten in place to point here,
so the two specs do not state opposite things.

The survey for an existing home:

```bash
rg -n "board|arbeitnow|workable|token.free" src public_tests docs
ls src/job_scraper/collectors/
```

`collectors/` holds one module per external service — `linkedin.py`,
`arbeitsagentur.py`, `ats.py`. `arbeitsagentur.py` is the closest existing
shape: a public, unauthenticated, paginated search read with a query/location
matrix. These three are the same kind of thing against different services, so
they are three more modules in that directory, not a new subsystem.

## Acceptance criteria

- [x] Each source is a separate registered component id, independently
  enabled, paged, and paced. Turning one off does not require editing another's
  configuration.
- [x] `workable_direct` builds its requests from `search_queries` × `locations`
  and follows the vendor's opaque page cursor until `max_listing_pages` or the
  cursor runs out, whichever comes first.
- [x] `workable_direct` emits full description text assembled from the
  posting's description and its requirements and benefits sections, so no
  second request per posting is needed. Live check: 40 postings, 4-8 KB of
  text each, no empty field.
- [x] A posting the vendor marks as written in a language the run does not want
  is still emitted; language is the pipeline's decision, not the adapter's.
- [x] `arbeitnow_direct` and `berlinstartupjobs_direct` page a whole board with
  no query matrix, and stop at `max_listing_pages` or the first empty page.
  The second answers a page past the end with a client error rather than an
  empty list, which is also treated as the end.
- [x] A rate-limit response ends that source's paging cleanly, keeping the
  postings already collected, and is reported as an event rather than raised.
- [x] A posting whose company name cannot be determined is dropped with an
  event, never emitted with an empty or invented company. Live check: 2 of 100
  postings on the title-derived board were dropped, each logged.
- [x] One failing query or page fails that query or page only; the rest of the
  run is unaffected.
- [x] `job-scraper capabilities --json` lists all three component ids, and
  `public_tests/test_public_contract.py` pins the full source list.
- [x] Offline tests use fake transports and fictional payloads; no test reaches
  a real board.

## Design and constraints

Dependency direction is unchanged: three Source adapters behind the existing
Source port, registered in `registry/builtins.py`, configured only in the
private workspace.

**Why three sources rather than one with a provider table.** `ats_direct` uses
a provider table because its providers are interchangeable readers of one
abstract thing — an employer's own board — so a provider is a row. These three
are not interchangeable. They differ in population (one vendor's customers, a
national feed, one city's startup board), in pacing (one rate-limits at a page
interval the others tolerate), and in value per request. An operator needs to
enable, page, and pace them separately, and a single component id would make
turning one off an edit to another's provider list. One component id per
external service is also what `collectors/` already does.

**Pacing, not retrying.** One of these boards answers a too-fast page loop with
a rate-limit status; measured, a sub-second page interval was refused where the
collector's configured interval was not. `BaseCollector.fetch_text` deliberately
does not retry that status — retrying spends the budget faster — so these
adapters pace between pages with the existing rate limiter and treat the status
as the end of paging. This adds no new retry policy.

**Company name is required.** One board carries the employer only inside the
posting title, after a separator; the structured taxonomy that would give it
directly is not public, returning a forbidden status. A title without the
separator therefore has no recoverable company, and such a posting is dropped.
Emitting it with an empty company would put a blank employer into every
downstream sink, which is the defect
[`2026-08-27-employer-direct-source-coverage.md`](2026-08-27-employer-direct-source-coverage.md)
repaired on another source; re-introducing it here would be a regression.

**Publisher noise arrives here too.** The token-free surfaces carry the same
staffing and crowd-work re-listers the search surfaces do — on the vendor
search, one crowd-work platform was the second most frequent company in a
sample. The `company` step's denylist already covers this for every source, so
these adapters add no filtering of their own.

**Why an enumerated board list is still not worth reading.** Having harvested
tokens from the crawl index, each provider's population was sampled directly
against its own public board API. Each is the wrong population for a
technical-role track, for a different reason, and the samples were large enough
to settle it:

| Provider | Sample | Result |
|---|---|---|
| German SMB system | 5 live boards, 57 positions | zero technical postings; the population is trades, workshops, dealerships |
| US-oriented system | 21 live boards, 2,469 postings | 7 in the configured country, none matching the role rule |
| Startup-oriented system | 178 live boards, 3,457 postings | 58 in the configured country; one board carried matching postings |

The third is the closest fit and still returns roughly one board in 178. A full
sweep of that provider is free and takes about fifteen minutes without
tripping rate limits, and would return a stock of low tens of postings, most of
which the existing sources already carry. The cost is not the obstacle; the
yield is.

**The same list against a different role rule, measured after the above.** That
last sentence was the important one. The first provider — the German SMB
system, dismissed above for carrying "zero technical postings" — was swept in
full for a non-technical track whose role rule selects office, commercial, and
administrative titles instead. 765 crawled tokens reduced to 506 live boards;
those served 5,570 positions, of which 392 passed that track's filters, from
180 employers. 177 of the 180 were absent from a database already holding 526
employers acquired from a public statutory job index, so the overlap between
the two surfaces is near zero rather than partial. That track's stored postings
grew by half in one sweep.

So "wrong population" was never a property of these boards. It is a
relationship between a board's population and one track's role rule, and it
reverses completely when the rule does. **Do not read the table above as a
verdict on the providers.** Re-run the measurement for any track whose roles
differ from the technical ones it was taken against; the sweep is free, and the
answer changed sign the first time it was asked again.

The freshness window matters as much as the role rule here. These boards are
slow: median posting age on the swept provider was 262 days, and only about 1%
of stock was a day old. A track reading them under a 24-hour window sees almost
nothing; the track above configures 60 days, which is why the same boards yield
for it.

**Freshness.** Like every other source, these do not filter by the search
window; `posted_at_text` is carried through and the pipeline's `freshness` step
decides. One board dates postings as a Unix timestamp rather than a formatted
string, which is converted at the adapter boundary.

## Verification

Offline tests per source: the request shape (query matrix and cursor following
for the search source, page walking for the two feed sources), the record
mapping including description assembly, per-query and per-page failure
isolation, the rate-limit stop, and the dropped-posting rule for a missing
company. The full quality gate in [`AGENTS.md`](../../../AGENTS.md) applies.

An independent user-path simulation through the production registry is required,
as it was for `ats_direct`, because per-item isolation is the criterion most
likely to pass in a unit test and fail in a run.

**Live validation (2026-08-27).** All three were additionally run read-only
against their real endpoints once, writing to no database, because a fixture
that invents a self-consistent payload passes while the adapter reads fields
the service never sends. Results: 40 postings across 23 employers from the
search source with no empty field; 175 across 119 from the national feed; 98
across 61 from the regional board, with 2 further postings dropped and logged
for an unrecoverable employer name. Two shapes the fixtures had not covered
turned up and are handled: the national feed sends multi-location strings in
more than one format, only one of which is safely splittable, and a minority
of its postings carry no location at all.

**Production outcome (2026-08-28).** Against the technical track these sources
were built for, the honest result is that they add little: through that track's
own filters and its 24-hour window, the search source and the regional board
each accepted nothing and the national feed accepted about ten postings a day,
of which most employers were already known. That track now runs the national
feed only. The limit is the market, not the adapters — the same measurement
found the role rule was rejecting almost nothing that belonged.

Where the same work paid off was elsewhere, and only after a reader pointed out
that the wrong population for one track is the right one for another: see the
`ats_direct` sweep recorded above and in that source's own spec.

An error worth recording because it cost a broken deploy: `--source` was added
to the CLI parser and to `run_daily`'s, but `job-scraper run` reaches
`run_daily` through `run_all_tracks`, which reparses and rebuilds the argument
list. The flag was accepted by `--help` and rejected at run time. The test that
missed it asserted the selection function behaved correctly, which it did;
nothing walked the chain. Its replacement does, and the per-profile argument
construction was extracted to `build_profile_argv` so a test can use the real
translation rather than restate it — restating it reproduces the bug instead of
catching it.

## Follow-ups

- Two vendors were measured and rejected rather than implemented: one
  enterprise-oriented applicant-tracking search whose token-free endpoint
  ignores every location parameter tried and whose national slice held four
  employers, all large; and two remote-work boards whose populations carry
  effectively no postings for the configured country. Recorded so they are not
  re-evaluated from the endpoint list alone — each has a working token-free API
  and is still the wrong population.
- A monthly free-text hiring thread was measured (roughly seven relevant posts
  per month) and deliberately not implemented: it needs a parser for
  unstructured prose that shares nothing with the other sources, and the volume
  does not carry that cost.
- The aggregated jobs surface of a major search engine was measured and is the
  strongest remaining candidate, but it is metered and so is not implemented
  here. Two properties make it different in kind from everything above: it
  indexes employers' own career pages, which is the surface `ats_direct` was
  built to reach and cannot enumerate; and its result sets for different
  queries did not overlap at all in a two-query sample, so coverage scales with
  the query matrix instead of resampling one pool. Against a track's real
  filters its acceptance rate was an order of magnitude higher than any
  token-free board here. Three caveats belong with it: results per query are
  capped at roughly twenty; the posted-date filter did not apply, and a third
  of results carried no date at all, so it is a stock surface rather than a
  daily-flow one; and its value depends on the track — for a track whose roles
  are covered by a public statutory job index, that index was itself the most
  frequent thing it returned, so it mostly re-indexes a source already read
  directly. If it is implemented, the natural shape is an operator-triggered
  periodic sweep, not a daily source, and `parse_relative_posted_at` needs a
  case it currently misses (a relative date expressed in months).
