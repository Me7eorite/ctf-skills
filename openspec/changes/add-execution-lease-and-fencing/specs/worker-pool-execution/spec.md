## ADDED Requirements

### Requirement: Each run is persisted as an execution row under a build-attempt container

The system SHALL persist each individual build run as a row in a PostgreSQL
table `executions`. A `build_attempts` row SHALL act as a per-challenge
**container**; one or more `executions` rows SHALL belong to it, ordered by a
monotonic `iteration_no`. Each `executions` row SHALL carry: `id` (UUID PK),
`build_attempt_id` (FK to `build_attempts.id`, `ON DELETE CASCADE`, NOT NULL),
`parent_execution_id` (FK to `executions.id`, nullable), `iteration_no`
(positive integer), `execution_kind` (one of `initial`, `retry`, `revision`),
`execution_mode` (one of `standard`, `clean`; `clean` is only valid when
`execution_kind = 'retry'`), `worker_id` (TEXT nullable), `claim_token` (UUID NOT
NULL),
`lease_expires_at` (TIMESTAMPTZ NOT NULL), `heartbeat_at` (TIMESTAMPTZ
nullable), `status` (one of `claimed`, `running`, `succeeded`, `failed`,
`lost`), `exit_class` (TEXT nullable), `started_at` / `finished_at`
(TIMESTAMPTZ nullable), and `created_at` (TIMESTAMPTZ NOT NULL).

The compound unique key `(build_attempt_id, iteration_no)` SHALL hold. An
`execution_kind` of `revision` SHALL require a non-null `parent_execution_id`.
An `execution_mode` of `clean` SHALL require `execution_kind = 'retry'`.
`executions` SHALL be the source of truth for per-run state; the container row's
status and run metadata SHALL be derived from execution transitions in the same
transaction.
On claim and on lease recovery, `current_execution_id` and `latest_execution_id`
SHALL be identical; the active execution is always the latest execution.

#### Scenario: Initial run inserts iteration 1

- **WHEN** a fresh build attempt is claimed for the first time
- **THEN** exactly one `executions` row is inserted with `iteration_no = 1`,
  `execution_kind = 'initial'`, `parent_execution_id` null, and a non-null
  `claim_token` and `lease_expires_at`

#### Scenario: Revision without a parent is rejected

- **WHEN** an `executions` row with `execution_kind = 'revision'` is inserted
  with a null `parent_execution_id`
- **THEN** the database rejects the row via a check constraint

#### Scenario: Clean execution mode requires retry kind

- **WHEN** an `executions` row is inserted with `execution_mode = 'clean'`
  and `execution_kind != 'retry'`
- **THEN** the database rejects the row via a check constraint

### Requirement: At most one active execution per build-attempt container

The system SHALL enforce that no `build_attempts` container has more than one
`executions` row whose `status` is `claimed` or `running` at any moment. This
SHALL be implemented as a PostgreSQL partial unique index on
`executions (build_attempt_id)` filtered by `status IN ('claimed', 'running')`.

#### Scenario: Concurrent second claim is rejected at the database

- **GIVEN** a `claimed` execution already exists for `build_attempt_id = B`
- **WHEN** a second claim attempts to insert another `claimed` execution for
  `B`
- **THEN** PostgreSQL raises a unique-violation error and the second claim
  fails

### Requirement: Claim mints a fencing token and lease in one transaction

When a worker claims the next execution for a build attempt, the system SHALL,
in a single database transaction: lock the build attempt row, allocate
`iteration_no = COALESCE(MAX(iteration_no), 0) + 1`, mint a fresh
`claim_token`, set `lease_expires_at = now() + LEASE_TTL`, insert the
`executions` row with `status = 'claimed'`, and set the container's
`current_execution_id` and `latest_execution_id`. `LEASE_TTL` SHALL default to
the existing build-lost grace value (300 seconds).

#### Scenario: Claim is atomic

- **WHEN** a build attempt is claimed
- **THEN** the new execution row, the minted token, the lease expiry, and the
  container's `current_execution_id`/`latest_execution_id` updates all commit
  together or not at all

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

#### Scenario: Stale-token heartbeat is rejected

- **GIVEN** an execution whose lease was recovered and re-minted to token `K2`
- **WHEN** a heartbeat is received carrying the old token `K1`
- **THEN** the heartbeat is rejected and the lease is not renewed

### Requirement: Terminal transition and publish are fenced by the current token

The system SHALL require a matching current `claim_token` before transitioning
an execution to a terminal status (`succeeded` / `failed`) or before the
publisher promotes any output into `work/challenges`. A request carrying a
stale token SHALL be rejected; the affected output SHALL be left in quarantine
and SHALL NOT be published. A terminal write from any execution other than the
container's current execution SHALL be rejected and SHALL NOT overwrite the
container aggregate.

#### Scenario: Expired worker cannot publish

- **GIVEN** execution `E1`'s lease expired and was recovered, re-minting a new
  token while `E1`'s Hermes process is still running
- **WHEN** the still-running `E1` process requests publish or completion with
  its old token
- **THEN** the request is rejected, nothing is published, and `E1`'s output
  remains in quarantine

### Requirement: Reconciler reaps expired leases and re-mints tokens

The `BuildReconciler` SHALL act as a lease reaper over executions: it SHALL find
executions whose `status` is `claimed` or `running` and whose
`lease_expires_at` has passed without a fresh heartbeat, mark them `lost`, and,
when recovering the work, re-mint the `claim_token` so the prior process's
token is invalidated. Execution terminal transitions SHALL propagate to the
container's aggregate `build_attempts.status` and `latest_execution_id` only
when they apply to the container's current execution.

#### Scenario: Expired lease is reaped

- **GIVEN** an execution with `status = 'running'` and `lease_expires_at` in
  the past and no recent `heartbeat_at`
- **WHEN** the reconciler tick runs
- **THEN** the execution is marked `lost` and, on recovery, a new `claim_token`
  is minted that invalidates the prior token

### Requirement: Revision claim materializes the prior iteration in place

A `revision` claim SHALL materialize the parent execution's output manifest,
base artifact, and human feedback snapshot into the new workspace's
`current/input/`. The base artifact SHALL be read **in place** from the same
challenge directory's `attempts/iter-(N-1)/output`, without a cross-directory
copy path or a database-driven path lookup. A revision SHALL NOT claim an
unrelated shard.

#### Scenario: Revision reads the prior failure scene from the same directory

- **GIVEN** execution `E1` (iteration 1) failed and its output/logs were
  archived under `work/executions/<build_attempt_id>/attempts/iter-1/`
- **WHEN** a `revision` execution `E2` (iteration 2, parent `E1`) is claimed
- **THEN** `E2`'s `current/input/base-artifact` resolves to
  `../../attempts/iter-1/output` and `previous-output-manifest.json` to
  `../../attempts/iter-1/manifest.json`
- **AND** `E2` materializes the feedback snapshot and does not claim any
  unrelated shard

### Requirement: Revalidate appends an event without creating an execution

A `revalidate` action SHALL NOT create a new `executions` row. It SHALL append a
`revalidation_events` record (check name, result, timestamp, actor) to the
container's latest execution and reuse the reconciler's existing validation
logic. The request SHALL be rejected if `latest_execution_id` and
`current_execution_id` do not match, so it never attaches to a stale run.

#### Scenario: Revalidate leaves the execution row count unchanged

- **GIVEN** a build attempt whose latest execution is `E2`
- **WHEN** the operator triggers `revalidate`
- **THEN** no new `executions` row is created and a `revalidation_events` record
  is appended referencing `E2`
