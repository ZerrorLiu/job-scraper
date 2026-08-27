# Description language policy defect

## Outcome

`minimum_english_ratio` does not do what its name and its documentation
promise. Under every profile shipped today it rejects a *narrow band of the
most English* descriptions and admits everything less English than that band.
A maintainer reading the key name cannot predict this, and the two profiles
that lean hardest on language policy are the two least protected by it.

The mechanism is the interaction between two keys that are documented
separately and only meaningful together. `LanguageStep` calls
`is_allowed_description_language`, which branches on whether
`allowed_description_languages` is empty:

- **Empty** — the ratio is compared through `is_english_job`, and a
  non-English description is rejected. This is the behavior the key name
  describes, and no shipped profile selects it.
- **Non-empty** — membership in the list decides the verdict, and the ratio is
  consulted *only* when the detected label is exactly `English`.

`describe_language` assigns `English` only at an English ratio of 0.75 or
above, and its other labels partition everything below. So when the list is
non-empty and contains every label the classifier can emit, the ratio gate
reduces to a single rejected interval:

| English ratio | Label | Verdict at `minimum_english_ratio = 0.85` |
|---|---|---|
| 0.00 | `German` | accepted |
| 0.40 | `German` | accepted |
| 0.60 | `Mixed` | accepted |
| 0.74 | `Mixed` | accepted |
| **0.75** | `English` | **rejected** |
| **0.84** | `English` | **rejected** |
| 0.85 | `English` | accepted |
| 1.00 | `English` | accepted |

A wholly German description passes. A description that is 80% English is
rejected under `RejectionReason.NON_ENGLISH`. That is the inversion.

The observable result of this change is that the language keys mean what they
say: a value that reads as "reject descriptions below this much English" either
does that or is not accepted by configuration validation.

## Scope

- In scope:
  - The interaction contract between `require_english`,
    `allowed_description_languages`, and `minimum_english_ratio`, and whichever
    of the three survives.
  - The German-language phrasings that `excluded_requirement_patterns` fails to
    match, described below as a measured gap rather than as a word list.
  - The profile documentation that currently describes the three keys as one
    undifferentiated "language policy".
- Out of scope:
  - `describe_language` and its thresholds. The classifier's labels are
    consistent with themselves; the defect is in how the policy consumes them.
  - Any acquisition source. The defect predates the sources under consideration
    in [`2026-08-27-public-employment-agency-source.md`](2026-08-27-public-employment-agency-source.md)
    and affects the profiles running today.
  - Rejection-reason taxonomy beyond keeping `NON_ENGLISH` truthful.

## What this change replaces

The survey:

```bash
rg -n "minimum_english_ratio|allowed_description_languages|require_english" src public_tests docs config
```

The key is read in two places, and the survey above found only the first
because the second does not filter by language — it decides which languages to
*read*.

1. `LanguageStep` in `pipeline/steps.py`, through
   `is_allowed_description_language` in `pipeline/language_filter.py`. This is
   the branch the verdict table describes.
2. `CsvSink.publish` in `adapters/sinks/csv.py`, which read `require_english`
   directly to choose the `languages` argument for the cumulative query — and
   therefore decided which rows the policy would ever see, *before* the policy
   ran. A language excluded there could not be re-admitted by the step.

The second site was found by running the pipeline end to end rather than by
reading, and it was the more damaging of the two: on a profile admitting
German, acquisition stored German postings and the export then dropped every
one of them, silently, with the rows still in the database. Measured on live
databases at the time it was found, the affected share was 35–40% of two
profiles' stored rows and 82% of a third's. Both sites now follow the same
two-mode contract. What is superseded is the *documented* contract in
[`profile-design.md`](../profile-design.md), which presents the three keys as a
single policy a maintainer may set independently, and is corrected in the same
change.

The key cannot simply be dropped from configuration files. `config.py` reads it
by direct subscript, not `.get`, so a profile omitting it fails to load. Either
the read gains a default or the key stays required with an honest meaning; that
choice is an acceptance criterion below, not a decision recorded here.

## Acceptance criteria

- [x] A profile that sets `minimum_english_ratio` to a value that cannot affect
  any verdict — because `allowed_description_languages` admits every label the
  classifier emits — is rejected at load time, or the key's effect is made
  independent of the list. Silently ignoring the value is not an outcome.
  Implemented as both: `is_allowed_description_language` in
  `pipeline/language_filter.py` never consults the ratio once the list is
  non-empty, and `config.resolve_minimum_english_ratio` raises `ValueError` at
  load time if a profile sets both keys.
- [x] No configuration produces a verdict in which a description is rejected
  while a strictly less English description is accepted.
- [x] `RejectionReason.NON_ENGLISH` is recorded only for a description the
  policy actually judged insufficiently English.
- [x] Removing the key from a profile either loads with a documented default or
  fails with a message naming the key; it does not raise `KeyError` from the
  configuration loader. A profile with a non-empty `allowed_description_languages`
  loads with `minimum_english_ratio` defaulted to `0.0` (unused in that mode);
  a profile with an empty list and no ratio key fails with a `ValueError`
  naming `minimum_english_ratio`.
- [x] `excluded_requirement_patterns` matching is documented as whole-substring
  matching, with the consequence stated: a pattern naming a noun does not match
  that noun behind an intensifier.
- [x] The three language keys are documented as one interacting contract with
  the branch condition stated, not as three independent knobs.
