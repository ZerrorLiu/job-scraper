# Fine-grained AI and C++ role skill analysis

## Outcome

Produce a reproducible, public-safe analysis that splits the existing broad AI
and C++ job clusters into decision-useful subcategories and reports the exact
skill frequency and requirement type within each subcategory.

## Scope

- In scope:
  - Read the existing cleaned job-market CSV without changing its source data.
  - Classify AI-focused jobs into one primary AI subcategory.
  - Classify C++-focused jobs into one primary C++ subcategory.
  - Allow a job to appear once in each family when it genuinely crosses AI and
    C++, while preventing duplication within a family.
  - Export category counts, job-level audit mappings, per-category skill
    frequency with must/nice/responsibility/stack counts, and a Markdown
    interpretation.
  - Add focused fictional-data tests for priority and exclusivity rules.
- Out of scope:
  - Re-scraping jobs, changing the existing broad role clusters, changing user
    fit scoring, or modifying source databases and prior exports.
  - Treating rule-based categories as human-reviewed ground truth.
  - Adding private search strategy, identity, credentials, or workspace IDs.

## Acceptance criteria

- [ ] AI categories distinguish at least research, ML engineering, LLM/GenAI,
      computer vision/3D, robotics, and AI platform/inference work.
- [ ] C++ categories distinguish at least backend/distributed,
      trading/low-latency, embedded/firmware, industrial/device integration,
      Qt/application, robotics, performance/HPC, and test/V&V work.
- [ ] Every selected job has exactly one primary category per applicable
      family, with an auditable classification reason.
- [ ] Skill output contains category denominator, count, share, and
      must/nice/responsibility/stack counts.
- [ ] Generated reports state that AI and C++ family totals may overlap and
      that rule-based labels are estimates.
- [ ] Focused tests and all repository quality gates pass.

## Design and constraints

The analysis is an offline reporting extension under `job-market-analysis/`.
It reads `cleaned-jobs.csv`, uses stable title, existing broad cluster, industry,
and structured skill fields, and writes only new analysis outputs. Strong title
signals take priority; existing broad clusters and structured skills provide
fallback routing. Description text is not used to inflate family membership.

The classification is mutually exclusive inside each family. Cross-family
overlap is intentional for roles such as robotics perception or C++ inference
systems and must be reported explicitly.

No source database, existing CSV, Notion backup, or runtime configuration is
modified.

## Verification

- Focused fictional-data tests for category priority, cross-family overlap,
  exclusivity, and skill aggregation.
- Run the generator against the existing cleaned export and verify totals,
  non-empty categories, share bounds, and job-map uniqueness.
- Required repository gates:
  - `uv run ruff format --check .`
  - `uv run ruff check .`
  - `uv run pyright`
  - `uv run pytest`

Independent user-path simulation is not required because this adds no CLI,
configuration, public API, architecture, or runtime behavior; the generated
CSV audit map provides direct reproducibility.

## Follow-ups

Optional future manual sampling can estimate precision for individual
subcategories. It is not required for the rule-based market comparison.
