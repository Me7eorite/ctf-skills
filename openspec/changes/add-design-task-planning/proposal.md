## Why

The research workflow can now persist operator intent, research runs, sources,
and findings, but it still stops before creating concrete challenge work. The
next step is to convert a researched generation request into the same kind of
per-challenge task data that today's shard pipeline already understands,
without writing file-backed shards yet.

This change introduces database-backed design tasks: one row per future
challenge, linked to its `generation_requests` row and shaped to be compatible
with the existing `{"challenges": [...]}` shard item structure. It gives the UI
a place to show and manage the planned challenge tasks before any design agent
or batch worker consumes them.

## What Changes

- Add `design_tasks` as the first database-backed representation of a single
  future challenge task.
- Keep `generation_requests 1 -> N design_tasks`; do not add
  `design_batches` yet.
- Store shard-compatible seed fields on every design task:
  `challenge_id`, `title`, `category`, `difficulty`, `primary_technique`,
  `learning_objective`, `points`, and `port`.
- Store planning context needed by later prompt rendering:
  `research_run_id`, `task_no`, `scenario`, `constraints`, `evidence_summary`,
  and `source_finding_ids`.
- Do not store rendered `prompt_input` in PostgreSQL. Design prompts are
  rendered at execution time from the skill/template plus database fields,
  generation request fields, and referenced research findings/sources.
- Add a task-planning service that creates exactly `target_count` draft
  `design_tasks` from the latest completed research run and its findings.
- Add HTTP endpoints for generating, listing through request detail, queueing,
  and archiving design tasks.
- Extend the request detail page with a `Design Tasks` section so operators can
  inspect the conversion result from demand analysis to challenge tasks.

## Capabilities

### New Capabilities

- `design-task-planning`: converts a researched generation request into
  database-backed, shard-compatible design task rows for downstream challenge
  design.

### Modified Capabilities

- `research-planning`: request detail now exposes generated design tasks, but
  research execution remains responsible only for sources/findings and request
  status.
- `challenge-seed-management`: design task rows intentionally mirror the
  existing shard seed shape so a future change can export or batch them without
  lossy translation.

## Impact

- Adds an Alembic revision after the research tables.
- Adds `DesignTask` DTO/model/repository methods.
- Adds a planning service for creating task rows from a completed research run.
- Adds HTTP endpoints and dashboard UI for task generation and task state
  changes.
- Adds tests for schema, validation, idempotency, API behavior, and UI render
  coverage.
- No design worker, batch table, claim/lease logic, Hermes design invocation,
  or file-shard export in this change.

## Out of Scope

- `design_batches`.
- Design-agent execution and Hermes session recovery for design tasks.
- Batch claim/lease/heartbeat.
- Promotion into `work/shards/pending/`.
- Storing rendered prompt text or prompt JSON blobs in PostgreSQL.
