# Deployment documentation restructure

## Outcome

A third-party user could install this project, but only by having an agent
reconstruct the missing steps. The deployment playbook described the sequence
without stating how to obtain any credential, so an agent reaching
`ERROR notion destination` had no documented path forward. Operational detail
had accumulated in `README.md`, and `docs/` mixed the current public contract
with superseded pre-v2 notes, including two files whose names collided with
their public replacements.

After this change one document, `docs/public/agent-deployment.md`, is
sufficient to take a new user from a fresh clone to a working installation,
including credential acquisition for every optional integration. Each remaining
document has one audience and one job, and the split is recorded where an agent
will read it.

## Scope

- In scope: rewrite the deployment playbook as a self-contained runbook; add an
  operations document and a documentation map; reduce `README.md` to
  navigation; remove the duplication between `CONTRIBUTING.md` and `AGENTS.md`;
  record the documentation split in `AGENTS.md`; archive superseded local notes
  out of `docs/`.
- Out of scope: any change to source code, CLI surface, configuration schema,
  or test suite. No behavior changes.

## Acceptance criteria

- [x] The deployment runbook states, for each optional integration, what
      credential artifact is needed, where the user obtains it, and how to
      confirm it resolves.
- [x] The runbook states the zero-credential default path before any optional
      integration.
- [x] Every `doctor` report an agent can encounter during deployment maps to a
      documented action.
- [x] Command examples are shell-neutral and match the current CLI surface.
- [x] `README.md` carries no operational detail that belongs in operations or
      configuration.
- [x] `docs/` contains only the current public contract.
- [x] Root-level Markdown is limited to the four standard files, and the limit
      is stated in `AGENTS.md`.
- [x] No internal documentation link is broken.
- [x] The repository quality gates pass and the privacy boundary is unchanged.

## Design and constraints

The documents are split by audience, not by topic. An agent deploying for a
user and an agent changing the code need disjoint information, and the previous
structure forced both to read `README.md` and `AGENTS.md` and discard half of
each. `docs/public/README.md` makes the split explicit; `AGENTS.md` records it
as a rule so later changes land in one place instead of accumulating in the
README again.

Credential instructions describe the artifact and where it lives rather than
transcribing a provider's current interface, because those interfaces change
and a stale click path is worse than none. The Notion section additionally
documents the grant step, which is the failure the previous documentation left
unexplained: a valid token with no page grant returns `404` on every call.

Examples use POSIX shell. The previous PowerShell-only examples were an
unnecessary platform assumption in a pure-Python project. Placeholder values
stay fictional and neutral, and no example names a real query, location,
employer, workspace, or identifier.

The superseded notes are archived under the ignored `local/` tree rather than
deleted, with a README stating what each one is wrong about. They describe four
built-in tracks and a shared public `config/` directory, both of which the
privacy boundary removed; leaving them beside the current documents invited an
agent to act on them.

## Verification

- `uv run ruff format --check .`, `uv run ruff check .`, `uv run pyright`, and
  `uv run pytest` — unchanged, since no source file is touched.
- Every relative link in the changed Markdown resolves to an existing file.
- The runbook was executed end to end against a clean clone of `HEAD` in a
  scratch directory: dependency install, offline suite, `capabilities`, `init`
  for both a minimal and a full-integration profile, `list`,
  `config validate --all`, `doctor --all`, `db init`, `plan`, and `feed`.
  Reported `doctor` states matched the table in section 6.
- No independent user-path simulation is required: the change is documentation
  only, and the user path was verified directly by executing it.

## Follow-ups

None.
