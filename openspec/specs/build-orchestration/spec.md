# build-orchestration Specification

## Purpose
TBD - created by archiving change add-build-attempts. Update Purpose after archive.
## Requirements
### Requirement: build_attempts table is the editorial unit of building

The system SHALL persist build orchestration state in a PostgreSQL table
`build_attempts`. Each row SHALL represent one operator-initiated
submission of a single design task for building. Rows SHALL carry
`id` (UUID PK), `design_task_id` (FK to `design_tasks.id`),
`attempt_no` (positive integer, unique per design task), `status`
(one of `queued`, `running`, `succeeded`, `failed`, `lost`),
`shard_basename` (TEXT, the basename written into
`work/shards/pending/`), `worker` (TEXT nullable),
`resulting_challenge_dir` (TEXT nullable), `error` (TEXT nullable),
`artifact_status` (TEXT NOT NULL DEFAULT `unknown`, one of `unknown`,
`present`, `missing`),
`created_at` (TIMESTAMPTZ NOT NULL DEFAULT now()), `started_at`
(TIMESTAMPTZ nullable, set when the row enters `running`), and
`finished_at` (TIMESTAMPTZ nullable, set when the row reaches a
terminal status).

The unique compound key `(design_task_id, attempt_no)` SHALL hold.

#### Scenario: First attempt receives attempt_no = 1

- **WHEN** the operator submits a `designed` design task for building
- **THEN** exactly one row is inserted with `attempt_no = 1`,
  `status = 'queued'`, `shard_basename` matching the rendered shard
  file's basename, and `worker` left null
- **AND** `created_at` is server-set; `started_at` and `finished_at`
  remain null

#### Scenario: Retry attempts increment monotonically

- **GIVEN** the latest attempt for a design task has `attempt_no = 3`
  and `status = 'failed'`
- **WHEN** the operator retries it
- **THEN** a new row is inserted with `attempt_no = 4`,
  `status = 'queued'`, and a fresh attempt-specific `shard_basename`

### Requirement: Only one build attempt per design task may be active

The system SHALL enforce that no design task has more than one
`build_attempts` row whose `status` is `queued` or `running` at any
moment. This SHALL be implemented as a PostgreSQL partial unique
index on `build_attempts (design_task_id)` filtered by
`status IN ('queued', 'running')`.

#### Scenario: Concurrent submit is rejected at the database

- **GIVEN** a `build_attempts` row exists with
  `(design_task_id = T, status = 'queued')`
- **WHEN** a second `INSERT` is attempted for the same `design_task_id`
  with `status = 'queued'`
- **THEN** PostgreSQL raises a unique-violation error
- **AND** the orchestration service surfaces the conflict as a
  validation error to the API caller

#### Scenario: Terminal status frees the slot

- **GIVEN** the only active row for `design_task_id = T` transitions
  from `running` to `failed`
- **WHEN** the operator submits a retry for `T`
- **THEN** the new `queued` row is accepted

### Requirement: build_attempts five-state machine

`build_attempts.status` SHALL follow the transitions:

- `queued -> running`: the reconciler observed both (a) an attributed generated
  shard with matching top-level `build_attempt_id` under `running/`, and (b)
  the current `shard_basename`'s shard-level `queued/running` progress claim
  event. A running file without that event is insufficient because it may be a
  dry-run claim.
- `queued -> lost` or `running -> lost`: the generated shard with
  matching top-level `build_attempt_id` is not present under any of
  `pending/`, `running/`, `done/`, `failed/`.
- `running -> succeeded`: the shard moved to `done/`,
  `work/challenges/<category>/<challenge_id>-<slug>/metadata.json` exists, and
  its `solve_status == 'passed'`.
- `running -> failed`: the shard moved to `failed/`, OR moved to
  `done/` and the artifact directory exists but `solve_status` is not
  `passed`.
- `queued -> succeeded` or `queued -> failed`: claim and completion both
  occurred between reconciler ticks. The reconciler SHALL apply the equivalent
  logical `queued -> running -> terminal` transition in one transaction and
  recover `worker` from the claim sidecar or shard report.
- A `done/` shard whose expected artifact directory is missing at the first
  terminal observation SHALL result in `failed`, because successful artifact
  production cannot be established. A directory removed after a recorded
  success SHALL leave `status = succeeded` and change only
  `artifact_status = missing`.

Terminal statuses (`succeeded`, `failed`, `lost`) SHALL be terminal:
no transitions out except by creating a new attempt with
`attempt_no + 1`.

