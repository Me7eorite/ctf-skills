## Why

`add-design-task-planning` ended at one row per future challenge in
`design_tasks`. When operators press **Queue**, the task is "released for
design" but nothing actually designs it - `queued -> designing -> designed`
is reserved without an implementation. This change wires that gap: it
takes a `queued` design task, builds a prompt from the existing
`skills/design-challenges` skill plus the request's research evidence,
runs it through Hermes, validates the structured JSON it returns, and
persists it as a draft `challenge_designs` row so operators can review
the design before any challenge files or shard exports happen.

The output is intentionally a database-backed *design document*, not a
buildable challenge artifact. Producing Dockerfiles, attachments, or
shard JSON belongs to later changes; this one stops at "we have a
validated, structured design we can show to the operator."

## What Changes

- Add `design_attempts` - one row per Hermes invocation against a
  `design_task`, tracking attempt number, status, prompt path, log
  path, error, and a request-local fencing token.
- Add `challenge_designs` - one row per successful attempt, holding the
  validated structured JSON the design-challenges skill returns
  (event-level + per-challenge sections matching its `challenges[]`
  schema).
- Add a deterministic **prompt context loader + pure prompt wrapper**:
  the loader reads the `skills/design-challenges` skill template and
  category references (`web-design.md` / `pwn-design.md` /
  `reverse-design.md` / `other-categories.md`), while the pure wrapper
  combines that context with the parent `generation_requests` topic,
  runtime constraints, source `design_tasks` row, and cited
  `research_findings` / `research_sources`.
- Add a **`DesignChallengeExecutor`** that runs Hermes once with the
  rendered prompt, captures stdout/log, and lets the service apply
  request-local token-fenced terminal transitions.
- Add **JSON validation** for the design-challenges output: required
  fields per challenge (id/title/category/difficulty/points/deployment/
  primary_technique/learning_objective/prompt/artifacts/flag_location/
  validation/hints), category must equal the parent task, points > 0,
  Web/Pwn must include port and follow the SKILL.md container rules
  (single-service compose, FLAG via env, non-root user, no volumes).
- Drive `design_tasks` status: `queued -> designing` on real attempt
  start, `designing -> designed` on validation success,
  `designing -> queued` when a failed attempt can be retried, and
  `designing -> failed` when `max_attempts` is exhausted. Retry does
  not insert a queued attempt placeholder; the next operator trigger
  creates the next real attempt.
- Expose `POST /api/design-tasks/{id}/design` to trigger one attempt
  synchronously (operator-initiated, not a worker pool), and extend
  `GET /api/research/requests/{id}` so each `design_tasks` row includes
  its latest `challenge_design` summary + attempt history with
  artifact URLs instead of raw filesystem paths.
- Add a **Designs** subsection under each Design Task row in the
  request detail page - collapsible JSON viewer for the validated
  design, attempt list with status pills, and a "Design now" button.

## Capabilities

### New Capabilities

- `structured-challenge-designs`: converts `queued` design tasks into
  validated, structured challenge_design documents by invoking the
  bundled `skills/design-challenges` skill via Hermes and persisting
  the JSON result. Owns the `design_attempts` / `challenge_designs`
  tables, the prompt wrapper, the executor, the JSON validator, and
  the status transitions on `design_tasks` from `queued` through
  `designed` / `failed`.

### Modified Capabilities

<!-- design-task-planning is not yet in openspec/specs/ because that
     change has not been archived. The status transitions
     queued -> designing -> designed / failed land here as part of the
     new capability. -->

## Impact

- **Depends on** `add-design-task-planning` being archived first
  (this change writes to `design_tasks.status` values reserved by that
  change).
- Adds Alembic revision `0004_design_attempts_and_designs` after the
  design-tasks tables.
- Adds DTOs `DesignAttempt`, `ChallengeDesign` and validators.
- Adds repository methods on a new `ChallengeDesignRepository`.
- Adds `DesignChallengeExecutor` (Hermes wrapper) and
  `ChallengeDesignService` (synchronous claim + execute + persist).
- Adds HTTP endpoints: `POST /api/design-tasks/{id}/design`; extends
  the request-detail payload with `latest_design` + `attempts`.
  Attempt responses split `design_task_status` from `attempt_status`
  so retryable attempt failures can return the task to `queued`
  without looking like terminal task failure.
- Adds dashboard collapsible **Designs** section under each Design
  Task row and a "Design now" action.
- Adds tests for prompt assembly, JSON validation, executor happy + 5
  failure paths, status-sync rules, and API/UI rendering.
- Writes Hermes stdout/stderr under `work/design/logs/<attempt_id>.log`
  and rendered prompt under
  `work/design/prompts/<attempt_id>.md`; no other filesystem outputs.
- **Does not** generate challenge directories, Dockerfiles, attachment
  files, shard JSON, or `work/shards/pending/` entries.
- **Does not** introduce a design worker pool, queue claim/lease, or
  background scheduler; each attempt is initiated by an explicit
  operator action and runs in the request thread with only a request-local fencing token.

## Out of Scope

- Background worker pool, lease/heartbeat, retry scheduler.
- Generating challenge file trees (Dockerfile, source, attachments).
- Exporting to `work/shards/pending/` or the legacy file-based shard
  queue.
- Multi-Hermes-profile fan-out per design.
- A separate "design queue" UI page; the design lives inline with its
  parent design task row in the request detail page.
