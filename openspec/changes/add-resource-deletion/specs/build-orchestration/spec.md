## ADDED Requirements

### Requirement: Build attempt allocation remains monotonic after deletion

Each `design_tasks` row SHALL carry
`next_build_attempt_no INTEGER NOT NULL DEFAULT 1` with a positive CHECK
constraint. The migration SHALL backfill existing tasks to
`COALESCE(MAX(build_attempts.attempt_no), 0) + 1`. Build submission SHALL lock
the task row, allocate the current value, and increment the counter in the same
transaction that inserts the attempt. Deletion SHALL NOT decrement or recompute
the counter.

#### Scenario: Deleting the latest attempt does not reuse its number

- **GIVEN** task T allocated attempts 1, 2, and 3
- **WHEN** attempt 3 is deleted and T is submitted again
- **THEN** the new attempt receives `attempt_no = 4`
- **AND** `attempt_no = 3` is not reused

#### Scenario: Existing tasks are backfilled

- **GIVEN** a task has existing attempts through `attempt_no = 5` before migration
- **WHEN** the migration is applied
- **THEN** its `next_build_attempt_no` becomes 6
- **AND** a task with no attempts receives 1

### Requirement: Build attempt dashboard exposes governed deletion

The existing Build Attempts list and detail modes SHALL add Delete actions
without replacing their filters, worker/validation actions, refresh behavior,
detail navigation, or retry controls. Deletion behavior and confirmation SHALL
conform to the `resource-deletion` capability.

#### Scenario: List adds deletion alongside existing build actions

- **WHEN** the build-attempt list renders
- **THEN** refresh, start-worker, validate, detail, and eligible retry actions retain their existing behavior
- **AND** Delete is available for each attempt row

#### Scenario: Detail exposes governed deletion

- **WHEN** a non-running build-attempt detail renders
- **THEN** a Delete action opens the shared confirmation dialog
- **AND** default confirmation preserves its challenge artifacts

## MODIFIED Requirements

### Requirement: BuildOrchestrationService submits and retries builds

The system SHALL provide `services.BuildOrchestrationService` with at least
these public methods. Mutation methods SHALL keep database writes inside short
PostgreSQL transactions; staging writes and queue publication are recoverable
filesystem steps outside those row-transition transactions.
`render_shard_payload` SHALL be a pure renderer:

- `submit_batch(design_task_ids: list[UUID]) -> list[UUID]`
- `submit_single(design_task_id: UUID) -> UUID`
- `retry(build_attempt_id: UUID) -> UUID`
- `render_shard_payload(design_task, latest_design, *, build_attempt_id,
  resume_from_shard_basename=None) -> dict`

`submit_batch` SHALL only accept design tasks whose current `status` is
`designed` or `build_failed`. It SHALL use this recoverable publication
protocol:

The staging directory SHALL be created by project path initialization and by
the orchestration service before writing, so a fresh checkout has the same
directory guarantees as `pending/`, `running/`, `done/`, and `failed/`.

1. Validate every task, pre-allocate each `build_attempt_id` following the
   repository's existing application-side UUID pattern, ensure
   `work/shards/staging/build-attempts/` exists, and render every payload into
   `work/shards/staging/build-attempts/<build_attempt_id>.json`; no file is yet
   visible to workers.
2. In one PostgreSQL transaction, lock every selected design-task row in stable
   UUID order, insert each `build_attempts` row with its pre-allocated id,
   `status = 'queued'`, and `attempt_no` allocated from that task's persistent
   `next_build_attempt_no`; increment each counter once and set every parent
   design task to `building`; then commit.
3. After commit, make an immediate best-effort atomic rename of every staged
   payload to `work/shards/pending/<shard_basename>`. A post-commit publication
   error SHALL be logged and recovered asynchronously; it SHALL NOT be reported
   as if the committed submission rolled back.

If validation, staging, or the database transaction fails, no database change,
counter increment, or pending shard SHALL survive. If the process exits after
commit but before all renames, a recovery pass SHALL publish matching committed
rows on startup or the next reconciliation tick. Staged files older than one
hour without committed rows SHALL be removed by recovery. Younger staged
payloads without a row SHALL be left alone so recovery cannot race an in-flight
submission transaction.