- [x] An existing profile that changes no key produces the same accept/reject
  verdict for every description outside the inverted band, so the change is
  reviewable against stored data.

## Design and constraints

**Why this was invisible.** The inverted band is real but rare. Replaying the
four live databases — 4,327 stored jobs, the same corpus the step-ordering
comment in `pipeline/steps.py` cites — finds 10 jobs whose English ratio falls
in `[0.75, 0.85)`, or 0.23%. The key has therefore never visibly misbehaved: it
changes the verdict for roughly one job in four hundred, and changes it in the
direction that discards the job, where a discarded job leaves no trace to
notice. Language distribution across the same corpus is 59.7%–93.7% `English`
by profile, with `German` the remainder and `Mixed` and `Unknown` together
under 3%.

**Why the interaction, not the value, is the defect.** Every shipped profile
sets `allowed_description_languages` to all four labels `describe_language` can
return. That is a reasonable thing for a maintainer to write — it reads as "do
not filter on language" — but it silently transfers the verdict away from the
ratio to a membership test that can never fail, leaving the ratio governing
only the one label that reaches it. Two keys that are individually sensible
compose into a rule neither expresses.

**The requirement-pattern gap is separate and additive.** Language *detection*
answers "what language is this written in". Language *requirement* answers
"what does this employer demand", and only `excluded_requirement_patterns`
addresses it. Those are different questions and the second is the one that
matters for a candidate reading a foreign-language advertisement. The patterns
are matched as whole substrings against a normalized copy of the description,
so a pattern naming a bare noun does not match that noun preceded by an
intensifier or a qualifier — the most common way a language demand is actually
phrased in German. Measured against a sample of 46 descriptions from a public
employment-service surface, the configured patterns matched 35% while a
phrasing-aware pattern set matched 59%; against 36 descriptions from
applicant-tracking boards, 7 of 36 versus 11 of 36. The missed cases were not
exotic. They were the ordinary intensifier-plus-noun form.

**Privacy.** The phrasings that were missed are properties of a language, not
of an installation, but the queries, locations, and employers that produced the
samples are search strategy and do not appear here. The sample composition is
stated only as counts.

**Compatibility.** Any correction changes verdicts. The safe form is a
configuration-validation error that forces a maintainer to restate intent,
rather than a silent change of meaning for a key whose current value was chosen
under the wrong model of what it does.

- [x] Every site that consults `require_english` follows the same two-mode
  contract. `CsvSink.publish` derives its language query from
  `allowed_description_languages` when that list is non-empty and falls back to
  `require_english` only when it is empty, so the export can no longer answer a
  different question from the acquisition that filled the database.

## Verification

Offline tests cover: the verdict table above as a property — no accepted
description is less English than a rejected one, asserted across the ratio
range and every label; both branches of the empty/non-empty
`allowed_description_languages` split; a profile omitting the key; and
whole-substring matching for `excluded_requirement_patterns` including the
intensifier case that motivated this.

A replay of the stored databases before and after is required, reporting the
count of changed verdicts by rejection reason. The property tests establish the
rule; the replay establishes the blast radius, and 0.23% is the number it
should reproduce for the language step alone. No independent user-path
simulation is required — the defect is entirely inside one pure function and
its configuration, with no acquisition, concurrency, or external-write
surface.

**Replay result (2026-08-27).** Read-only replay against the four live
databases behind `config.toml`, `config.chinese.toml`, `config.cpp.toml`, and
`config.it_adjacent.toml`, comparing the old and new
`is_allowed_description_language` verdicts over each database's stored
`description_language`/`english_ratio` columns at that profile's shipped
`minimum_english_ratio`:

| Database | Jobs | Changed | Now accepted | Now rejected |
|---|---|---|---|---|
| `jobs.db` (ratio 0.85) | 1,954 | 2 (0.10%) | 2 | 0 |
| `jobs_chinese.db` (ratio 0.85) | 95 | 0 (0.00%) | 0 | 0 |
| `jobs_cpp.db` (ratio 0.85) | 1,903 | 5 (0.26%) | 5 | 0 |
| `jobs_it_adjacent.db` (ratio 0.75) | 375 | 0 (0.00%) | 0 | 0 |
| **Total** | **4,327** | **7 (0.16%)** | **7** | **0** |

Same corpus size the design section cites (4,327), same order of magnitude
(0.16% vs. the 0.23% estimated from a 10-job sample), and every changed
verdict moves in the corrective direction — a previously wrongly-rejected
description is now accepted, never the reverse. `config.it_adjacent.toml`
shows zero change because its ratio (0.75) sits exactly at the `English` label
boundary, so the inverted interval was empty for that profile even before the
fix.

## Follow-ups

- Whether `require_english` survives at all. It is consulted only in the branch
  no shipped profile selects, which makes it a third name for a behavior two
  other keys already decide. Deleting it is plausible and out of scope here.
  The `CsvSink` site is the argument for deleting it: a key that means nothing
  in the mode every profile uses will keep being read by the next component
  that needs "should this be English", because its name still promises an
  answer.
- A rule that a sink may not narrow its own input by policy. Both language
  sites were defensible in isolation; the damage came from one of them
  filtering *before* the shared pipeline ran, where its decision was invisible
  and unappealable. Any future sink that pre-filters its query has the same
  shape.
- A requirement-phrasing vocabulary per language, rather than a per-profile
  list each installation maintains by hand. Deferred: the correct shape depends
  on how many languages a profile ever needs, and today the answer is one.
