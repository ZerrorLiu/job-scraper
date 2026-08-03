# Platform job-link contract

## Outcome

Every acquired job keeps the original platform listing URL as its public job
link. Job discovery, detail enrichment, filtering, deduplication, persistence,
CSV export, and Notion publication use that stable source identity.

## Scope

- LinkedIn records use the LinkedIn public listing URL.
- Direct Indeed records use the Indeed listing URL returned or reconstructed
  from the provider job ID.
- Indeed recommendations found in email may use the standard Bright Data
  dataset for title, company, location, description, and posting metadata.
- Email detail retrieval does not replace the extracted platform URL with an
  HTTP redirect destination.
- Notion titles and `Job URL` blocks use `source_url`, falling back to
  `canonical_url` when needed.
- CSV and workspace migrations preserve job metadata while sanitizing raw
  payload URL fields that are not part of the source identity.
- Manual `Applied` and `Not Interested` decisions remain authoritative for
  later candidate processing.

## Acceptance criteria

- [x] LinkedIn, Indeed, and email acquisition retain title, company, location,
      description, provenance, freshness, filtering, and persistence behavior.
- [x] Direct Indeed collection uses one normal dataset stage.
- [x] Email-derived Indeed jobs can retrieve details through bounded,
      failure-isolated Bright Data batches.
- [x] Stored and published job links resolve to the source platform listing.
- [x] CSV export and V1-to-V2 migration sanitize non-source URL metadata.
- [x] Existing private databases remain readable without destructive migration.
- [x] Manual job-decision import and processed-job filtering continue to work.
- [x] All repository quality gates pass.

## Design and constraints

Source identity remains separate from transport. Bright Data snapshot IDs,
request hashes, query context, and detail metadata may be retained, but they do
not replace the platform listing URL.

Database migrations remain idempotent and non-destructive. Historical schema
columns may remain physically present for compatibility, but they are not part
of the current domain or publication contract.

No private workspace, profile, database, CSV, log, export, or runtime data is
deleted by this change.

## Verification

Run focused offline tests for LinkedIn and Indeed mapping, email detail
enrichment, platform-link publication, CSV export, migration sanitation, and
manual decision filtering. Then run:

```powershell
uv run ruff format --check .
uv run ruff check .
uv run pyright
uv run pytest
```

## Follow-ups

None.
