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
  `status = 'queued'`, and the same `shard_basename`

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

- `queued -> running`: the reconciler observed the shard moved from
  `work/shards/pending/` to `work/shards/running/`, OR the
  `progress_events` table contains a `(stage='queued', status='running')`
  event for the row's `shard_basename`.
- `queued -> lost` or `running -> lost`: the shard file is not present
  under any of `pending/`, `running/`, `done/`, `failed/`.
- `running -> succeeded`: the shard moved to `done/`,
  `work/challenges/<challenge_id>-<slug>/metadata.json` exists, and
  its `solve_status == 'passed'`.
- `running -> failed`: the shard moved to `failed/`, OR moved to
  `done/` but the artifact directory is missing or `solve_status`
  is not `passed`.
- `done/` shard whose artifact directory is missing SHALL result in
  `status = 'lost'` (not `failed`), because the artifact was produced
  and then removed externally.

Terminal statuses (`succeeded`, `failed`, `lost`) SHALL be terminal:
no transitions out except by creating a new attempt with
`attempt_no + 1`.

`started_at` SHALL be set when transitioning to `running`;
`finished_at` SHALL be set when transitioning to a terminal status.

#### Scenario: Shard claim transitions the row to running

- **WHEN** the reconciler observes
  `work/shards/running/web-0001.hermes-02.json`
  for a row whose `shard_basename = 'web-0001.json'` and
  `status = 'queued'`
- **THEN** the row's `status` becomes `running`,
  `worker` becomes `'hermes-02'`, and `started_at` is set

#### Scenario: Done shard with passed artifact promotes to succeeded

- **WHEN** the reconciler observes
  `work/shards/done/web-0001.json` and
  `work/challenges/web-0001-flag-leak/metadata.json` exists with
  `solve_status = 'passed'`
- **THEN** the row's `status` becomes `succeeded`,
  `resulting_challenge_dir` becomes
  `'work/challenges/web-0001-flag-leak'`, and `finished_at` is set

#### Scenario: Done shard with missing artifact dir marks lost

- **WHEN** the reconciler observes `work/shards/done/web-0001.json`
  but no directory under `work/challenges/` matches the challenge id
- **THEN** the row's `status` becomes `lost`, `error` summarizes
  "artifact directory missing", and `finished_at` is set

#### Scenario: Shard vanishing from disk marks running attempts lost

- **GIVEN** a row with `status = 'running'` and
  `shard_basename = 'web-0001.json'`
- **WHEN** the shard file is absent from `pending/`, `running/`,
  `done/`, and `failed/` for a full reconciler tick
- **THEN** the row's `status` becomes `lost` immediately on the next
  tick with no grace period

### Requirement: BuildOrchestrationService submits and retries builds

The system SHALL provide `services.BuildOrchestrationService` with at
least these public methods, each opening a short PostgreSQL transaction:

- `submit_batch(design_task_ids: list[UUID]) -> list[UUID]`
- `submit_single(design_task_id: UUID) -> UUID`
- `retry(build_attempt_id: UUID) -> UUID`
- `render_shard_payload(design_task, latest_design) -> dict`

`submit_batch` SHALL only accept design tasks whose current `status`
is `designed` or `build_failed`. For each accepted task it SHALL,
within a single transaction:

1. Insert a `build_attempts` row with `status = 'queued'` and
   `attempt_no = COALESCE(max(attempt_no), 0) + 1`.
