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

### Requirement: build_attempts five-state machine

`build_attempts.status` SHALL be a **derived container aggregate** over its
`executions` rows and SHALL NOT be driven by filesystem observation. The
aggregate SHALL follow the latest execution using this precedence: latest `queued` =>
container `queued`; latest `claimed` or `running` => container `running`; latest
`succeeded` => `succeeded`; latest `failed` => `failed`; latest `lost` =>
`lost`. With no execution yet, the container remains `queued`.

The driving transitions live on the execution row (capability
`worker-pool-execution`), not on the reconciler's queue scan:

- `queued -> running`: a worker **claims** the queued latest execution
  (mints token + lease, sets `current_execution_id`); the container is moved to
  `running` in that same claim transaction.
- `running -> succeeded` / `running -> failed`: the worker performs a
  **token-gated** terminal write on its current execution; a successful publish
  yields `succeeded`, otherwise `failed`. The reconciler no longer derives
  terminal status from `done/` / `failed/` queue movement.
- `* -> lost`: the reconciler lease reaper terminally marks an expired current
  execution `lost` (see `worker-pool-execution`); recovery is a NEW queued
  `retry` execution, never an in-place revival of the lost row.

Terminal statuses (`succeeded`, `failed`, `lost`) SHALL be terminal for that
execution: there is no transition out of a terminal execution. A new run for the
same challenge SHALL be a new `executions` row under the **same** container
(`iteration_no + 1`); it SHALL NOT create a new `build_attempts` row and SHALL
NOT increment `attempt_no`. A terminal write from an execution that is no longer
the container's current execution SHALL be rejected and SHALL NOT overwrite the
aggregate.

`started_at` SHALL be set when the container's first execution enters `running`;
`finished_at` SHALL be set when the container reaches a terminal status and
SHALL be cleared when a new iteration returns the container to `queued`.

#### Scenario: Claim transitions the container to running

- **GIVEN** a container `B` whose latest execution is `queued`
- **WHEN** a worker claims that execution (mints token + lease)
- **THEN** the execution becomes `claimed`, `current_execution_id` is set, and
  the container `status` becomes `running` in the same transaction

#### Scenario: Worker token-write drives terminal status

- **GIVEN** a `running` current execution `E`
- **WHEN** the worker performs a token-gated terminal write after a successful
  publish
- **THEN** `E` becomes `succeeded`, `current_execution_id` is cleared, and the
  container aggregate becomes `succeeded`
- **AND** the reconciler does not separately derive this from queue movement

#### Scenario: Retry stays in the same container

- **GIVEN** a container whose latest execution is `failed` with `iteration_no = 3`
- **WHEN** the operator retries
- **THEN** no new `build_attempts` row is created, `attempt_no` is unchanged,
  and a new `executions` row with `iteration_no = 4` is scheduled

#### Scenario: Older terminal write does not win over a newer execution

- **GIVEN** execution `E1` has already been superseded by newer execution `E2`
- **WHEN** `E1` later attempts a terminal write
- **THEN** the write is rejected and the container status remains governed by
  `E2`

### Requirement: Only one build attempt per design task may be active

The system SHALL enforce that no design task has more than one `build_attempts`
container whose aggregate `status` is `queued` or `running` at any moment,
implemented as a PostgreSQL partial unique index on
`build_attempts (design_task_id)` filtered by `status IN ('queued', 'running')`.
Because the container status is now derived from executions, the single-active
guarantee at the execution layer is the partial unique index
`one_nonterminal_execution_per_attempt` (capability `worker-pool-execution`);
the container index remains so that two build sessions for the same design task
cannot be active at once. During the migration cutover window both the legacy
behavior and the execution-derived behavior MAY be present, but only one SHALL
be treated as authoritative for a given container (see Migration).

#### Scenario: Concurrent submit for the same design task is rejected

- **GIVEN** a `build_attempts` container exists with
  `(design_task_id = T, status = 'queued')`
- **WHEN** a second container `INSERT` is attempted for the same `design_task_id`
  while the first is still non-terminal
- **THEN** PostgreSQL raises a unique-violation error and the orchestration
  service surfaces it as a validation error

#### Scenario: Terminal container frees the design-task slot

- **GIVEN** the only active container for `design_task_id = T` reaches a
  terminal aggregate status
- **WHEN** the operator submits a brand-new build session for `T`
- **THEN** the new container is accepted with the next `attempt_no`

### Requirement: BuildReconciler mirrors filesystem state to PostgreSQL

`services.BuildReconciler` SHALL continue to run as a daemon thread launched by
`web.server.serve(...)`, polling on `BUILD_RECONCILER_POLL_SECONDS` (default 5,
invalid values fall back with a warning), resilient to PostgreSQL hiccups
(skip-and-warn, never crash), and SHALL still run staging recovery and trigger a
synchronous tick on `/api/state`. Its **status-determination role changes**:

- For containers created **after** the execution cutover, the reconciler SHALL
  NOT derive `running` / `succeeded` / `failed` from queue-directory movement.
  Those transitions are owned by the claim path and the worker's token-gated
  terminal writes (capability `worker-pool-execution`). The reconciler's only
  status write for these is the **lease reaper**: an expired current execution
  is terminally marked `lost` via a conditional update fenced by current
  execution id + active status + expired lease.
- For **pre-cutover** in-flight `build_attempts` rows that have no `executions`
  row, the reconciler SHALL retain the legacy filesystem-mirroring behavior to
  carry them to a terminal status; these rows are not backfilled with
  executions.
- It SHALL still roll the parent `design_tasks.status` forward from the
  container aggregate and recheck `resulting_challenge_dir` /
  `artifact_status` for succeeded containers.

#### Scenario: Reaper marks an expired current execution lost

- **GIVEN** a container's current execution is `running` with an expired lease
  and no recent heartbeat
- **WHEN** the reconciler tick runs
- **THEN** that execution is terminally marked `lost`, the container's current
  pointer is cleared, and the container aggregate becomes `lost`
- **AND** the reconciler does not mint a new token or auto-schedule recovery

#### Scenario: Legacy in-flight row still mirrors filesystem state

- **GIVEN** a pre-cutover `build_attempts` row with no `executions` row that is
  still `queued`
- **WHEN** the reconciler observes its attributed shard reach `done/` with a
  passed artifact
- **THEN** the reconciler applies the legacy transition to `succeeded` for that
  row

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
