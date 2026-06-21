# design-task-planning Specification

## Purpose
TBD - created by archiving change add-design-task-planning. Update Purpose after archive.
## Requirements
### Requirement: Design tasks are database-backed shard-compatible challenge rows

The system SHALL persist planned challenge design work as rows in
`design_tasks`. Each row SHALL represent one future challenge and SHALL belong
to exactly one `generation_requests` row via `generation_request_id`.

Each design task SHALL store the shard-compatible seed fields consumed by the
existing file-backed shard format: `challenge_id`, `title`, `category`,
`difficulty`, `primary_technique`, `learning_objective`, `points`, and `port`.
The database and repository SHALL also store task planning metadata:
`research_run_id`, `task_no`, `scenario`, `constraints`, `evidence_summary`,
`finding_ids`, `status`, `created_at`, and `updated_at`.

The system SHALL NOT store rendered prompt text or a `prompt_input` blob on
`design_tasks`.

#### Scenario: Task row contains old shard seed fields

- **WHEN** a design task is persisted for a Web request
- **THEN** the row contains non-empty `challenge_id`, `title`, `category`,
  `difficulty`, `primary_technique`, and `learning_objective`
- **AND** `points` is a positive integer
- **AND** `port` is a valid TCP port

#### Scenario: Prompt input is not persisted

- **WHEN** a design task row is loaded from the repository
- **THEN** it exposes structured fields only
- **AND** no rendered prompt text or `prompt_input` field is returned

### Requirement: Design tasks are generated from a researched request

The system SHALL generate design tasks only for a generation request whose
latest research run is completed AND whose finding count is at least
`ceil(generation_requests.target_count * 0.5)`. The generation operation SHALL
use the parent request, the completed run, and its findings/sources to create
exactly `generation_requests.target_count` draft design tasks.

The generated tasks SHALL match the parent request's category and difficulty
distribution. Each generated task SHALL reference at least one finding from the
same completed research run.

A generation attempt against a request whose latest run is `failed`, `queued`,
`running`, or `completed` but with `findings.length < ceil(target_count * 0.5)`
SHALL be rejected with a `409` carrying a machine-readable reason code:

- `latest_run_not_completed`: latest run is `queued`, `running`, `failed`, or
  absent.
- `insufficient_findings`: latest run is `completed` but the finding count is
  below the threshold (this is a defensive secondary check; the primary gate
  lives in `research-planning` and prevents `completed` rows from being
  persisted below the threshold).

#### Scenario: Latest completed research run creates target_count tasks

- **GIVEN** a generation request with `target_count = 3` whose latest research run is completed and has at least 2 findings
- **AND** that completed run has sources
- **WHEN** the operator generates design tasks
- **THEN** exactly three `design_tasks` rows are created
- **AND** each row has `status = "draft"`
- **AND** each row references the same `generation_request_id`

#### Scenario: Request without completed research cannot generate tasks

- **GIVEN** a generation request whose latest run is queued, running, failed, or
  absent
- **WHEN** the operator generates design tasks
- **THEN** the response is `409` with reason code `latest_run_not_completed`
- **AND** no `design_tasks` row is created

#### Scenario: Request with insufficient findings is rejected defensively

- **GIVEN** a generation request with `target_count = 4`
- **AND** the latest research run is completed but predates the
  research-planning quality gate and carries only 1 finding
- **WHEN** the operator generates design tasks
- **THEN** the response is `409` with reason code `insufficient_findings`
- **AND** no `design_tasks` row is created

#### Scenario: Difficulty distribution is preserved

- **GIVEN** a generation request with
  `difficulty_distribution = {"easy": 1, "medium": 2}`
- **WHEN** three design tasks are generated
- **THEN** exactly one generated task has `difficulty = "easy"`
- **AND** exactly two generated tasks have `difficulty = "medium"`

#### Scenario: Cross-category task is rejected

- **GIVEN** a parent request with `category = "web"`
- **WHEN** the planner proposes a design task with `category = "pwn"`
- **THEN** the generation operation is rejected
- **AND** no task rows from that planner output are persisted

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

### Requirement: Regeneration is repeatable only before queue release

The system SHALL allow regeneration of design tasks for a request only while
all existing tasks for that request are still `draft` or `archived`. When
regeneration is allowed, existing draft/archived rows MAY be replaced by the
new generated set. If any existing task is `queued`, `designing`, `designed`, or
`failed`, regeneration SHALL be rejected.

#### Scenario: Draft tasks can be regenerated

- **GIVEN** a researched request with existing design tasks all in `draft`
- **WHEN** the operator regenerates design tasks
- **THEN** the previous draft rows are replaced
- **AND** the request again has exactly `target_count` draft tasks

#### Scenario: Archived-only tasks can be regenerated

- **GIVEN** a researched request whose existing design tasks are all
  `archived` (no `draft` or later-status rows)
- **WHEN** the operator regenerates design tasks
- **THEN** the archived rows are replaced by a fresh set of `draft` tasks
- **AND** the request again has exactly `target_count` draft tasks