2. Render a matrix-shaped shard JSON document (see "Shard JSON
   schema is matrix-shaped plus design context") and atomically write
   it under `work/shards/pending/<shard_basename>`.
3. Set the parent design task's `status` to `building`.

A design task in any other status SHALL be rejected with a
validation error and SHALL NOT advance.

`retry` SHALL require that the named `build_attempts` row is in a
terminal status. It SHALL create a new attempt for the same design
task following the same flow, then return the new attempt id. It
SHALL NOT touch `work/challenges/<id>-<slug>/`; the runner's resume
protocol carries forward already-passed stages.

#### Scenario: Submit batch rejects ineligible tasks

- **GIVEN** task A is `designed` and task B is `building`
- **WHEN** `submit_batch([A, B])` is invoked
- **THEN** the call raises a validation error
- **AND** no new `build_attempts` rows are inserted for either task
- **AND** no shard file is written under `work/shards/pending/`

#### Scenario: Submit batch is transactional across all tasks

- **GIVEN** tasks A and B both `designed`, and the file system blocks
  the shard write for B
- **WHEN** `submit_batch([A, B])` is invoked
- **THEN** neither task transitions to `building`
- **AND** no `build_attempts` rows are inserted for either task

#### Scenario: Retry preserves existing artifacts

- **GIVEN** build attempt #1 for design task T finished `failed` and
  `work/challenges/<challenge_id>-<slug>/` exists with partial output
- **WHEN** `retry(attempt_1.id)` is invoked
- **THEN** a new `attempt_no = 2` row is inserted
- **AND** the existing artifact directory is not deleted or modified
- **AND** the rewritten shard file lands in `work/shards/pending/`

### Requirement: BuildReconciler mirrors filesystem state to PostgreSQL

The system SHALL provide `services.BuildReconciler` running as a
daemon thread launched by `web.server.serve(...)`. It SHALL poll
`work/shards/{running,done,failed}/` on a fixed interval read from
`BUILD_RECONCILER_POLL_SECONDS` (default 5; non-positive or
non-integer values SHALL fall back to the default and emit a
warning).

Each tick SHALL:

1. Match shards under `work/shards/running/` (filename
   `<basename>.<worker>.json`) against the highest-`attempt_no`
   `build_attempts` row with that `shard_basename` and a non-terminal
   status; promote `queued` to `running`, set `worker` and
   `started_at`.
2. For shards under `work/shards/done/`, inspect the corresponding
   `work/challenges/<challenge_id>-<slug>/metadata.json`; choose
   `succeeded` / `failed` / `lost` per "build_attempts five-state
   machine".
3. For shards under `work/shards/failed/`, transition the matching
   row to `failed`, summarizing the cause from the shard report.
4. For non-terminal rows whose `shard_basename` is absent from all
   four queue directories, set `status = 'lost'`.
5. Roll the parent `design_tasks.status` forward based on the
   highest-`attempt_no` row: `succeeded` -> `built`,
   `failed`/`lost` -> `build_failed`.

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
matrix shards and SHALL add the following two optional top-level
fields:

- `build_attempt_id`: the UUID of the `build_attempts` row that
  emitted this shard.
- `design_task_id`: the UUID of the parent design task.

Each entry in `challenges[]` SHALL include every field already
written by the existing matrix conventions (`id`, `category`,
`difficulty`, `primary_technique`, `learning_objective`, `points`,
`port`, `title`, plus category-specific keys such as `runtime`,
`framework`, `compiler`, `mitigations`, `target_platform`) AND a
`design` sub-object whose value SHALL be the validated
`challenge_designs.payload` content (deployment, artifacts,
flag_location, validation, hints, prompt, etc.).

Existing hand-written matrix shards that omit `build_attempt_id`,
`design_task_id`, and `design` SHALL continue to be valid input to
the runner. The reconciler SHALL ignore shards that lack
`build_attempt_id`.

#### Scenario: Generated shard carries traceability ids

- **WHEN** the orchestration service renders a shard for design
  task T (with the latest validated design D and build attempt A)
- **THEN** the file contains `build_attempt_id = A`,
  `design_task_id = T`, and exactly one challenge entry
- **AND** that entry includes both the matrix fields and a `design`
  sub-object derived from D

#### Scenario: Hand-written matrix shard is still accepted

- **WHEN** an operator hand-writes a matrix shard with no top-level
  `build_attempt_id` and submits it via `challenge-factory split`
- **THEN** the runner processes it normally
- **AND** the reconciler never tries to attribute its outcome to a
  `build_attempts` row

### Requirement: HTTP API exposes build orchestration

The dashboard backend SHALL register the following endpoints in
`web/build_attempts_endpoints.py`, registered BEFORE the static
catch-all in `web/server.py`:

- `POST /api/design-tasks/build` with body
  `{"design_task_ids": [UUID, ...]}`; returns `201` with body
  `{"build_attempt_ids": [UUID, ...]}` ordered by input.
- `POST /api/design-tasks/{id}/build` with empty body; returns
  `201` with body `{"build_attempt_id": UUID}`.
- `GET /api/build-attempts?status=&worker=&design_task_id=&limit=`;
  returns `200` with a JSON array of "folded" rows (one per design
  task, exposing only its highest-`attempt_no` row) joined with the
  parent design task title/category and the latest derived percent
  from `progress_snapshots`.
- `GET /api/build-attempts/{id}`; returns `200` with the row plus
  `sibling_attempts` (all attempts for the same design task ordered
  by `attempt_no` ascending), `progress_events` (for the row's
  shard, with `carry-forward:` events preserved), and
  `resulting_challenge_dir` when present.
- `POST /api/build-attempts/{id}/retry` with empty body; returns
  `201` with body `{"build_attempt_id": UUID}` (the new attempt).

The list endpoint SHALL apply `BUILD_ATTEMPTS_LIST_DEFAULT_LIMIT`
(default 100) when no `limit` is given, SHALL cap at
`BUILD_ATTEMPTS_LIST_MAX_LIMIT` (default 500), and SHALL reject
malformed `limit` values with `400`. Both knobs are read at module
import time from the environment, falling back to defaults on
missing or invalid values with a warning.

Unknown filter values (e.g.
`?status=invalid`) SHALL be rejected with `400`.

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

### Requirement: 构建任务 view follows the Design Tasks layout

The dashboard SHALL expose a top-level navigation entry "构建任务"
(slug `build-attempts`) under its own sidebar group. The list view
SHALL render a filter bar above a table. Filter bar fields SHALL
include `状态` (build-attempts statuses), `Worker`, `分类` (web/pwn/re),
and `Design Task` (UUID input). The filter bar's right side SHALL
present five action buttons: `Apply`, `Clear`, `⟳ 刷新`,
`▶ 启动 Worker`, `☑ 重新验证`. The table SHALL have one row per
design task that has at least one `build_attempts` row, showing the
title, category, difficulty, latest attempt status, derived percent
(from `progress_snapshots`, "-" if absent), worker, attempt count,
created-at, and an action area with `详情` (always) and `重试` (only
when the latest attempt is in `failed` or `lost`).

The detail view SHALL show, for the inspected attempt: basic info,
a link to its parent design task, the related shard path, the
`resulting_challenge_dir` (when set), a table of all sibling
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