`started_at` SHALL be set when transitioning to `running`;
`finished_at` SHALL be set when transitioning to a terminal status.
For a skipped-running transition, both timestamps SHALL be populated during
the terminal update. Claim-sidecar `claimed_at` SHALL be preferred for
`started_at`, with reconciliation time as fallback.

#### Scenario: Shard claim transitions the row to running

- **WHEN** the reconciler observes
  `work/shards/running/web-0001.hermes-02.json`
  for a row whose `shard_basename = 'web-0001.json'` and
  `status = 'queued'`, and the shard payload's top-level
  `build_attempt_id` equals that row's `id`, and a shard-level
  `queued/running` progress claim event exists for `web-0001.json`
- **THEN** the row's `status` becomes `running`,
  `worker` becomes `'hermes-02'`, and `started_at` is set

#### Scenario: Done shard with passed artifact promotes to succeeded

- **WHEN** the reconciler observes
  `work/shards/done/web-0001.json` and
  `work/challenges/web/web-0001-flag-leak/metadata.json` exists with
  `solve_status = 'passed'`
- **THEN** the row's `status` becomes `succeeded`,
  `resulting_challenge_dir` becomes
  `'work/challenges/web/web-0001-flag-leak'`, `artifact_status` becomes
  `present`, and `finished_at` is set

#### Scenario: Done shard with missing artifact fails the build

- **WHEN** the reconciler observes `work/shards/done/web-0001.json`
  but no directory under `work/challenges/` matches the challenge id
- **THEN** the row's `status` becomes `failed`, `error` summarizes
  "artifact directory missing", and `finished_at` is set

#### Scenario: Artifact removed after success changes availability only

- **GIVEN** an attempt has `status = 'succeeded'` and
  `artifact_status = 'present'`
- **WHEN** its recorded `resulting_challenge_dir` disappears
- **THEN** the reconciler sets `artifact_status = 'missing'`
- **AND** attempt status remains `succeeded`
- **AND** the parent design task remains `built`

#### Scenario: Fast completion skips an observed running state

- **GIVEN** an attempt is still `queued` in PostgreSQL
- **WHEN** its attributed shard and claim sidecar are first observed under
  `done/` with a passed artifact
- **THEN** the attempt becomes `succeeded` in one tick
- **AND** `worker`, `started_at`, and `finished_at` are populated

#### Scenario: Dry-run claim leaves queued status unchanged

- **GIVEN** an attributed attempt is `queued`
- **WHEN** dry-run temporarily moves its shard into `running/` without writing
  a progress claim event, then restores it to `pending/`
- **THEN** the reconciler does not promote the attempt to `running`
- **AND** the attempt remains `queued`

#### Scenario: Shard vanishing from disk marks running attempts lost

- **GIVEN** a row with `status = 'running'`,
  `shard_basename = 'web-0001.json'`, and `id = A`
- **WHEN** no shard payload with top-level `build_attempt_id = A` is
  present under `pending/`, `running/`, `done/`, or `failed/` for a
  full reconciler tick
- **THEN** the row's `status` becomes `lost` immediately on the next
  tick with no grace period

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

### Requirement: BuildReconciler mirrors filesystem state to PostgreSQL

The system SHALL provide `services.BuildReconciler` running as a
daemon thread launched by `web.server.serve(...)`. It SHALL poll
`work/shards/{pending,running,done,failed}/` on a fixed interval read from
`BUILD_RECONCILER_POLL_SECONDS` (default 5; non-positive or
non-integer values SHALL fall back to the default and emit a
warning).

Each tick SHALL first run staging recovery as a bounded filesystem step, then
run row reconciliation in one short PostgreSQL transaction:

0. Recover staged submissions: publish staged payloads attributed to committed
   queued rows and remove staged payloads older than one hour that have no
   database row. If publication of an attributed staged payload fails, record a
   warning and treat that staged payload as present for this tick; no database
   state change is required merely to remember that publication failed.
1. Match shards under `work/shards/running/` by reading the shard
   JSON payload's top-level `build_attempt_id`. If the field is absent
   or does not name a non-terminal `build_attempts` row, ignore the
   shard. For a valid row, verify the row's `shard_basename` equals
   the original pending basename represented by the running filename
   `<basename>.<worker>.json`; then require a shard-level `queued/running`
   progress claim event for that original basename before promoting `queued`
   to `running` and setting `worker` / `started_at`. Without the event, leave
   the queued row unchanged for this tick.
2. For shards under `work/shards/done/`, first attribute the shard by
   payload `build_attempt_id` using the same ignore/verify rules, then
   inspect the corresponding
   `work/challenges/<category>/<challenge_id>-<slug>/metadata.json`;
   choose `succeeded` / `failed` per the state machine. If the row is still
   `queued`, apply the fast-completion transition and recover claim metadata
   from the sidecar or report.
