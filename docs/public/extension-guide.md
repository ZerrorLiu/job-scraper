# Extension guide

## Add a source

Implement `job_scraper.ports.sources.JobSource`. A source produces typed raw
job records; it does not filter, persist, export, or publish them.

Inject network transport where practical so unit tests can use local fake
responses. Translate vendor payloads and errors at the adapter boundary.

Register one stable component ID in `registry/builtins.py`, add contract tests,
and let users select the ID only in their private profiles.

## Add a pipeline step

Implement `job_scraper.ports.processors.PipelineStep`:

```python
class ExampleStep:
    name = "example"

    def evaluate(self, job, context): ...
```

One step should decide one business dimension and return a `Decision`. It
must not access the network, environment, clock, or database directly; inputs
arrive through the evaluation context and policy.

Register the step ID and add it to a local profile's ordered `pipeline` list.
The first rejection stops later evaluation.

## Add a sink

Implement `job_scraper.ports.sinks.JobSink`. A sink may write a file or
external system, but must not decide whether a job matches a profile.

Define empty-input behavior, translate SDK failures into project-level errors,
register a stable ID, and keep credentials in environment variables.

## Add a profile

Profiles are private composition, not library code. An agent creates the
profile and runtime TOML using the
[configuration reference](configuration.md), validates component IDs, and
runs offline checks before any live integration.

## Verification

```powershell
uv run ruff format --check .
uv run ruff check .
uv run pyright
uv run pytest
```

Live tests must be marked `live` and are never part of the default suite.
