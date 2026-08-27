# Documentation map

Two audiences, kept separate. Find your row first.

## Installing and running it

| Document | Use it when |
|---|---|
| [Agent deployment runbook](agent-deployment.md) | Taking a new user from a fresh clone to a working installation. The procedure, self-contained, including credential acquisition. |
| [Designing a profile](profile-design.md) | Deciding what goes *into* the configuration: queries, the five keyword fields, destinations, which optional components to offer, and what is safe to re-run. |
| [Operations](operations.md) | The installation exists and you need the everyday commands. |
| [Configuration reference](configuration.md) | Looking up what a specific key, file, or environment variable does, and the exact shape the Notion sink writes. |

## Changing the code

| Document | Use it when |
|---|---|
| [`AGENTS.md`](../../AGENTS.md) | Before any change. Privacy boundary, architecture rules, quality gates. Authoritative. |
| [Architecture](architecture.md) | Understanding the layers, concurrency, external-write, and schema-evolution rules. |
| [Extension guide](extension-guide.md) | Adding a source, pipeline step, or sink. |
| [Development workflow](agent-development-workflow.md) | The specification-to-handoff lifecycle every change follows. |
| [Spec template](spec-template.md) | Starting a new change. Copy it into `specs/`. |
| [`specs/`](specs/) | Why a behavior is the way it is. One dated document per change. |

## What is not here

The repository contains no profiles, search terms, locations, company
watchlists, workspace names, credentials, or runtime data. Those live in each
installation's private, ignored `config/`, `data/`, and `.env`. Documentation
examples are fictional and neutral by rule.