3. For shards under `work/shards/failed/`, first attribute the shard
   by payload `build_attempt_id` using the same ignore/verify rules,
   then transition the matching non-terminal row to `failed`, summarizing the
   cause from the shard report. A queued row MAY transition directly as the
   fast-completion case defined above.
4. For non-terminal rows whose generated shard with matching top-level
   `build_attempt_id` is absent from all four queue directories **and** from
   `work/shards/staging/build-attempts/`, set `status = 'lost'`. An attributed
   staged payload counts as present even when its publication failed during
   this tick, so its row remains `queued` and can be retried later. A
   basename-colliding shard without the matching `build_attempt_id` does not
   count as present.
5. Roll the parent `design_tasks.status` forward based on the
   highest-`attempt_no` row: `succeeded` -> `built`,
   `failed`/`lost` -> `build_failed`.
6. Recheck `resulting_challenge_dir` for succeeded rows. Set
   `artifact_status` to `present` or `missing` without changing attempt or
   parent-task status. Availability MAY return from `missing` to `present`.

Recovery and queue observation SHALL be idempotent when `/api/state` and the
daemon tick overlap.

The same staging recovery SHALL run once during server startup before the
daemon begins polling.

A connection failure inside a tick SHALL be logged as a warning and
the tick SHALL be skipped; the reconciler thread SHALL NOT crash.

`web.server.create_app(...)` SHALL be able to receive or read one optional
reconciler instance (for example from `app.state.build_reconciler`).
`/api/state` SHALL synchronously invoke one reconciler tick when that instance
is present before returning, so the operator's immediate next view of the
dashboard reflects newly-completed shards.

#### Scenario: Reconciler interval is configurable via environment

- **WHEN** `BUILD_RECONCILER_POLL_SECONDS=12` is set and
  `web.server.serve(...)` starts
- **THEN** the daemon thread polls every 12 seconds
- **AND** at most one reconciler thread is started per server process

#### Scenario: Invalid interval falls back to default

- **WHEN** `BUILD_RECONCILER_POLL_SECONDS=0` or `not-a-number` is set
- **THEN** the reconciler uses the 5-second default
- **AND** a warning is logged once at startup

#### Scenario: Reconciler is resilient to PostgreSQL hiccups

- **GIVEN** the reconciler thread is running
- **WHEN** PostgreSQL is briefly unreachable during a tick
- **THEN** the tick is skipped, a warning is logged, and the thread
  remains alive for the next tick

#### Scenario: API state request triggers a synchronous tick

- **WHEN** an operator hits `/api/state`
- **THEN** the response includes
  the build state at most one tick old (the handler triggers a tick
  before serializing)

### Requirement: Shard JSON schema is matrix-shaped plus design context

Shard JSON files emitted by the orchestration service SHALL preserve
the existing `{"challenges": [...]}` envelope used by hand-written
matrix shards and SHALL add the following optional top-level
fields:

- `build_attempt_id`: the UUID of the `build_attempts` row that
  emitted this shard.
- `design_task_id`: the UUID of the parent design task.
- `resume_from_shard_basename`: omitted on initial submission; on retry, the
  source attempt's basename used only for historical resume reads.

Each entry in `challenges[]` SHALL include every field already
written by the existing matrix conventions (`id`, `category`,
`difficulty`, `primary_technique`, `learning_objective`, `points`,
`port`, `title`, plus category-specific keys such as `runtime`,
`framework`, `compiler`, `mitigations`, `target_platform`) AND a
`design` sub-object whose value SHALL be the validated
`challenge_designs.payload` content (deployment, artifacts,
flag_location, validation, hints, prompt, etc.).

Existing hand-written matrix shards that omit `build_attempt_id`,
`design_task_id`, `resume_from_shard_basename`, and `design` SHALL continue to be valid input to
the runner. The reconciler SHALL ignore shards that lack
`build_attempt_id`; build attribution SHALL be driven by the payload's
`build_attempt_id`, with `shard_basename` used only as a consistency
check and for progress-event lookup.

#### Scenario: Generated shard carries traceability ids

- **WHEN** the orchestration service renders a shard for design
  task T (with the latest validated design D and build attempt A)
- **THEN** the file contains `build_attempt_id = A`,
  `design_task_id = T`, and exactly one challenge entry
- **AND** that entry includes both the matrix fields and a `design`
  sub-object derived from D

#### Scenario: Retry shard carries its resume source

