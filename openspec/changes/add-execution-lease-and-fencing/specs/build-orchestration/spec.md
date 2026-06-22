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
`error` (TEXT nullable), `artifact_status` (TEXT NOT NULL DEFAULT `unknown`,
one of `unknown`, `present`, `missing`), `created_at` (TIMESTAMPTZ NOT NULL
DEFAULT now()), `started_at` (TIMESTAMPTZ nullable, set when the container's
first execution enters `running`), and `finished_at` (TIMESTAMPTZ nullable, set
when the container reaches a terminal status). Rows SHALL additionally carry
nullable execution references `current_execution_id`, `latest_execution_id`,
and `successful_execution_id` (FKs to `executions.id`). The per-run `worker`
and `error` details SHALL live on the `executions` row.

The unique compound key `(design_task_id, attempt_no)` SHALL hold.

#### Scenario: First submit creates a container with attempt_no = 1

- **WHEN** the operator submits a `designed` design task for building
- **THEN** exactly one container row is inserted with `attempt_no = 1`,
  `status = 'queued'`, `shard_basename` matching the rendered shard file's
  basename
- **AND** `created_at` is server-set; `started_at` and `finished_at` remain
  null

#### Scenario: Retry appends an execution rather than minting a build attempt

- **GIVEN** the latest execution for a build attempt has `iteration_no = 3`
  and `status = 'failed'`
- **WHEN** the operator retries it
- **THEN** no new `build_attempts` row is created and `attempt_no` is not
  incremented
- **AND** a new `executions` row is inserted under the same container with
  `iteration_no = 4`, `execution_kind = 'retry'`, and a fresh `claim_token`

#### Scenario: Clean rebuild reuses the same container

- **GIVEN** a build attempt container has a failed latest execution
- **WHEN** the operator requests a clean rebuild
- **THEN** no new `build_attempts` row is created
- **AND** a new `executions` row is inserted under the same container with the
  clean-rebuild execution mode preserved in execution metadata

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
feedback is out of scope.

#### Scenario: Feedback snapshot is persisted immutably

- **WHEN** a reviewer posts feedback for a build attempt
- **THEN** an immutable feedback snapshot is stored carrying `summary`,
  `requested_changes`, `preserve`, `forbid`, and `reviewer`
- **AND** a later `revision` execution can reference that snapshot without
  mutating it
