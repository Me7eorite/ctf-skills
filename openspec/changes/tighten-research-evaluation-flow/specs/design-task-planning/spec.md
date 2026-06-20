## MODIFIED Requirements

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

#### Scenario: Researched request creates target_count tasks

- **GIVEN** a generation request with `target_count = 3` and at least 2 findings on its latest completed run
- **AND** its latest research run is completed with findings and sources
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
- **AND** a historical completed run carrying only 1 finding (predating the
  research-planning quality gate)
- **WHEN** the operator generates design tasks
- **THEN** the response is `409` with reason code `insufficient_findings`
- **AND** no `design_tasks` row is created
