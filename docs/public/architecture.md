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

- `domain`: typed jobs, identities, policies, and decisions.
- `ports`: stable interfaces for sources, channels, repositories, sinks, and
  status gateways.
- `pipeline`: small policy steps that accept or reject a normalized job.
- `application`: acquisition, search planning, aggregation, and profile
  orchestration.
- `adapters`: LinkedIn, Bright Data, IMAP, SQLite, CSV, and Notion boundaries.
- `registry`: maps stable component IDs to implementations.
- `cli`: validates local composition and invokes application use cases.

Dependencies point inward. Domain and ports never depend on a vendor SDK,
network transport, CLI, or database.

## Runtime composition

A local profile selects component IDs, query inputs, pipeline order, and
outputs. The public repository supplies implementations but no selection. This
keeps a user's search strategy and external workspace private while allowing
new sources and pipeline steps to be shared independently.

## In-process browser application delivery

Application delivery runs as a separate use-case area inside this repository.
Discovery, normalization, filtering, and canonical job identity remain
upstream responsibilities. The application runner reads accepted jobs from the
existing workspace database and uses private runtime facts, approved
documents, and a dedicated browser profile to operate supported application
forms.

Private runtime state must remain outside Git and must not leak into discovery
domain or pipeline imports. Browser vendors, credential stores, and external
status integrations are adapters behind ports. See the [in-process browser
application specification](specs/2026-07-27-in-process-browser-application.md).

## Concurrency

Enabled profiles can run concurrently. Sources within a profile can run
concurrently, and a source may use bounded query/detail workers. Duplicate
requests in one run are coalesced. External writes that require structural
consistency are serialized.

Recommendation-email enrichment is also failure-isolated. Concrete Indeed
URLs are resolved through bounded concurrent Bright Data batches. Retryable
HTTP responses use exponential backoff, and a persistently failing batch is
bisected until only the failing URL falls back to sparse email-card metadata.
Successful sibling batches retain their full job descriptions and snapshot
provenance.
