# Engineering Role Targeting for AI-Oriented Search

## Outcome

The AI-oriented runtime profile should prioritize technical engineering and
developer roles while retaining useful coverage for AI automation,
integration, application, agentic, and platform work.

## Scope

### In scope

- Keep one neutral AI-oriented profile.
- Align profile-level queries with the runtime source query matrix.
- Require accepted titles to contain both an AI-family signal and an
  engineering/developer role marker.
- Apply the same search intent to sources that inherit the primary source's
  search matrix.

### Out of scope

- Creating a second profile.
- Adding credentials, identities, external payloads, or private workspace
  details to public configuration or documentation.
- Targeting non-technical advisory, management, ownership, analyst, or
  scientist roles unless their title also identifies an engineering or
  developer role.

## Acceptance criteria

1. The selected profile and runtime source configuration expose the same
   search intent.
2. A title containing an AI-family signal and `Engineer` or `Developer` is
   eligible for the target-role rule.
3. A title without `Engineer` or `Developer` is not eligible for the
   target-role rule, even when it contains an AI-family signal.
4. A generic automation title without an AI-family signal is not eligible for
   the target-role rule.
5. Grouped target rules preserve the same matching semantics after configuration
   values are converted into runtime policy values.
6. Offline configuration and project quality checks pass.

## Design

Use the existing profile composition and title-scoped target-rule mechanism.
The rule uses two keyword groups: one for AI/automation intent and one for the
technical role marker. No new profile or adapter is required.

## Verification

- Load the private profile and runtime configuration through the normal
  configuration loader.
- Inspect the composed query plan and target rule.
- Exercise representative fictional title cases offline.
- Run the repository's configured formatting, lint, type, and test gates.

## Follow-up

Review accepted-result quality after the next search cycle. Split the profile
only if the combined intent produces materially different location, seniority,
or role-quality needs.
