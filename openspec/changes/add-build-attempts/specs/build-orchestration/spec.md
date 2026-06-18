## ADDED Requirements

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
these public methods. Mutation methods SHALL open a short PostgreSQL
transaction; `render_shard_payload` SHALL be a pure renderer:

- `submit_batch(design_task_ids: list[UUID]) -> list[UUID]`
- `submit_single(design_task_id: UUID) -> UUID`
- `retry(build_attempt_id: UUID) -> UUID`
- `render_shard_payload(design_task, latest_design, *, build_attempt_id,
  resume_from_shard_basename=None) -> dict`

`submit_batch` SHALL only accept design tasks whose current `status` is
`designed` or `build_failed`. It SHALL use this recoverable publication
protocol:

1. Validate every task and render every payload into
   `work/shards/staging/build-attempts/<build_attempt_id>.json`; no file is yet
   visible to workers.
2. In one PostgreSQL transaction, insert each `build_attempts` row with
   `status = 'queued'` and
   `attempt_no = COALESCE(max(attempt_no), 0) + 1`, and set every parent
   design task to `building`; then commit.
3. After commit, make an immediate best-effort atomic rename of every staged
   payload to `work/shards/pending/<shard_basename>`. A post-commit publication
   error SHALL be logged and recovered asynchronously; it SHALL NOT be reported
   as if the committed submission rolled back.

If validation, staging, or the database transaction fails, no database change
or pending shard SHALL survive. If the process exits after commit but before
all renames, a recovery pass SHALL publish matching committed rows on startup
or the next reconciliation tick. Staged files older than one hour without
committed rows SHALL be removed by recovery. Younger unattributed files SHALL
be left alone so recovery cannot race an in-flight submission transaction.

For a `designed` task, the emitted payload SHALL omit
`resume_from_shard_basename`. For a `build_failed` task, the service SHALL
require its highest-`attempt_no` row to be `failed` or `lost` and SHALL use
that row's basename as `resume_from_shard_basename`.

A design task in any other status SHALL be rejected with a
validation error and SHALL NOT advance.

`retry` SHALL require that the named `build_attempts` row is both the highest-
`attempt_no` row for its design task and in `failed` or `lost`, and that the
parent task is `build_failed`. It SHALL create a new attempt for the same design
task following the same flow with a fresh attempt-specific
`shard_basename` and `resume_from_shard_basename` set to the source
attempt's basename, then return the new attempt id. It SHALL NOT touch
`work/challenges/<category>/<id>-<slug>/`; the runner's resume
protocol carries forward already-passed stages by inspecting artifact
evidence for the same challenge id.

#### Scenario: Submit batch rejects ineligible tasks

- **GIVEN** task A is `designed` and task B is `building`
- **WHEN** `submit_batch([A, B])` is invoked
- **THEN** the call raises a validation error
- **AND** no new `build_attempts` rows are inserted for either task
- **AND** no shard file is written under `work/shards/pending/`

#### Scenario: Submit batch failure before commit leaves no work

- **GIVEN** tasks A and B both `designed`, and the file system blocks
  the shard write for B
- **WHEN** `submit_batch([A, B])` is invoked
- **THEN** neither task transitions to `building`
- **AND** no `build_attempts` rows are inserted for either task
- **AND** no shard is visible under `work/shards/pending/`

#### Scenario: Crash after commit converges through staging recovery

- **GIVEN** rows A and B committed but the process exited after publishing
  only shard A
- **WHEN** staging recovery next runs
- **THEN** shard B is published from its attributed staged payload
- **AND** no duplicate shard A is created

#### Scenario: Post-commit publication error remains accepted

- **GIVEN** the database transaction committed and a staged payload remains
  durable, but its immediate rename to `pending/` fails
- **WHEN** `submit_batch` completes
- **THEN** it returns the committed attempt id as accepted
- **AND** a warning is logged
- **AND** recovery retries publication on startup or the next tick

#### Scenario: Retry preserves existing artifacts

- **GIVEN** build attempt #1 for design task T finished `failed` and
  `work/challenges/<category>/<challenge_id>-<slug>/` exists with partial output
