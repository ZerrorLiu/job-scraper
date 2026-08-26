# Notion database ID bindings

## Outcome

Each local profile retains the identity of its daily Notion database after the
first successful title-based resolution. A later table-title edit or display
label change therefore updates the existing database instead of creating a
second one.

## Scope

- In scope:
  - Persist a database ID and data-source ID per profile in the private data
    directory.
  - Resolve a stored database ID before title discovery in publication and
    processed-status import flows.
  - Fall back to title discovery only when the stored database returns Notion
    `404`, then replace the binding when a database is found.
  - Make a bound database adopt the configured daily-table display title.
  - Show stored bindings for selected profiles in `job-scraper db status`
    without a Notion request or a local write.
- Out of scope:
  - A live Notion write, a migration of existing Notion objects, or changing
    any runtime Notion container or table display label.
  - Moving the V1/V2 publication state into the workspace database.
  - Bright Data acquisition and timeout behavior.

## Acceptance criteria

- [x] A successful title discovery records both IDs under the profile ID in a
  private, atomically written binding file.
- [x] A later publication or status import addresses an existing binding by
  database ID rather than enumerating tables by title.
- [x] A stored binding falls back to title discovery only after a `404`; other
  API failures do not risk a new database.
- [x] A title mismatch on an ID-bound database sends the configured display
  title to Notion; it never changes local configuration from the remote title.
- [x] `db status` reports every selected profile's stored binding or `unbound`
  without creating files, databases, or Notion objects.
- [x] Focused public tests remain offline and use fictional IDs only.

## Design and constraints

The binding file is local operational state next to each profile database, not
configuration and not a workspace table. It is ignored by Git and keyed by the
stable profile ID rather than a mutable track label or table prefix. The file is
written atomically only after a database response contains both required IDs.

The database ID is authoritative when a binding exists. The stored data-source
ID is a binding-time diagnostic value; publishing uses the current ID from the
database response, because Notion owns the relationship between the two.
Existing explicit database-ID configuration remains compatible for
installations that use it, but it does not override recovery from a stored ID
that returned `404`.

Title discovery remains the bootstrap and recovery mechanism. It performs the
same legacy-title handling as the existing publication flow, but is not used
while a stored ID resolves. Status import can discover and store an existing
database but never creates, renames, moves, or deletes a Notion object. The
initial binding comes from the normal live publishing path, not from `db
status`; it requires the owner's approval for that operational run.

## Verification

Focused offline tests cover per-profile persistence, concurrent profile saves,
a bound lookup, `404` recovery before an explicit database ID,
configured-title synchronization, and read-only `db status` output. Run the
repository formatting, lint, type, and public-test gates. An independent
user-path simulation is required because this changes both a CLI status surface
and the Notion publication configuration contract.

## Follow-ups

Live read-only confirmation of the four existing bindings and any owner-approved
Notion title synchronization remain separate operational work. Cross-process
writers are not coordinated; normal multi-profile execution is coordinated
within its process.
