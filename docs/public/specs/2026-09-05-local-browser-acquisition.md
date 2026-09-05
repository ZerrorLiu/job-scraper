# Local browser acquisition

## Outcome

A fresh checkout can collect Indeed search cards and full descriptions using
the operator's connected Chrome and an interactive agent, with no VPS, server,
device enrollment, mailbox, paid provider, or sibling checkout. Validated results
are durable in a private SQLite queue and exportable as a cumulative CSV.

## Design

Extend `job-scraper browser` with a `local` command group. Reuse
`BrowserTaskStore`, leases, result contracts, and transactional outbox. Local
transport belongs in `cli/browser_local.py`; `cli/serve.py` remains HTTP-service
composition. The local workspace is separate from the network queue, selected
explicitly by `--workspace` and defaulting to `data/browser-local` under the
current directory. No existing runtime configuration is read for this path.

The local outbox expands search cards into detail tasks; completed detail bodies
are already stored transactionally in SQLite. Export deduplicates by canonical
URL, retaining the latest collected detail across runs. It does not claim those
raw collected jobs passed profile screening or were published to Notion.

Bundle the operational worker instructions in `skills/positions-browser-worker`
so a fresh clone needs no locally installed private plugin. This is an acquisition
skill, not a prerequisite workflow for editing the repository. The separate
network client is outside this local path. Existing network behavior is retained.

## Acceptance

- A clean environment installs from this checkout and exposes the entire local CLI.
- One search creates a bounded queue; repeated same-day requests are idempotent.
- Claims resume an existing live lease and recover expired leases.
- Completion validates task identity, body schema, block reason, and full description.
- Result replay cannot duplicate jobs; search expansion survives interruption.
- Local CSV export is atomic and keeps earlier jobs, with spreadsheet-safe cells.
- CLI errors are concise; credentials and external publication are not required.
- Chrome unavailability or a challenge is reported truthfully; no HTTP scraping,
  CAPTCHA bypass, profile copying, or application submission is introduced.

## Verification

Offline tests exercise the complete fictional search-to-detail-to-export flow,
malformed results, lease expiry/replay, blocked results, and cross-run retention.
Run the full Python quality gates and install a built wheel in a clean environment.
Browser availability and successful live extraction are separate evidence gates.
VPS packaging, unattended execution, login autostart, and Web onboarding are deferred.
