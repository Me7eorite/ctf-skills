## ADDED Requirements

### Requirement: Each run is persisted as an execution row under a build-attempt container

The system SHALL persist each individual build run as a row in a PostgreSQL
table `executions`. A `build_attempts` row SHALL act as a per-build-session
**container**; one or more `executions` rows SHALL belong to it, ordered by a
monotonic `iteration_no`. Each `executions` row SHALL carry: `id` (UUID PK),
`build_attempt_id` (FK to `build_attempts.id`, `ON DELETE CASCADE`, NOT NULL),
`parent_execution_id` (FK to `executions.id`, nullable), `iteration_no`
(positive integer), `execution_kind` (one of `initial`, `retry`, `revision`),
`execution_mode` (one of `standard`, `clean`; `clean` is only valid when
`execution_kind = 'retry'`), `feedback_snapshot_id` (nullable FK to
`build_feedback_snapshots.id`), `worker_id` (TEXT nullable), `claim_token` (UUID
nullable), `lease_expires_at` (TIMESTAMPTZ nullable), `heartbeat_at`
(TIMESTAMPTZ nullable), `status` (one of `queued`, `claimed`, `running`,
`succeeded`, `failed`, `lost`), `exit_class` (TEXT nullable), `error` (TEXT
nullable), `started_at` / `finished_at`
(TIMESTAMPTZ nullable), and `created_at` (TIMESTAMPTZ NOT NULL).

The compound unique key `(build_attempt_id, iteration_no)` SHALL hold. Any
non-initial execution SHALL require a non-null `parent_execution_id` that
belongs to the same build attempt. An `execution_kind` of `revision` SHALL also
require a non-null `feedback_snapshot_id` belonging to that build attempt. An
`execution_mode` of `clean` SHALL require `execution_kind = 'retry'`. Queued
executions SHALL have null `worker_id`, `claim_token`, `lease_expires_at`, and
`heartbeat_at`; all claimed/running/terminal executions SHALL retain non-null
claim and lease values.
Running executions SHALL have non-null `started_at`; terminal executions SHALL
have non-null `finished_at`. The pair `(id, build_attempt_id)` SHALL be unique
to support composite ownership foreign keys.
Same-container ownership SHALL be enforced with composite foreign keys
`(parent_execution_id, build_attempt_id)` and
`(feedback_snapshot_id, build_attempt_id)` referencing corresponding unique
pairs on executions and feedback snapshots; it SHALL NOT rely on a cross-row
CHECK constraint.
`executions` SHALL be the source of truth for per-run state; the container row's
status and run metadata SHALL be derived from execution transitions in the same
transaction.
Scheduling SHALL set `latest_execution_id` and leave `current_execution_id`
null. Claim SHALL set current to the queued latest execution. Terminal writes
SHALL clear current while retaining latest.

#### Scenario: Initial run schedules iteration 1

- **WHEN** a fresh build attempt is submitted
- **THEN** exactly one `executions` row is inserted with `iteration_no = 1`,
  `execution_kind = 'initial'`, `status = 'queued'`, `parent_execution_id` null,
  and null worker/claim/lease fields

#### Scenario: Revision without a parent is rejected

- **WHEN** an `executions` row with `execution_kind = 'revision'` is inserted
  with a null `parent_execution_id`
- **THEN** the database rejects the row via a check constraint

#### Scenario: Clean execution mode requires retry kind

- **WHEN** an `executions` row is inserted with `execution_mode = 'clean'`
  and `execution_kind != 'retry'`
- **THEN** the database rejects the row via a check constraint

### Requirement: At most one non-terminal execution per build-attempt container

The system SHALL enforce that no `build_attempts` container has more than one
`executions` row whose `status` is `queued`, `claimed`, or `running` at any
moment. This
SHALL be implemented as a PostgreSQL partial unique index on
`executions (build_attempt_id)` filtered by `status IN ('queued', 'claimed',
'running')`.

#### Scenario: Concurrent second scheduling is rejected at the database

- **GIVEN** a non-terminal execution already exists for `build_attempt_id = B`
- **WHEN** a scheduler attempts to insert another queued execution for
  `B`
- **THEN** PostgreSQL raises a unique-violation error and the second scheduling
  fails

### Requirement: Scheduling and claim are separate atomic transitions

Scheduling SHALL, in one transaction, lock the build attempt, allocate
`iteration_no = COALESCE(MAX(iteration_no), 0) + 1`, insert a queued execution,
and update `latest_execution_id`. Worker claim SHALL, in a separate transaction,
lock that exact queued latest execution, mint a fresh `claim_token`, set
`worker_id`, `lease_expires_at = now() + LEASE_TTL`, change status to `claimed`,
set `current_execution_id`, and move the container to `running`. The claim
operation SHALL return the token to the claiming worker. `LEASE_TTL` SHALL
default to the existing build-lost grace value (300 seconds).

#### Scenario: Claim is atomic

- **GIVEN** a queued latest execution exists with null claim fields
- **WHEN** a worker claims that execution
- **THEN** the token, worker, lease expiry, status change, and container
  `current_execution_id` update all commit together or not at all

### Requirement: Heartbeat renews the lease under a valid token