#### Scenario: Queued task blocks regeneration

- **GIVEN** a request with at least one design task in `queued`
- **WHEN** the operator regenerates design tasks
- **THEN** the operation is rejected
- **AND** no existing design task is modified

### Requirement: Request detail exposes design tasks

The research request detail API SHALL include a summary of design tasks for the
requested `generation_request_id`, but SHALL NOT inline the complete design
task rows. The summary SHALL be a JSON object with exactly two keys:
`total` (non-negative integer) and `by_status` (object). The `by_status` object
SHALL contain a key for every value in
`draft|queued|designing|designed|failed|archived`, even when the count is zero,
so consumers can render fixed columns without null checks. The dashboard SHALL
render the summary on the request detail page together with a navigation link
to the dedicated design tasks view filtered by that `generation_request_id`,
and a `Generate design tasks` action that calls
`POST /api/research/requests/{id}/design-tasks/generate`. Complete task rows,
attempts, and latest_design data SHALL be available only via the dedicated
design tasks resource (see *Design tasks are queryable via a dedicated
resource* below).

#### Scenario: Request detail returns design task summary

- **GIVEN** a request with two design tasks (one `draft`, one `queued`)
- **WHEN** `GET /api/research/requests/{id}` is called
- **THEN** the JSON response includes `design_tasks_summary`
- **AND** the summary equals
  `{ "total": 2, "by_status": { "draft": 1, "queued": 1, "designing": 0, "designed": 0, "failed": 0, "archived": 0 } }`
- **AND** the response does NOT include a `design_tasks` field

#### Scenario: Request detail returns zero-filled summary for empty request

- **GIVEN** a request with no design tasks
- **WHEN** `GET /api/research/requests/{id}` is called
- **THEN** the summary equals
  `{ "total": 0, "by_status": { "draft": 0, "queued": 0, "designing": 0, "designed": 0, "failed": 0, "archived": 0 } }`

#### Scenario: Dashboard shows summary, Generate, and navigation link

- **GIVEN** the request detail response includes `design_tasks_summary`
- **WHEN** the dashboard renders the request detail page
- **THEN** it shows a `Design Tasks` summary card with the `total` and each of
  the six per-status counts
- **AND** it shows a navigation link that opens the dedicated design tasks
  view filtered by the current `generation_request_id`
- **AND** it shows a `Generate design tasks` action on the same summary card
- **AND** it does NOT render a table of design task rows

### Requirement: Design tasks are queryable via a dedicated resource

The system SHALL expose design tasks as a dedicated REST resource under
`/api/design-tasks`. The list endpoint
`GET /api/design-tasks?generation_request_id=&status=&category=&limit=` SHALL return
design tasks ordered by `(generation_request_id, task_no)`, support optional
filtering by `generation_request_id`, `status`, and `category`, and apply a
server-side `limit` (default 100, max 500). The list endpoint SHALL return
task rows only and SHALL NOT inline `attempts` or `latest_design`. The detail
endpoint `GET /api/design-tasks/{id}` SHALL return the full design task fields
together with the task's design attempts (ordered by `attempt` ascending) and
its current `latest_design` record. History data SHALL be loaded with explicit
JOINs or a fixed bounded number of queries; the implementation SHALL NOT
perform per-row N+1 queries against `design_attempts` or `challenge_designs`.

#### Scenario: List endpoint filters by request

- **GIVEN** two generation requests, each with two design tasks
- **WHEN** `GET /api/design-tasks?generation_request_id={A}` is called
- **THEN** only the two tasks for request A are returned
- **AND** they are ordered by `task_no` ascending

#### Scenario: List endpoint filters by status

- **GIVEN** a request with tasks of mixed statuses (one `draft`, one `queued`)
- **WHEN** `GET /api/design-tasks?status=queued` is called
- **THEN** only the `queued` task is returned

#### Scenario: List endpoint filters by category

- **GIVEN** design tasks from Web and Pwn generation requests
- **WHEN** `GET /api/design-tasks?category=web` is called
- **THEN** only Web design tasks are returned

#### Scenario: List endpoint returns lightweight rows

- **GIVEN** a design task with attempts and a latest design
- **WHEN** `GET /api/design-tasks` is called
- **THEN** the task row is returned
- **AND** the row does NOT include `attempts`
- **AND** the row does NOT include `latest_design`

#### Scenario: List endpoint rejects unknown status

- **WHEN** `GET /api/design-tasks?status=nonsense` is called
- **THEN** the response status is 400
- **AND** the body explains the allowed status values

#### Scenario: Detail endpoint returns attempts and latest design without N+1

- **GIVEN** a design task with three design attempts and a current
  `latest_design`
- **WHEN** `GET /api/design-tasks/{id}` is called
- **THEN** the response includes the full task fields
- **AND** the response includes `attempts` ordered by `attempt` ascending
- **AND** the response includes `latest_design` reflecting the most recent
  successful design row, or `null` if none exists
