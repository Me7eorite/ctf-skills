## MODIFIED Requirements

### Requirement: Design task status supports planning before execution

The system SHALL model design task status with the values
`draft|queued|designing|designed|failed|archived|building|built|build_failed`.
The planning subsystem SHALL implement operator transitions
`draft -> queued`, `draft -> archived`, and `queued -> archived`. Worker
transitions involving `designing`, `designed`, and `failed` are governed by
the design-agent execution change. The new statuses `building`, `built`,
and `build_failed` are governed by the build-orchestration capability,
and the planning subsystem SHALL NOT permit any direct operator transition
into or out of them via the design-task planning endpoints.

The existing `failed` status keeps its design-phase meaning: a design
attempt failed before producing a usable `challenge_designs` row. It MUST
NOT be reused to indicate a build-phase failure; the build-orchestration
capability uses `build_failed` for that purpose so operator diagnostics
can distinguish design failures from build failures.

#### Scenario: Draft task can be queued

- **GIVEN** a design task with `status = "draft"`
- **WHEN** the operator queues it
- **THEN** the task status becomes `queued`

#### Scenario: Draft or queued task can be archived

- **GIVEN** a design task with `status = "draft"` or `status = "queued"`
- **WHEN** the operator archives it
- **THEN** the task status becomes `archived`

#### Scenario: Designed task cannot be archived by the planning endpoint

- **GIVEN** a design task fixture-injected with `status = "designed"`
- **WHEN** the operator calls the planning archive endpoint
- **THEN** the operation is rejected
- **AND** the status remains `designed`

#### Scenario: Build-phase statuses are not reachable from planning endpoints

- **GIVEN** a design task with `status = "designed"`
- **WHEN** the operator invokes any of the design-task planning
  transitions (`queue`, `archive`)
- **THEN** the status does NOT become `building`, `built`, or
  `build_failed`
- **AND** moving into those statuses requires invoking the build-
  orchestration endpoints (`POST /api/design-tasks/build` etc.)

#### Scenario: Database CHECK admits the build-phase values

- **WHEN** Alembic revision `0006_build_attempts` has been applied
- **THEN** the `design_tasks.status` `CHECK` constraint admits
  `building`, `built`, and `build_failed` in addition to the original
  six values

### Requirement: Dashboard exposes design tasks as a first-class view

The dashboard SHALL expose `Design Tasks` as a top-level navigation entry,
independent of the research request detail page. The view SHALL provide:
(a) a list mode that lists tasks across all requests with filters for
`generation_request_id`, `status`, and `category`; and (b) a detail mode that
shows a single task with its clickable parent `generation_request_id`, attempts,
latest_design, and the per-task action buttons (`Queue`, `Archive`, `Design`).
When the build-orchestration capability is enabled, the list view SHALL
additionally render a checkbox column on rows whose `status` is `designed`
or `build_failed`, a top-of-view bulk `构建已选` button that calls
`POST /api/design-tasks/build` with the selected ids, and a per-row
`构建` button on the same rows that calls
`POST /api/design-tasks/{id}/build`. After a successful submission the
view SHALL surface a toast with a link to the new "构建任务" view filtered
by the affected `generation_request_id`. The dashboard SHALL NOT render the
complete design tasks table inside the research request detail page; only the
summary card defined in *Request detail exposes design tasks* SHALL appear
there.

#### Scenario: Sidebar shows Design Tasks entry

- **WHEN** the dashboard loads
- **THEN** the left sidebar shows a top-level `Design Tasks` entry
- **AND** clicking it opens the list view

#### Scenario: List view filters by request via URL query

- **GIVEN** the user is on the research request detail page and clicks
  `View design tasks →`
- **WHEN** the design tasks list view opens
- **THEN** the list is pre-filtered to that `generation_request_id`
- **AND** the active filter is visible in the toolbar

#### Scenario: List view makes parent request navigable

- **GIVEN** the design tasks list contains rows from multiple generation requests
- **WHEN** the dashboard renders the list
- **THEN** each row shows a shortened parent `generation_request_id`
- **AND** clicking it opens the corresponding research request detail page

#### Scenario: Detail view shows attempts and latest design

- **GIVEN** the user opens a specific design task
- **WHEN** the detail view renders
- **THEN** it shows the task's attempts ordered by `attempt`
- **AND** it shows `latest_design` (or "no design yet")
- **AND** it shows `Queue` / `Archive` / `Design` action buttons subject to
  the current status's allowed transitions

#### Scenario: Build controls appear only on eligible rows

- **GIVEN** the list contains tasks in statuses
  `{draft, queued, designing, designed, failed, archived, building,
  built, build_failed}`
- **WHEN** the dashboard renders the list
- **THEN** the checkbox column and per-row `构建` button are enabled
  only on rows whose status is `designed` or `build_failed`
- **AND** `building`, `built`, and `built_failed` rows show a
  read-only badge linking to the corresponding build-attempts row
  in the "构建任务" view

#### Scenario: Bulk build button invokes the orchestration endpoint

- **GIVEN** the operator has selected two `designed` rows A and B
- **WHEN** the operator clicks `构建已选`
- **THEN** the frontend issues a single
  `POST /api/design-tasks/build` request with both ids
- **AND** on success the toast offers a link to
  `#/build-attempts?generation_request_id={current request id}`