The system SHALL expose a dedicated `POST /api/executions/{id}/heartbeat`
endpoint that, given an execution id and a `claim_token`, renews the active
execution's lease (`lease_expires_at = now() + LEASE_TTL`, `heartbeat_at =
now()`) only when the supplied token matches the row's current `claim_token`.
A heartbeat with a stale token SHALL be rejected without renewing the lease.

#### Scenario: Valid heartbeat extends the lease

- **GIVEN** an execution with a current `claim_token = K`
- **WHEN** a heartbeat is received carrying `claim_token = K`
- **THEN** `lease_expires_at` advances by `LEASE_TTL` and `heartbeat_at` is set

#### Scenario: Superseded-token heartbeat is rejected

- **GIVEN** lost execution `E1` used token `K1` and recovery execution `E2` is
  now current with token `K2`
- **WHEN** a heartbeat is received for `E1` carrying token `K1`
- **THEN** the heartbeat is rejected and the lease is not renewed

### Requirement: Terminal transition and publish are fenced by the current token

The system SHALL require a matching current `claim_token` before transitioning
an execution to a terminal status (`succeeded` / `failed`) or before the
publisher promotes any output into `work/challenges`. A request carrying a
stale token SHALL be rejected; the affected output SHALL remain noncanonical in
its archived execution workspace (or quarantine if already staged) and SHALL
NOT be published. A terminal write from any execution other than the
container's current execution SHALL be rejected and SHALL NOT overwrite the
container aggregate. A successful terminal write for the current execution
SHALL clear `current_execution_id` in the same transaction.

#### Scenario: Expired worker cannot publish

- **GIVEN** execution `E1`'s lease expired, `E1` was marked lost, and recovery
  execution `E2` became current while `E1`'s Hermes process is still running
- **WHEN** the still-running `E1` process requests publish or completion with
  its old token
- **THEN** the request is rejected, nothing is published, and `E1`'s output
  remains in quarantine

### Requirement: Reconciler terminates expired executions and recovery appends a run

The `BuildReconciler` SHALL act as a lease reaper over executions: it SHALL find
executions whose `status` is `claimed` or `running` and whose
`lease_expires_at` has passed without a fresh heartbeat, terminally mark them
`lost`, record the lease-expiry error, and clear the container's current
pointer. A later retry request SHALL create a new queued `retry` execution with
the lost execution as parent; automatic retry policy is out of scope for this
change. The old row and token SHALL never be reused or rotated.
The reaper SHALL use a conditional update requiring the execution to be the
container's current active execution with an expired lease.
Execution terminal transitions SHALL propagate to the
container's aggregate `build_attempts.status` and `latest_execution_id` only
when they apply to the container's current execution.

#### Scenario: Expired lease is reaped

- **GIVEN** an execution with `status = 'running'` and `lease_expires_at` in
  the past and no recent `heartbeat_at`
- **WHEN** the reconciler tick runs
- **THEN** the execution is marked `lost`, its current pointer is cleared, and
  its old token can no longer pass the current-execution fence
- **AND** a later retry schedules a new queued iteration whose worker
  receives a different token when it claims that iteration

### Requirement: Advancing an iteration isolates stale process writes

Before a queued iteration is claimed, the system SHALL atomically rename the
entire prior `current/` directory to its canonical `attempts/iter-NNN/` archive,
then create a new `current/` directory. It SHALL NOT move only the prior
directory's children. On POSIX systems this ensures a stale process retains the
renamed old cwd inode and cannot resolve relative writes into the new
iteration's `current/`.

#### Scenario: Stale process writes remain in the archived iteration

- **GIVEN** expired execution `E1` still has the old `current/` as its cwd
- **WHEN** the system atomically renames that directory to `attempts/iter-001/`
  and creates a new `current/` before claiming `E2`
- **THEN** later relative writes by `E1` remain under `attempts/iter-001/`
- **AND** `E2` writes only into the newly created `current/`

### Requirement: Revision claim materializes the prior iteration in place

A `revision` claim SHALL materialize the parent execution's output manifest,
base artifact, and human feedback snapshot into the new workspace's
`current/input/`. The base artifact SHALL be read **in place** from the same
container directory's `attempts/iter-<parent.iteration_no>/output`, without a
cross-directory copy path or a persisted filesystem-path lookup. A revision SHALL NOT claim an
unrelated shard.

#### Scenario: Revision reads the prior failure scene from the same directory

- **GIVEN** execution `E1` (iteration 1) failed and its output/logs were
  archived under `work/executions/<build_attempt_id>/attempts/iter-001/`
- **WHEN** a `revision` execution `E2` (iteration 2, parent `E1`) is claimed
- **THEN** `E2`'s `current/input/base-artifact` resolves to the deterministic
  `../../attempts/iter-001/output` and `previous-output-manifest.json` to
  `../../attempts/iter-001/manifest.json`
- **AND** `E2` materializes the feedback snapshot and does not claim any
  unrelated shard

### Requirement: Revalidate appends an event without creating an execution

A `revalidate` action SHALL NOT create a new `executions` row. It SHALL append a
row to `revalidation_events` carrying `id` (UUID PK), `execution_id` (FK,
`ON DELETE CASCADE`), `check_name` (TEXT), `result` (JSONB), `actor` (TEXT), and
`created_at` (TIMESTAMPTZ), and reuse the reconciler's existing validation
logic. The request SHALL require `current_execution_id IS NULL` and a terminal
`latest_execution_id`, so it cannot attach to a running or superseded run.

#### Scenario: Revalidate leaves the execution row count unchanged

- **GIVEN** a build attempt whose latest execution is `E2`
- **WHEN** the operator triggers `revalidate`
- **THEN** no new `executions` row is created and a `revalidation_events` record
  is appended referencing `E2`
