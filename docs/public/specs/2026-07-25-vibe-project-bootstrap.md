# Vibe project bootstrap

## Outcome

Make the repository's established setup and offline verification commands available to
the reusable Vibe workflow without replacing its existing agent guidance.

## Scope

- In scope: add `.vibe/project.yaml` with commands already required by `AGENTS.md`;
  create the local `docs/context/` directory for future project context; exclude the
  separately initialized `my-vibe-skills` repository from this repository's Ruff scope;
  make the installed Vibe Skills the preferred general workflow while retaining
  repository-specific rules as authoritative.
- Out of scope: changing application behavior, collectors, private configuration,
  production dependencies, or existing `AGENTS.md` instructions.

## Acceptance criteria

- [x] Configuration contains only commands confirmed by the repository's existing
  public guidance.
- [x] Existing agent guidance remains unchanged; configuration changes are limited to
  this Vibe bootstrap and the isolated nested-repository exclusion.
- [x] Ruff continues to evaluate this repository but does not descend into the nested,
  independently managed Vibe Skills repository.
- [x] `vibe-verify` reports the configured quality gates and their fresh outcomes.
- [x] `AGENTS.md` states the Vibe-Skill priority without weakening project-specific
  privacy, architecture, or verification requirements.

## Design and constraints

The project-local `AGENTS.md` remains authoritative. The configuration uses the
repository's active Windows virtual environment because `uv` is unavailable in this
session; the documented `uv` commands remain the portable setup contract. `build` and
`dev` remain empty because no project command was confirmed for them. Browser checks
are off for this CLI/library; security checks remain conditional on the changed surface.
No private runtime information is recorded.

## Verification

Run the Vibe verifier and the four established quality gates. An independent user-path
simulation is required because this is a project configuration change.

## Follow-ups

None.