For a `designed` task, the emitted payload SHALL omit
`resume_from_shard_basename`. For a `build_failed` task, the service SHALL
require its highest-`attempt_no` row to be `failed` or `lost` and SHALL use
that row's basename as `resume_from_shard_basename`.

A design task in any other status SHALL be rejected with a validation error and
SHALL NOT advance.

`retry` SHALL require that the named `build_attempts` row is both the highest-
`attempt_no` row for its design task and in `failed` or `lost`, and that the
parent task is `build_failed`. It SHALL create a new attempt for the same design
task following the same flow with the next persistent attempt number, a fresh
attempt-specific `shard_basename`, and `resume_from_shard_basename` set to the
source attempt's basename, then return the new attempt id. It SHALL NOT touch
`work/challenges/<category>/<id>-<slug>/`; the runner's resume protocol carries
forward already-passed stages by inspecting artifact evidence for the same
challenge id.

#### Scenario: Submit batch rejects ineligible tasks

- **GIVEN** task A is `designed` and task B is `building`
- **WHEN** `submit_batch([A, B])` is invoked
- **THEN** the call raises a validation error
- **AND** no new `build_attempts` rows are inserted for either task
- **AND** no counter is incremented for either task
- **AND** no shard file is written under `work/shards/pending/`

#### Scenario: Submit batch failure before commit leaves no work

- **GIVEN** tasks A and B are both `designed`, and the filesystem blocks the shard write for B
- **WHEN** `submit_batch([A, B])` is invoked
- **THEN** neither task transitions to `building`
- **AND** no row or counter increment is committed for either task
- **AND** no shard is visible under `work/shards/pending/`

#### Scenario: Concurrent submissions allocate distinct numbers

- **GIVEN** two transactions concurrently submit the same eligible task
- **WHEN** both attempt to lock and allocate its counter
- **THEN** row locking serializes allocation
- **AND** the active-attempt uniqueness rule permits only one active attempt
- **AND** a rolled-back conflicting transaction does not consume a number

#### Scenario: Crash after commit converges through staging recovery

- **GIVEN** rows A and B committed but the process exited after publishing only shard A
- **WHEN** staging recovery next runs
- **THEN** shard B is published from its attributed staged payload
- **AND** no duplicate shard A is created

#### Scenario: Post-commit publication error remains accepted

- **GIVEN** the database transaction committed and a staged payload remains durable, but its immediate rename to `pending/` fails
- **WHEN** `submit_batch` completes
- **THEN** it returns the committed attempt id as accepted
- **AND** a warning is logged
- **AND** recovery retries publication on startup or the next tick

#### Scenario: Retry preserves existing artifacts

- **GIVEN** build attempt #1 for design task T finished `failed` and `work/challenges/<category>/<challenge_id>-<slug>/` exists with partial output
- **WHEN** `retry(attempt_1.id)` is invoked
- **THEN** a new `attempt_no = 2` row is inserted
- **AND** the new row has a different `shard_basename` from attempt #1
- **AND** its `resume_from_shard_basename` equals attempt #1's basename
- **AND** the existing artifact directory is not deleted or modified
- **AND** normal immediate publication moves the new shard into `work/shards/pending/`
- **AND** if immediate publication fails after commit, the staged shard remains recoverable and the accepted attempt stays `queued`

#### Scenario: Retry after a deleted sibling preserves sequence

- **GIVEN** task T allocated attempts 1 and 2, attempt 2 was deleted, and attempt 1 remains the latest failed row
- **WHEN** the operator submits or retries from the valid remaining state
- **THEN** the new attempt receives `attempt_no = 3`
- **AND** its resume source follows the latest remaining failed attempt when retry rules require one

#### Scenario: Retry of a stale sibling is rejected

- **GIVEN** attempt #1 failed and a later attempt #2 succeeded
- **WHEN** `retry(attempt_1.id)` is invoked
- **THEN** the request is rejected as a conflict
- **AND** the built parent task and both existing attempts remain unchanged
