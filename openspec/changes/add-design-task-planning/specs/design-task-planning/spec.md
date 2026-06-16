## ADDED Requirements

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
latest research run is completed. The generation operation SHALL use the parent
request, the completed run, and its findings/sources to create exactly
`generation_requests.target_count` draft design tasks.

The generated tasks SHALL match the parent request's category and difficulty
distribution. Each generated task SHALL reference at least one finding from the
same completed research run.

#### Scenario: Researched request creates target_count tasks

- **GIVEN** a generation request with `target_count = 3`
- **AND** its latest research run is completed with findings and sources
- **WHEN** the operator generates design tasks
- **THEN** exactly three `design_tasks` rows are created
- **AND** each row has `status = "draft"`
- **AND** each row references the same `generation_request_id`

#### Scenario: Request without completed research cannot generate tasks

- **GIVEN** a generation request whose latest run is queued, running, failed, or
  absent
- **WHEN** the operator generates design tasks
- **THEN** the operation is rejected
- **AND** no `design_tasks` rows are written

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
`draft|queued|designing|designed|failed|archived`. This change SHALL implement
operator transitions `draft -> queued`, `draft -> archived`, and
`queued -> archived`. Worker transitions involving `designing`, `designed`, and
`failed` are reserved for a future design-agent execution change.

#### Scenario: Draft task can be queued

- **GIVEN** a design task with `status = "draft"`
- **WHEN** the operator queues it
- **THEN** the task status becomes `queued`

#### Scenario: Draft or queued task can be archived

- **GIVEN** a design task with `status = "draft"` or `status = "queued"`
- **WHEN** the operator archives it
- **THEN** the task status becomes `archived`

#### Scenario: Designed task cannot be archived by the planning endpoint

- **GIVEN** a design task fixture-injected with `status = "designed"` (this
  change does not create any code path that sets `designed`; the row exists
  only to verify the guard for the future design-worker change)
- **WHEN** the operator calls the planning archive endpoint
- **THEN** the operation is rejected
- **AND** the status remains `designed`

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

The request detail API SHALL include design tasks for the requested
`generation_request_id`, ordered by `task_no`. The dashboard SHALL render those
tasks in the request detail page with enough information for an operator to
understand and release planned challenge work.

#### Scenario: Request detail returns design tasks

- **GIVEN** a request with two design tasks
- **WHEN** `GET /api/research/requests/{id}` is called
- **THEN** the JSON response includes `design_tasks`
- **AND** the tasks are ordered by `task_no`

#### Scenario: Dashboard shows generated tasks

- **GIVEN** the request detail response includes design tasks
- **WHEN** the dashboard renders the request detail page
- **THEN** it shows a `Design Tasks` section
- **AND** each task row displays `challenge_id`, `title`, `difficulty`,
  `primary_technique`, and `status`
