## MODIFIED Requirements

### Requirement: BuildOrchestrationService submits and retries builds

The generated shard payload remains the execution input for Hermes, but
build-attempt dispatch SHALL NOT rely on filename ordering to choose which
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

### Requirement: HTTP API exposes build orchestration

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
pending shard. The category endpoint SHALL return `409` when no queued DB-known
attempt in that category has a matching pending shard after recovery. The
single-attempt endpoint SHALL return `409` when the named attempt is not
`queued` or when no matching pending shard exists after recovery. Both
endpoints SHALL return `409` when another local dashboard task is already
running.

#### Scenario: Category worker endpoint starts one DB-known attempt

- **GIVEN** queued Web build attempts `A` and `B` both have matching pending
  attributed shards
- **AND** `A` sorts before `B` by `(created_at, id)`
- **WHEN** `POST /api/build-attempts/worker/start` is called with
  `{"category": "web"}`
- **THEN** the backend starts the worker constrained to `build_attempt_id = A`
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

### Requirement: 构建任务 view follows the Design Tasks layout

The build-attempts dashboard view SHALL NOT use the legacy global
`POST /api/actions/worker` endpoint for category-filtered or attempt-specific
build work. A list action SHALL require an explicit category filter and SHALL
call the constrained category endpoint, which resolves to one DB-known queued
attempt and launches by build-attempt id. A detail action SHALL call the
constrained single-build-attempt endpoint for the inspected attempt.

This replaces the previous list-action behavior that invoked
`POST /api/actions/worker` unchanged. Refresh behavior and the rest of the
Build Attempts list/detail layout remain as specified by the base requirement.

The legacy global worker action MAY remain available in a shard-management
surface only when the UI text makes clear that it consumes the whole pending
file queue.

Constrained build-worker starts SHALL preserve the existing dashboard local
task guard: a server process may not start a constrained worker while another
local worker or validation subprocess is still running.

#### Scenario: List worker action requires an explicit category

- **GIVEN** the operator is on the Build Attempts list with category filter
  unset
- **WHEN** the operator clicks Start Worker
- **THEN** no worker process is started
- **AND** the UI asks the operator to choose a category

#### Scenario: List worker action starts constrained category execution

- **GIVEN** the operator is on the Build Attempts list with category filter
  `web`
- **WHEN** the operator clicks Start Worker
- **THEN** the frontend calls the constrained build-worker endpoint with
  `category = web`
- **AND** it does not call `POST /api/actions/worker`

#### Scenario: Detail worker action starts constrained attempt execution

- **GIVEN** the operator is viewing build attempt `A`
- **WHEN** the operator clicks Start Worker
- **THEN** the frontend calls the constrained build-worker endpoint with
  `build_attempt_id = A`
- **AND** no unrelated shard may be claimed

#### Scenario: Constrained worker respects local process guard

- **GIVEN** the dashboard task manager already has a running worker or
  validation subprocess
- **WHEN** the operator starts a category- or attempt-constrained build worker
- **THEN** the endpoint returns a conflict
- **AND** no second worker process is started