- **WHEN** retry attempt A2 is created from terminal attempt A1
- **THEN** A2's payload contains
  `resume_from_shard_basename = A1.shard_basename`
- **AND** its own `shard_basename` remains different from A1's

#### Scenario: Hand-written matrix shard is still accepted

- **WHEN** an operator hand-writes a matrix shard with no top-level
  `build_attempt_id` and submits it via `challenge-factory split`
- **THEN** the runner processes it normally
- **AND** the reconciler never tries to attribute its outcome to a
  `build_attempts` row

#### Scenario: Basename collision without build_attempt_id is ignored

- **GIVEN** a hand-written shard has the same filename as a
  `build_attempts.shard_basename` but no top-level `build_attempt_id`
- **WHEN** that shard appears under `running/`, `done/`, or `failed/`
- **THEN** the reconciler ignores it
- **AND** no `build_attempts` row changes status because of that shard

### Requirement: HTTP API exposes build orchestration

The dashboard backend SHALL register the following endpoints in
`web/build_attempts_endpoints.py`, registered BEFORE the static
catch-all in `web/server.py`:

- `POST /api/design-tasks/build` with body
  `{"design_task_ids": [UUID, ...]}`; returns `201` with body
  `{"build_attempt_ids": [UUID, ...]}` ordered by input.
- `POST /api/design-tasks/{id}/build` with empty body; returns
  `201` with body `{"build_attempt_id": UUID}`.
- `GET /api/build-attempts?status=&worker=&design_task_id=&generation_request_id=&category=&limit=`;
  returns `200` with a JSON array of "folded" rows (one per design
  task, exposing only its highest-`attempt_no` row) joined with the
  parent design task title/category and the latest derived percent
  from `progress_snapshots`. Rows SHALL include `artifact_status` and SHOULD
  include a concise failure summary derived from progress evidence.
- `GET /api/build-attempts/{id}`; returns `200` with the row plus
  `sibling_attempts` (all attempts for the same design task ordered
  by `attempt_no` ascending), `progress_events` (for the row's
  shard, with `carry-forward:` events preserved), and
  `resulting_challenge_dir`, `artifact_status`, and a concise failure summary
  when present.
- `POST /api/build-attempts/{id}/retry` with empty body; returns
  `201` with body `{"build_attempt_id": UUID}` (the new attempt), or `409`
  when the attempt is not the latest failed/lost sibling or its parent is not
  `build_failed`.
- `POST /api/build-attempts/{id}/revalidate` with empty body; re-runs host
  validation for the same failed attempt without creating a new attempt or
  invoking Hermes. It returns `200` with the repaired attempt payload when the
  same attempt becomes `succeeded`, or `409` with a precise error when the
  attempt is ineligible or validation still fails.

The list endpoint SHALL apply `BUILD_ATTEMPTS_LIST_DEFAULT_LIMIT`
(default 100) when no `limit` is given, SHALL cap at
`BUILD_ATTEMPTS_LIST_MAX_LIMIT` (default 500), and SHALL reject
malformed `limit` values with `400`. Both knobs are read at module
import time from the environment, falling back to defaults on
missing or invalid values with a warning.

Unknown filter values (e.g. `?status=invalid` or `?category=crypto`)
and malformed UUID filters SHALL be rejected with `400`.

The list query SHALL fold before filtering: first select the highest-
`attempt_no` row for every design task, then apply `status`, `worker`,
`design_task_id`, `generation_request_id`, and `category` filters to that
latest-row relation, then order and limit the result. A filter SHALL never
cause an older sibling attempt to be exposed as the folded row.

#### Scenario: Revalidate repairs a failed attempt without creating a sibling

- **GIVEN** build attempt A is the latest attempt for design task T
- **AND** A has `status = failed`
- **AND** `work/shards/failed/{A.shard_basename}` is an attributed shard for A
- **AND** the challenge directory now exists with complete validation evidence
- **WHEN** `POST /api/build-attempts/{A.id}/revalidate` is invoked
- **THEN** no new `build_attempts` row is created
- **AND** A becomes `succeeded`
- **AND** T becomes `built`
- **AND** the shard file moves from `failed/` to `done/`
- **AND** fresh `validate/passed` and `complete/passed` progress events are
  recorded for A's shard basename

#### Scenario: Revalidate failure keeps the same failed attempt

- **GIVEN** latest build attempt A has `status = failed`
- **AND** its failed shard is present
- **WHEN** `POST /api/build-attempts/{A.id}/revalidate` is invoked but the
  challenge directory is missing or validation fails
