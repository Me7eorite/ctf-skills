## MODIFIED Requirements

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
