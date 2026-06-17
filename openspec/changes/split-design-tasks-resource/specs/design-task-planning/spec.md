## MODIFIED Requirements

### Requirement: Request detail exposes design tasks

The research request detail API SHALL include a summary of design tasks for the
requested `generation_request_id`, but SHALL NOT inline the complete design
task rows. The summary SHALL contain at least the total task count and counts
grouped by status (`draft|queued|designing|designed|failed|archived`). The
dashboard SHALL render the summary on the request detail page together with a
navigation link to the dedicated design tasks view filtered by that
`generation_request_id`. Complete task rows, attempts, and latest_design data
SHALL be available only via the dedicated design tasks resource (see
*Design tasks are queryable via a dedicated resource* below).

#### Scenario: Request detail returns design task summary

- **GIVEN** a request with two design tasks (one `draft`, one `queued`)
- **WHEN** `GET /api/research/requests/{id}` is called
- **THEN** the JSON response includes `design_tasks_summary`
- **AND** the summary contains `total = 2`
- **AND** the summary contains counts `{ "draft": 1, "queued": 1 }`
- **AND** the response does NOT include a `design_tasks` field

#### Scenario: Dashboard shows summary and navigation link

- **GIVEN** the request detail response includes a non-empty
  `design_tasks_summary`
- **WHEN** the dashboard renders the request detail page
- **THEN** it shows a `Design Tasks` summary card with the total and per-status
  counts
- **AND** it shows a navigation link that opens the dedicated design tasks
  view filtered by the current `generation_request_id`
- **AND** it does NOT render a table of design task rows

## ADDED Requirements

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

### Requirement: Dashboard exposes design tasks as a first-class view

The dashboard SHALL expose `Design Tasks` as a top-level navigation entry,
independent of the research request detail page. The view SHALL provide:
(a) a list mode that lists tasks across all requests with filters for
`generation_request_id`, `status`, and `category`; and (b) a detail mode that
shows a single task with its attempts, latest_design, and the per-task action
buttons (`Queue`, `Archive`, `Design`). The dashboard SHALL NOT render the
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

#### Scenario: Detail view shows attempts and latest design

- **GIVEN** the user opens a specific design task
- **WHEN** the detail view renders
- **THEN** it shows the task's attempts ordered by `attempt`
- **AND** it shows `latest_design` (or "no design yet")
- **AND** it shows `Queue` / `Archive` / `Design` action buttons subject to
  the current status's allowed transitions

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