- **THEN** the response status is `409`
- **AND** no new build attempt is created
- **AND** A remains `failed`
- **AND** the failed shard remains under `failed/`
- **AND** A.error contains the precise validation reason

#### Scenario: Revalidate rejects non-failed or stale attempts

- **WHEN** `POST /api/build-attempts/{id}/revalidate` names a queued, running,
  succeeded, lost, or stale older failed attempt
- **THEN** the response status is `409`
- **AND** no queue files move
- **AND** no new attempt is created

#### Scenario: Status filter applies only to the latest attempt

- **GIVEN** task T has attempt #1 `failed` and latest attempt #2 `queued`
- **WHEN** `GET /api/build-attempts?status=failed` is invoked
- **THEN** task T is not returned
- **AND** attempt #1 is not substituted for the latest row

#### Scenario: Batch submit returns ordered ids

- **WHEN** `POST /api/design-tasks/build` is invoked with
  `{"design_task_ids": [A, B, C]}` where all three are `designed`
- **THEN** the response status is `201`
- **AND** `build_attempt_ids` has length 3 in the same A-B-C order

#### Scenario: List is folded by design task

- **GIVEN** design task T has attempts #1 (failed), #2 (succeeded),
  and #3 (queued)
- **WHEN** `GET /api/build-attempts?design_task_id=T` is invoked
- **THEN** the response contains exactly one row
- **AND** that row's `attempt_no` is 3 and `status` is `queued`

#### Scenario: Detail exposes sibling attempts in order

- **WHEN** `GET /api/build-attempts/{id}` is invoked for attempt
  #2 of design task T
- **THEN** the response includes `sibling_attempts` containing
  attempts #1, #2, #3 ordered by `attempt_no` ascending
- **AND** `progress_events` includes events whose `shard` matches
  the row's `shard_basename`, including any `carry-forward:`
  entries written by the runner

#### Scenario: Limit cap is honored

- **WHEN** `GET /api/build-attempts?limit=10000` is invoked with
  `BUILD_ATTEMPTS_LIST_MAX_LIMIT=500`
- **THEN** at most 500 rows are returned
- **AND** the response header `X-Limit-Capped: 500` is set

#### Scenario: Stale retry is an HTTP conflict

- **WHEN** `POST /api/build-attempts/{id}/retry` names a failed attempt that
  has a newer sibling
- **THEN** the response status is `409`
- **AND** no new attempt is created

### Requirement: Attributed shards cannot use legacy requeue

`POST /api/shards/{state}/{name}/requeue` SHALL preserve its existing behavior
for shard payloads without `build_attempt_id`. If the source payload has a
non-empty `build_attempt_id`, the endpoint SHALL return `409`, SHALL NOT move
the file, and SHALL identify `/api/build-attempts/{build_attempt_id}/retry` as
the supported retry path.

#### Scenario: Legacy requeue rejects an attributed failed shard

- **GIVEN** `work/shards/failed/X.json` contains `build_attempt_id = A`
- **WHEN** `POST /api/shards/failed/X.json/requeue` is invoked
- **THEN** the response status is `409`
- **AND** the shard remains under `failed/`
- **AND** no new attempt is created implicitly

#### Scenario: Hand-written shard requeue remains compatible

- **GIVEN** a failed shard has no `build_attempt_id`
- **WHEN** the legacy requeue endpoint is invoked
- **THEN** its existing requeue behavior is unchanged

### Requirement: 构建任务 view follows the Design Tasks layout

The dashboard SHALL expose a top-level navigation entry `构建记录`
(slug `build-attempts`). The list view SHALL render a filter bar above a table.
Filter bar fields SHALL include `状态`, `Worker`, `分类` (web/pwn/re),
`设计任务` (UUID input), and `生成请求` (UUID input). The list filter bar SHALL
include `应用筛选`, `清空`, and `刷新`; it SHALL NOT include global
`Start Worker`, `Validate`, `启动 Worker`, or `重新验证` actions.
The `生成请求` filter SHALL initialize from the route's
`generation_request_id` query parameter and remain editable.

The list table SHALL use Chinese column labels: `题目`, `分类`, `难度`, `状态`,
`产物`, `进度`, `Worker`, `次数`, `创建时间`, and `操作`. Row actions SHALL include
`详情` and `删除`; rows whose latest attempt is `failed` or `lost` SHALL also
show `重试构建`. `重试构建` SHALL continue to call the retry endpoint and create a
new attempt.

The detail view SHALL be titled `构建运行 #N`, where `N` is the attempt number.
It SHALL show Chinese field labels including `设计任务`, `分片`, `Worker`,
`开始时间`, `完成时间`, `产物目录`, and `失败原因`. Detail actions SHALL be scoped to
the inspected attempt:

