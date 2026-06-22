## MODIFIED Requirements

### Requirement: build_attempts table is the editorial unit of building

The system SHALL persist build orchestration state in a PostgreSQL table
`build_attempts`. Each row SHALL represent one operator-initiated build
**session container** for a single design task; the individual runs within a
session SHALL be `executions` rows (see capability `worker-pool-execution`).
Rows SHALL carry `id` (UUID PK), `design_task_id` (FK to `design_tasks.id`),
`attempt_no` (positive integer, unique per design task, allocated only on a
fresh submit), `status` (one of `queued`, `running`, `succeeded`, `failed`,
`lost`, maintained as the container aggregate derived from its latest
execution), `shard_basename` (TEXT, the basename written into
`work/shards/pending/` and re-rendered per iteration),
`resulting_challenge_dir` (TEXT nullable, the final successful artifact),
`error` (TEXT nullable compatibility aggregate mirroring the latest execution;
authoritative per-run errors live on executions), `artifact_status` (TEXT NOT
NULL DEFAULT `unknown`,
one of `unknown`, `present`, `missing`), `created_at` (TIMESTAMPTZ NOT NULL
DEFAULT now()), `started_at` (TIMESTAMPTZ nullable, set when the container's
first execution enters `running`), and `finished_at` (TIMESTAMPTZ nullable, set
when the container reaches a terminal status). Rows SHALL additionally carry
nullable execution references `current_execution_id`, `latest_execution_id`,
and `successful_execution_id` (FKs to `executions.id`). The per-run `worker`
and `error` details SHALL live on the `executions` row.
Each execution pointer SHALL be constrained to an execution belonging to the
same build attempt, using composite foreign keys rather than application-only
validation.

The unique compound key `(design_task_id, attempt_no)` SHALL hold.

#### Scenario: First submit creates a container with attempt_no = 1

- **WHEN** the operator submits a `designed` design task for building
- **THEN** exactly one container row is inserted with `attempt_no = 1`,
  `status = 'queued'`, `shard_basename` matching the rendered shard file's
  basename
- **AND** exactly one `executions` row is inserted with `iteration_no = 1`,
  `execution_kind = 'initial'`, `status = 'queued'`, and null worker/claim/lease
  fields
- **AND** `created_at` is server-set; `started_at` and `finished_at` remain
  null

#### Scenario: Retry appends an execution rather than minting a build attempt

- **GIVEN** the latest execution for a build attempt has `iteration_no = 3`
  and `status = 'failed'`
- **WHEN** the operator retries it
- **THEN** no new `build_attempts` row is created and `attempt_no` is not
  incremented
- **AND** a new `executions` row is inserted under the same container with
  `iteration_no = 4`, `execution_kind = 'retry'`, `status = 'queued'`, and
  null worker/claim/lease fields; a token is minted only when a worker claims it

#### Scenario: Clean rebuild reuses the same container

- **GIVEN** a build attempt container has a failed latest execution
- **WHEN** the operator requests a clean rebuild
- **THEN** no new `build_attempts` row is created
- **AND** a new `executions` row is inserted under the same container with the
  clean-rebuild execution mode preserved in execution metadata

#### Scenario: Successful publish records the canonical execution

- **GIVEN** an execution whose output is published successfully
- **WHEN** the publisher commits the canonical rename
- **THEN** the container's `successful_execution_id` is set to that execution

### Requirement: Container status follows the latest execution deterministically

The system SHALL derive `build_attempts.status` from the container's latest
execution using the following precedence: `queued` => `queued`; `claimed` or
`running` => `running`;
`succeeded` => `succeeded`; `failed` => `failed`; `lost` => `lost`. If no
execution exists yet, the container remains `queued`. When a new execution is
claimed, the container SHALL immediately move to `running`. A terminal write
from an older execution SHALL NOT overwrite the status of a newer latest
execution.

When a retry or revision is scheduled after a terminal iteration, the
container's `finished_at` and compatibility `error` aggregate SHALL be cleared
as it returns to `queued`; `started_at` SHALL retain the timestamp at which the
session's first execution entered running.

#### Scenario: Older terminal write does not win over a newer execution

- **GIVEN** execution `E1` has already been superseded by newer execution `E2`
- **WHEN** `E1` later attempts a terminal write
- **THEN** the write is rejected or ignored and the container status remains
  governed by `E2`

#### Scenario: Fresh submit after an abandoned session allocates the next attempt_no

- **GIVEN** the latest container for a design task has reached a terminal
  status and the operator starts a brand-new build session for it
- **WHEN** the new submit is committed
- **THEN** a new container row is inserted with the next monotonic `attempt_no`

## ADDED Requirements

### Requirement: Human feedback is accepted and persisted per build attempt

The system SHALL expose `POST /api/build-attempts/{id}/feedback` accepting a
structured payload (`summary`, `requested_changes`, `preserve`, `forbid`,
`reviewer`). The feedback SHALL be persisted as an immutable snapshot bound to
the build attempt container and SHALL be available to materialize into a
subsequent `revision` execution's workspace. This change SHALL provide the
schema, persistence, and materialization only; the management UI for submitting
feedback is out of scope. Each snapshot SHALL be a row in
`build_feedback_snapshots` carrying `id` (UUID PK), `build_attempt_id` (FK,
`ON DELETE CASCADE`), `summary` (TEXT), `requested_changes`, `preserve`, and
`forbid` (JSONB arrays), `reviewer` (TEXT), and `created_at` (TIMESTAMPTZ).
The pair `(id, build_attempt_id)` SHALL be unique for composite ownership FKs.
Rows are append-only. A revision execution SHALL store the selected snapshot in
its `feedback_snapshot_id`; if multiple snapshots exist, the request must name
one explicitly rather than relying on "latest" at claim time.

#### Scenario: Feedback snapshot is persisted immutably

- **WHEN** a reviewer posts feedback for a build attempt
- **THEN** an immutable feedback snapshot is stored carrying `summary`,
  `requested_changes`, `preserve`, `forbid`, and `reviewer`
- **AND** a later `revision` execution can reference that snapshot without
  mutating it
