# LinkedIn email card metadata

## Outcome

When a LinkedIn job-detail request does not provide structured posting data,
email ingestion should still preserve company and location when the selected
email card visibly contains them.

## Scope

- Use the selected job title as an anchor.
- Parse only the immediately following `Company · City` card metadata, or an
  explicit `at Company` suffix.
- Do not infer a selected job's company from unrelated recommendation cards in
  the same email.
- Keep the behavior offline and credential-free.

## Acceptance criteria

1. A card such as `Embedded Systems Engineer Northstar Robotics · Berlin`
   yields company `Northstar Robotics` and location `Berlin`.
2. A card such as `Platform Developer at Blue Harbor Systems` yields company
   `Blue Harbor Systems`.
3. A selected title followed by adjacent recommendation cards does not borrow
   their company or location.
4. Existing non-LinkedIn email extraction behavior remains unchanged.