- `queued`: show `运行`.
- `failed`: show `重新校验`, `重试构建`, and `删除`.
- `lost`: show `重试构建` and `删除`.
- `succeeded`: show `删除`.

The detail view SHALL keep the existing sibling-attempt history and progress
events sections, with Chinese section titles `尝试历史` and `进度事件`.
The queued-attempt `运行` action SHALL call the constrained
`POST /api/build-attempts/{id}/worker/start` endpoint for the inspected attempt
and SHALL NOT call the legacy global `POST /api/actions/worker` endpoint.

The application-wide header SHALL NOT expose worker, validation, refresh, or
sync-time controls. The list-level `刷新` action SHALL call `/api/state` first
to trigger a synchronous reconciler tick, then refetch `/api/build-attempts`.
The legacy global worker and validation endpoints SHALL remain available to
explicit API clients.

The UI SHALL localize build attempt status labels as `待运行`, `运行中`, `成功`,
`失败`, and `丢失`. It SHALL localize artifact labels as `已生成`, `缺失`, and
`未知`.

#### Scenario: List view no longer exposes global execution actions

- **WHEN** the operator opens `#/build-attempts`
- **THEN** the list filter bar shows filters and `刷新`
- **AND** it does not show `Start Worker`, `Validate`, `启动 Worker`, or
  `重新验证`

#### Scenario: Queued attempt detail can be run

- **GIVEN** build attempt A has `status = queued`
- **WHEN** the operator opens `#/build-attempts/{A.id}`
- **THEN** the detail action bar shows `运行`
- **AND** activating it calls `/api/build-attempts/{A.id}/worker/start`
- **AND** no unrelated shard may be claimed

#### Scenario: Generation request route initializes the editable filter

- **WHEN** the operator opens `#/build-attempts?generation_request_id=R`
- **THEN** the `生成请求` filter is initialized to R
- **AND** the operator can edit or clear it

#### Scenario: Global header does not expose build actions

- **WHEN** the dashboard renders any view
- **THEN** the application-wide header contains no worker, validation, refresh,
  or sync-time action

#### Scenario: Refresh triggers reconciliation before refetch

- **WHEN** the operator clicks `刷新` in the build-attempt list
- **THEN** the frontend calls `/api/state` before `/api/build-attempts`

#### Scenario: Failed attempt detail distinguishes revalidate from retry

- **GIVEN** build attempt A has `status = failed`
- **WHEN** the operator opens `#/build-attempts/{A.id}`
- **THEN** the detail action bar shows both `重新校验` and `重试构建`
- **AND** `重新校验` calls `/api/build-attempts/{A.id}/revalidate`
- **AND** `重试构建` calls `/api/build-attempts/{A.id}/retry`

#### Scenario: Failure reason prefers progress evidence

- **GIVEN** a failed attempt has `build_attempts.error = "shard execution failed"`
- **AND** its latest `validate/failed` progress event contains
  `error=missing_challenge`
- **WHEN** the list or detail view renders the attempt
- **THEN** it shows a Chinese failure summary equivalent to
  `校验失败：missing_challenge`
- **AND** it does not show only `shard execution failed`

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

### Requirement: Constrained build dispatch selects only eligible attributed shards

The generated shard payload SHALL remain the execution input for Hermes.
Build-attempt dispatch SHALL select work by verified attribution and SHALL NOT
rely on filename ordering to choose which
category or build attempt to execute. Any service or endpoint that starts a
build-attempt worker for a category SHALL claim only attributed shards whose
payload has a non-empty top-level `build_attempt_id` and whose challenge
entries all match that category. Any service or endpoint that starts a worker
for a single build attempt SHALL claim only the shard whose payload has that
top-level `build_attempt_id`.

#### Scenario: Category-constrained build dispatch does not claim another category

- **GIVEN** `work/shards/pending/` contains a pending Pwn shard and a pending
  Web shard
- **WHEN** the operator starts a Web-constrained build worker
- **THEN** only an attributed shard whose payload challenge categories are all
  `web` may be moved to `running/`
- **AND** the Pwn shard remains pending

#### Scenario: Category-constrained build dispatch ignores legacy category shards

- **GIVEN** `work/shards/pending/` contains a hand-written legacy Web shard
  with no top-level `build_attempt_id`
- **AND** it contains an attributed Web build-attempt shard
- **WHEN** the operator starts a Web-constrained build-attempt worker
- **THEN** only the attributed Web shard may be moved to `running/`
- **AND** the legacy Web shard remains pending

