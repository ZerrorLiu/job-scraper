# Architecture

The library separates business policy from external systems so an agent can
compose a workspace without editing the runner.

```text
CLI and concrete adapters
          |
          v
application use cases
          |
          +----------> ports <----------+
          |                             |
          v                             v
      domain model              pipeline decisions
          |
          v
repositories and sinks
```

## Layers

- `domain`: typed jobs, identities, policies, decisions, and the single
  country/location reference the adapters share.
- `ports`: stable interfaces for sources, channels, repositories, and sinks.
- `pipeline`: small policy steps that accept or reject a normalized job. The
  cumulative CSV export re-runs these same steps against stored rows rather
  than carrying a second copy of the rules.
- `application`: acquisition, search planning, aggregation, and profile
  orchestration.
- `adapters`: LinkedIn, Bright Data, IMAP, SQLite, CSV, and Notion boundaries.
- `registry`: maps stable component IDs to implementations.
- `configuration`: loads the private workspace and translates it into domain
  policies, so `pipeline` and `application` never import the TOML model.
- `cli`: validates local composition and invokes application use cases.

Dependencies point inward. Domain and ports never depend on a vendor SDK,
network transport, CLI, or database.

## Runtime composition

A local profile selects component IDs, query inputs, pipeline order, and
outputs. The public repository supplies implementations but no selection. This
keeps a user's search strategy and external workspace private while allowing
new sources and pipeline steps to be shared independently.

## Concurrency

Enabled profiles can run concurrently. Sources within a profile can run
concurrently, and a source may use bounded query/detail workers. Duplicate
requests in one run are coalesced. External writes that require structural
consistency are serialized.

Recommendation-email enrichment is also failure-isolated. It may use the
normal Indeed dataset to retrieve job details through bounded concurrent URL
batches. Retryable HTTP responses use exponential backoff, and a persistently
failing batch is bisected until only the failing URL falls back to sparse
email-card metadata. Successful sibling batches retain their full job
descriptions and snapshot provenance. The original platform listing URL remains
authoritative.

Browser-rendered Indeed work uses a separate asynchronous boundary. Each client
deployment has its own FastAPI process, SQLite database, device credential,
queue, and transactional outbox. The HTTP process validates and durably accepts
a leased result only; it never expands search cards, runs the job pipeline, or
writes Notion inline. A separate outbox invocation performs those idempotent
effects. The local `positions-client` stores claims and exact result bytes before
showing or uploading them, while Codex operates the user's connected Chrome.
No caller-supplied client identifier selects a tenant process.

The same-machine `browser local` transport uses the same task store, leases,
result contracts, and outbox without HTTP or device credentials. It owns a
separate private workspace, expands search results into detail tasks locally,
and exports completed details from SQLite. It does not run the profile pipeline
or external sinks; the network consumer's pipeline behavior remains separate.

## External writes

Requests that create a Notion object are never replayed after a server error
or a dropped connection, because the first attempt may have succeeded before
the failure surfaced; only an explicit rate-limit rejection is retried. Reads
and updates, being idempotent, retry with backoff. Updating an existing row
preserves whatever status a person set on it, whichever Notion property type
the workspace uses for that column.

## Schema evolution

The workspace store applies ordered, recorded migrations on `initialize()`.
`CREATE TABLE IF NOT EXISTS` alone is not sufficient: it silently skips a
database that already exists, so column and index changes would never reach a
live workspace. Migrations are idempotent and never drop data; a table the
current schema no longer defines is reported by `db status` rather than
removed.

Manual `Not Interested` status is applied after normal candidate filtering. A
matching repost is suppressed for 30 days using normalized title, company, and
location history; it does not suppress unrelated roles from the same company.