- **AND** the underlying implementation loads attempts and latest_design via
  explicit JOINs or a fixed bounded number of queries, not per-task SELECTs

#### Scenario: Detail endpoint returns 404 for unknown task

- **WHEN** `GET /api/design-tasks/{id}` is called with an id that does not
  exist
- **THEN** the response status is 404

#### Scenario: Detail endpoint returns 404 for malformed id

- **WHEN** `GET /api/design-tasks/{id}` is called with a non-UUID id
  (e.g. `"not-a-uuid"`)
- **THEN** the response status is 404
- **AND** the body does NOT leak the internal parse error

### Requirement: Generate endpoint returns task identifiers, not full rows

`POST /api/research/requests/{id}/design-tasks/generate` SHALL keep its path
and side effects unchanged but SHALL return a slim JSON payload identifying
the newly created tasks rather than inlining the task rows themselves. The
payload SHALL contain exactly: `request_id` (UUID string), `design_task_ids`
(array of UUID strings, ordered by `task_no` ascending), and `total`
(non-negative integer equal to `design_task_ids.length`). Callers that need
the full rows SHALL follow up with
`GET /api/design-tasks?generation_request_id={request_id}`.

#### Scenario: Generate returns slim payload with ids

- **GIVEN** a researched request with `target_count = 3`
- **WHEN** `POST /api/research/requests/{id}/design-tasks/generate` is called
  and succeeds
- **THEN** the response status is 201
- **AND** the response body has exactly the keys `request_id`,
  `design_task_ids`, and `total`
- **AND** `design_task_ids` has length 3, ordered by the new tasks'
  `task_no` ascending
- **AND** `total` equals `3`
- **AND** the response body does NOT include a `design_tasks` array of
  task row objects

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
`POST /api/design-tasks/{id}/build`. After a successful submission, if every
submitted task belongs to the same generation request, the view SHALL surface
a toast linking to the new "构建任务" view filtered by that
`generation_request_id`. If submitted tasks span multiple generation requests,
the toast SHALL link to the unfiltered build-attempts view. The dashboard SHALL NOT render the
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
- **AND** `building` and `built` rows show a read-only badge linking to the
  corresponding build-attempts row in the "构建任务" view
- **AND** `build_failed` rows show the same linked badge while retaining their
  checkbox and `构建` action

#### Scenario: Bulk build button invokes the orchestration endpoint

- **GIVEN** the operator has selected two `designed` rows A and B from the
  same generation request
- **WHEN** the operator clicks `构建已选`
- **THEN** the frontend issues a single
  `POST /api/design-tasks/build` request with both ids
- **AND** on success the toast offers a link to
  `#/build-attempts?generation_request_id={current request id}`

#### Scenario: Cross-request bulk build links to unfiltered build view

- **GIVEN** selected eligible tasks belong to different generation requests
- **WHEN** the bulk build succeeds
- **THEN** the toast links to `#/build-attempts` without a
  `generation_request_id` filter

### Requirement: Polling for design tasks is independent of research detail

The dashboard SHALL poll design tasks data independently of the research
request detail page. Changes to a design task's status SHALL NOT trigger a
re-fetch of the parent `GET /api/research/requests/{id}` payload, and changes
to research runs/sources/findings SHALL NOT trigger a re-fetch of the design
task list or detail. The design tasks list view MAY use a different polling
cadence from research detail.

#### Scenario: Design status transition does not re-fetch research detail

- **GIVEN** the user is on the design tasks detail view for task T
- **WHEN** the user clicks `Queue` and the POST succeeds
- **THEN** the dashboard re-fetches only the design tasks data for T (and
  optionally the summary on the parent research detail if it is currently
  open)
- **AND** the dashboard does NOT re-fetch the full research request detail
  payload

#### Scenario: Research run progress does not re-fetch design tasks

- **GIVEN** the user is on the research request detail page
- **WHEN** the research run status transitions (e.g. running → completed)
- **THEN** the dashboard re-fetches `GET /api/research/requests/{id}`
- **AND** the dashboard does NOT re-fetch the design tasks list or detail

### Requirement: Design tasks expose governed deletion

The dedicated Design Task resource SHALL expose deletion through
`DELETE /api/design-tasks/{id}` and through Delete actions on its dashboard
list and detail surfaces. Cascade, active-work, artifact retention, response,
and confirmation behavior SHALL conform to the `resource-deletion` capability.
Queue, archive, design, build, and read contracts SHALL remain unchanged.

#### Scenario: Design Task detail offers deletion

- **WHEN** the dashboard renders an existing Design Task detail
- **THEN** it exposes a Delete action governed by the shared confirmation dialog
- **AND** active conflicts are displayed without removing the task from the view

#### Scenario: Design Task list offers deletion alongside existing actions

- **WHEN** a Design Task row renders its action group
- **THEN** Delete is available without removing Queue, Archive, Design, Build, or Details actions