#### Scenario: Build-attempt-constrained dispatch claims only the named attempt

- **GIVEN** two attributed pending shards exist with different top-level
  `build_attempt_id` values
- **WHEN** the operator starts a worker for build attempt `A`
- **THEN** only the shard whose payload has `build_attempt_id = A` may be
  moved to `running/`
- **AND** the other attributed shard remains pending

### Requirement: HTTP API exposes constrained build-worker starts

The dashboard backend SHALL expose constrained build-worker start endpoints in
addition to the existing build submission, list, detail, and retry endpoints:

- `POST /api/build-attempts/worker/start` with body `{"category": "<category>"}`
  selects one DB-known queued build attempt whose parent design task has that
  category, then starts a local worker constrained to that build attempt id.
  The selected row SHALL be the first eligible row ordered by
  `(build_attempts.created_at ASC, build_attempts.id ASC)` after staging
  recovery and pending-shard matching. The endpoint SHALL reject missing or
  unsupported categories with `400`.
- `POST /api/build-attempts/{id}/worker/start` with an empty body starts a
  local worker constrained to the named build attempt. The endpoint SHALL
  return `404` for malformed or unknown ids.

Both endpoints SHALL run build staging recovery before checking for a matching
pending shard. A matching pending shard SHALL have the exact persisted
`shard_basename`; its payload SHALL match the selected build-attempt id,
design-task id, and design-task category. Both launch forms SHALL pass the
selected attempt id and its DB-known category to the runner. The category
endpoint SHALL return `409` when no queued DB-known
attempt in that category has a matching pending shard after recovery. The
single-attempt endpoint SHALL return `409` when the named attempt is not
`queued` or when no matching pending shard exists after recovery. Both
endpoints SHALL return `409` when another local dashboard task is already
running. The final busy check and subprocess creation SHALL be one atomic local
task-manager operation. A successful start SHALL return `202` and the selected
`build_attempt_id`; the exact-attempt subprocess SHALL run without `--loop`.

#### Scenario: Category worker endpoint starts one DB-known attempt

- **GIVEN** queued Web build attempts `A` and `B` both have matching pending
  attributed shards
- **AND** `A` sorts before `B` by `(created_at, id)`
- **WHEN** `POST /api/build-attempts/worker/start` is called with
  `{"category": "web"}`
- **THEN** the backend starts the worker constrained to `build_attempt_id = A`
- **AND** the worker is also constrained to A's DB-known category
- **AND** no legacy Web shard or unknown attributed Web shard is eligible for
  that invocation

#### Scenario: Attempt worker endpoint recovers staging before conflict

- **GIVEN** build attempt `A` is `queued` and its matching staged payload is
  still under `work/shards/staging/build-attempts/`
- **WHEN** `POST /api/build-attempts/A/worker/start` is called
- **THEN** staging recovery runs before pending-shard matching
- **AND** the request is not rejected merely because immediate publication had
  not previously run

#### Scenario: Terminal attempt cannot be started

- **GIVEN** build attempt `A` is `succeeded`, `failed`, or `lost`
- **WHEN** `POST /api/build-attempts/A/worker/start` is called
- **THEN** the response is `409`
- **AND** no worker process is started

#### Scenario: Exact start rejects a mismatched attributed payload

- **GIVEN** queued build attempt `A` names shard basename `A.json`
- **AND** pending `A.json` has a different `design_task_id` or challenge category
- **WHEN** the exact-attempt start endpoint evaluates `A`
- **THEN** the endpoint returns `409`
- **AND** the mismatched shard is not claimed

#### Scenario: Category start skips a mismatched attributed payload

- **GIVEN** queued build attempt `A` sorts before eligible attempt `B` in the
  requested category
- **AND** A's pending payload does not match A's design-task id or category
- **WHEN** the category start endpoint evaluates the queue
- **THEN** A is skipped and B is selected
- **AND** A's mismatched shard is not claimed

### Requirement: Existing per-attempt revalidation is race-safe and recoverable

The dashboard backend SHALL retain `POST /api/build-attempts/{id}/revalidate`
for the latest failed attempt and SHALL harden its existing validation, queue,
and status updates while retaining progress events keyed by the row's
`shard_basename`.

The endpoint MUST:

- Reject any non-failed or stale sibling and preserve the current failed-shard
  identity checks.
- Prefer a valid recorded `resulting_challenge_dir`; otherwise resolve exactly
  one current directory whose metadata id matches the attributed challenge.
- Serialize the attempt with a PostgreSQL advisory lock across the validator
  subprocess; a duplicate request returns `409` before writing progress.