- **WHEN** `retry(attempt_1.id)` is invoked
- **THEN** a new `attempt_no = 2` row is inserted
- **AND** the new row has a different `shard_basename` from attempt #1
- **AND** its `resume_from_shard_basename` equals attempt #1's basename
- **AND** the existing artifact directory is not deleted or modified
- **AND** normal immediate publication moves the new shard into
  `work/shards/pending/`
- **AND** if immediate publication fails after commit, the staged shard remains
  recoverable and the accepted attempt stays `queued`

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

Each tick SHALL:

0. Recover staged submissions: publish staged payloads attributed to committed
   queued rows and remove staged payloads older than one hour that have no
   database row. If publication of an attributed staged payload fails, record a
   warning and mark that row as still staged for this tick.
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

`/api/state` SHALL synchronously invoke one reconciler tick before
returning so the operator's immediate next view of the dashboard
reflects newly-completed shards.

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
  from `progress_snapshots`. Rows SHALL include `artifact_status`.
- `GET /api/build-attempts/{id}`; returns `200` with the row plus
  `sibling_attempts` (all attempts for the same design task ordered
  by `attempt_no` ascending), `progress_events` (for the row's
  shard, with `carry-forward:` events preserved), and
  `resulting_challenge_dir` and `artifact_status` when present.
- `POST /api/build-attempts/{id}/retry` with empty body; returns
  `201` with body `{"build_attempt_id": UUID}` (the new attempt), or `409`
  when the attempt is not the latest failed/lost sibling or its parent is not
  `build_failed`.

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

The dashboard SHALL expose a top-level navigation entry "构建任务"
(slug `build-attempts`) under its own sidebar group. The list view
SHALL render a filter bar above a table. Filter bar fields SHALL include
`状态` (build-attempts statuses), `Worker`, `分类` (web/pwn/re), `Design Task`
(UUID input), and `Generation Request` (UUID input). The view SHALL initialize
`Generation Request` from the route's `generation_request_id` query parameter
and display it as an active, editable filter. The filter bar's right side SHALL
present five action buttons: `Apply`, `Clear`, `⟳ 刷新`,
`▶ 启动 Worker`, `☑ 重新验证`. The table SHALL have one row per
design task that has at least one `build_attempts` row, showing the
  title, category, difficulty, latest attempt status, derived percent
  (from `progress_snapshots`, "-" if absent), worker, attempt count,
  artifact availability, created-at, and an action area with `详情` (always) and `重试` (only
when the latest attempt is in `failed` or `lost`).

The detail view SHALL show, for the inspected attempt: basic info,
a link to its parent design task, the related shard path, the
`resulting_challenge_dir` and `artifact_status` (when set), a table of all sibling
attempts ordered by `attempt_no`, and a list of progress events
preserving `carry-forward:` styling.

The application-wide `<header>` toolbar's `重新验证`, `启动 Worker`,
update timestamp, and refresh icon SHALL be removed; the mobile
bottom action bar SHALL be removed. The `重新验证` and `启动 Worker`
actions SHALL be reachable only from the build-attempts view.
`POST /api/actions/worker` and `POST /api/actions/validate` SHALL
remain unchanged at the HTTP level.

#### Scenario: Sidebar exposes build-attempts entry

- **WHEN** the dashboard loads
- **THEN** the sidebar shows a "构建任务" entry that opens the
  list view at route `#/build-attempts`

#### Scenario: List actions invoke the unchanged endpoints

- **WHEN** the operator clicks `▶ 启动 Worker` on the build-tasks
  view
- **THEN** the frontend issues `POST /api/actions/worker`
- **AND** the response is rendered through the existing dashboard
  task-state UI mechanism

#### Scenario: Global header no longer exposes build actions

- **WHEN** the dashboard renders any view other than `build-attempts`
- **THEN** the `<header class="layout-header">` element contains
  no `启动 Worker`, `重新验证`, refresh icon, or sync-time element

#### Scenario: Refresh button triggers a synchronous reconciler tick

- **WHEN** the operator clicks `⟳ 刷新` in the build-tasks view
- **THEN** the frontend calls `/api/state` first (which triggers a
  reconciler tick) and then refetches `/api/build-attempts`