- Preserve the existing `dashboard-revalidate` worker and
  `validate/* → complete/*` event semantics.
- On `passed`: set `row.status = "succeeded"`, `row.error = NULL`,
  `row.artifact_status = "present"`, refresh `row.finished_at = NOW()`, and
  set parent `design_task.status = "built"`.
- On any non-passed status (`flag_mismatch`, `nonzero_exit`, `timeout`,
  `missing_validation`, `contract_failed`, etc.): set `row.status = "failed"`,
  `row.error = <validator status>`, refresh `row.finished_at = NOW()`, and set
  parent `design_task.status = "build_failed"`.
- Run the validator subprocess outside any open DB transaction while holding
  only the session-level advisory lock.
- Convert an unexpected validator exception to `validator_error`, write a
  `validate/failed` plus `complete/failed` event, and release the lock.
- Write `complete/passed` only after the shard move and database state commit.
  If the database commit fails after the shard move, restore the shard and its
  claim file to `failed/` before returning an error.

The endpoint SHALL retain its `200 OK` attempt representation on success,
return `404` when no row matches the id, and return `409` for ineligible,
concurrent, missing-shard, or validation-failure cases.

The existing `POST /api/actions/validate` endpoint and its underlying
`cli.py validate` subprocess SHALL remain available and unchanged.

#### Scenario: Revalidate flips a failed row to succeeded

- **GIVEN** build_attempt B is `failed` with a present `resulting_challenge_dir`
  and the on-disk `validate.sh` now exits `0` and prints the expected flag
- **WHEN** `POST /api/build-attempts/{B}/revalidate` is invoked
- **THEN** the response is `200` with `status="succeeded"`
- **AND** the row's `status` is `succeeded`, `error` is null, and
  `finished_at` is refreshed
- **AND** the parent design task's `status` is `built`
- **AND** exactly one `validate/running` and one `validate/passed` progress
  event are appended for the row's `shard_basename`

#### Scenario: Revalidate of a still-active attempt is rejected

- **GIVEN** build_attempt B is `running`
- **WHEN** `POST /api/build-attempts/{B}/revalidate` is invoked
- **THEN** the response is `409` and no progress event is written

#### Scenario: Revalidate of a missing failed shard is rejected

- **GIVEN** build_attempt B is `failed` but its attributed failed shard is absent
- **WHEN** `POST /api/build-attempts/{B}/revalidate` is invoked
- **THEN** the response is `409` with a message naming the missing failed shard

#### Scenario: Revalidate failure writes validate and complete terminals

- **GIVEN** the same revalidation produces a `flag_mismatch` result
- **WHEN** the endpoint completes
- **THEN** it appends `validate/running`, `validate/failed`, and
  `complete/failed` using the existing revalidate event semantics

#### Scenario: Stale sibling and concurrent duplicate are rejected

- **GIVEN** B is not the latest attempt for its design task, or another request
  is already revalidating B
- **WHEN** `POST /api/build-attempts/{B}/revalidate` is invoked
- **THEN** the response is `409` and that request starts no validator process

#### Scenario: Final database write failure restores queue placement

- **GIVEN** validation passes and the failed shard is moved to done
- **WHEN** the attempt status transaction fails to commit
- **THEN** the shard and claim file are restored under failed
- **AND** no `complete/passed` event is written

### Requirement: list_attempts progress subquery is bounded by the folded batch

The `list_attempts` repository query SHALL restrict its `progress_snapshots`
aggregation to the `shard_basename` set of the folded latest-per-task rows
selected by the outer query, rather than aggregating across every snapshot in
the table.

The query SHALL use the existing BTree primary-key index on
`progress_snapshots(shard, challenge_id)` for the restricted shard scan; it
SHALL NOT require a redundant single-column index.

This requirement is a performance contract: the `list_attempts` query's row
count read from `progress_snapshots` MUST be proportional to the number of
returned build_attempts, not to the global snapshot population.

#### Scenario: Snapshot scan size scales with returned rows

- **GIVEN** the `progress_snapshots` table holds 10000 rows across 500 shards
- **AND** the query is filtered such that only 5 build_attempts are returned
- **WHEN** the dashboard requests `GET /api/build-attempts?limit=5`
- **THEN** the executed query's `progress_snapshots` aggregation only scans
  rows belonging to those 5 shards (verifiable via `EXPLAIN ANALYZE` showing
  an index scan on `progress_snapshots(shard)`)

#### Scenario: Existing primary key index supports the scan

- **WHEN** the bounded query is explained on PostgreSQL
- **THEN** the plan may use the primary-key index whose leading column is `shard`
