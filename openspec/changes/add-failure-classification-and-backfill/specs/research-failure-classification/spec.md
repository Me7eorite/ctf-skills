## ADDED Requirements

### Requirement: Failure taxonomy maps `last_error` strings to a closed enum

The system SHALL provide a pure function `classify_last_error(text)` that maps any `research_runs.last_error` string to an immutable `FailureClassification(category, title, description, actions)` value, where `category` is drawn from a closed enum: `timeout`, `lease_expired`, `parse_failure`, `quality_gate`, `field_validation`, `binding`, `runtime`, `cancelled`, `unknown`. Empty or `None` input MUST map to `unknown`. The function MUST be deterministic, MUST NOT raise for arbitrary strings, and MUST have no I/O side effects.

#### Scenario: Hermes timeout is classified as `timeout`
- **WHEN** `classify_last_error("Hermes exited with 124")` is called
- **THEN** the result's `category` equals `timeout` and `actions` includes at least one entry referencing `hermes_timeout_seconds` or `target_count`

#### Scenario: Lease expiry is classified as `lease_expired`
- **WHEN** `classify_last_error("lease expired")` is called
- **THEN** the result's `category` equals `lease_expired`

#### Scenario: Output parse failures are classified as `parse_failure`
- **WHEN** `classify_last_error("unparseable_output:no_terminal_json_object")` is called
- **THEN** the result's `category` equals `parse_failure`

#### Scenario: Quality gate failure with concrete counts is classified as `quality_gate`
- **WHEN** `classify_last_error("insufficient_findings:got=3,need=5")` is called
- **THEN** the result's `category` equals `quality_gate` and the `description` references the actual got/need counts parsed from the input

#### Scenario: Field validation failure is classified as `field_validation`
- **WHEN** `classify_last_error("url_shape_invalid:not-a-url")` or `classify_last_error("content_hash_shape_invalid:zzz")` is called
- **THEN** the result's `category` equals `field_validation`

#### Scenario: Binding errors are classified as `binding`
- **WHEN** `classify_last_error("profile_not_bound")` or `classify_last_error("profile_disabled:default")` is called
- **THEN** the result's `category` equals `binding`

#### Scenario: Generic Hermes non-zero exit (not 124) is classified as `runtime`
- **WHEN** `classify_last_error("Hermes exited with 137")` is called
- **THEN** the result's `category` equals `runtime`

#### Scenario: Operator cancellation is classified as `cancelled`
- **WHEN** `classify_last_error("cancelled by operator")` is called
- **THEN** the result's `category` equals `cancelled`

#### Scenario: Unknown strings fall through to `unknown`
- **WHEN** `classify_last_error("a wild new error appears")` is called
- **THEN** the result's `category` equals `unknown` and the `description` echoes the original string

#### Scenario: Empty input is `unknown`
- **WHEN** `classify_last_error(None)` or `classify_last_error("")` is called
- **THEN** the result's `category` equals `unknown`

### Requirement: Each non-unknown category provides at least one actionable suggestion

The system SHALL ensure every category except `unknown` and `cancelled` resolves to a non-empty `actions` list. Each action MUST be a short Chinese-language string oriented to operator action (e.g., "增大 `--hermes-timeout-seconds`", "查日志末尾内容").

#### Scenario: Every actionable category yields at least one action
- **WHEN** `classify_last_error` is called with any seed string mapped to a category in `{timeout, lease_expired, parse_failure, quality_gate, field_validation, binding, runtime}`
- **THEN** the result's `actions` list is non-empty

#### Scenario: Unknown and cancelled may have empty actions
- **WHEN** `classify_last_error` resolves to `unknown` or `cancelled`
- **THEN** the result's `actions` MAY be empty

### Requirement: Run DTOs expose derived classification fields

The system SHALL include five derived fields on every research run DTO returned by HTTP APIs (`latest_run`, `runs[]` in `/api/research/requests/{id}`, list/detail run endpoints, and any other run view): `last_error_category` (enum string or null), `last_error_title` (string or null), `last_error_description` (string or null), `last_error_actions` (array of strings), and `recoverable` (boolean). For runs whose status is not `failed`, all four classification text/list fields MUST be null/null/null/empty-array and `recoverable` MUST be false.

#### Scenario: Failed run with classifiable error exposes category and title
- **WHEN** a client requests `/api/research/requests/{id}` for a request whose `latest_run.status` is `failed` and `last_error` is `"Hermes exited with 124"`
- **THEN** the response's `latest_run.last_error_category` equals `"timeout"`, `latest_run.last_error_title` is a non-empty Chinese string, and `latest_run.last_error` keeps the original raw value

#### Scenario: Completed run has null classification fields
- **WHEN** a client requests `/api/research/requests/{id}` for a request whose `latest_run.status` is `completed`
- **THEN** the response's `latest_run.last_error_category`, `last_error_title`, and
  `last_error_description` are `null`, `last_error_actions` is `[]`, and `recoverable` is `false`

### Requirement: `recoverable` flag is lazily computed from filesystem state

For runs whose `status='failed'`, the system SHALL compute the low-cost backfill-candidate flag `recoverable = true` only when: (1) `hermes_log_path` resolves to a regular file beneath `paths.research_logs` without escaping through a symbolic link; (2) its size is at most 10 MiB and it is readable as UTF-8; and (3) the content contains `"--- stdout ---"` followed by a later `"--- end stdout ---"`. The system MUST NOT perform JSON parsing or quality-gate evaluation for this flag. `recoverable=true` therefore MUST NOT be presented as a guarantee that preview/apply will succeed. For all other statuses or failed checks, `recoverable` MUST be false.

#### Scenario: Failed run with complete log block is recoverable
- **WHEN** a client requests a request whose `latest_run.status` is `failed`, `hermes_log_path` exists on disk, and the file contains both `--- stdout ---` and `--- end stdout ---` markers
- **THEN** the response's `latest_run.recoverable` is `true`

#### Scenario: Failed run with missing log file is not recoverable
- **WHEN** a client requests a failed run whose `hermes_log_path` is null or whose file does not exist on disk
- **THEN** `latest_run.recoverable` is `false`

#### Scenario: Failed run with truncated log (no end marker) is not recoverable
- **WHEN** a client requests a failed run whose log file contains `--- stdout ---` but not a later `--- end stdout ---`
- **THEN** `latest_run.recoverable` is `false`

#### Scenario: Completed run is never recoverable
- **WHEN** a client requests a request whose `latest_run.status` is `completed`
- **THEN** `latest_run.recoverable` is `false`

#### Scenario: Running run is never offered for manual backfill

- **WHEN** a client requests a running run whose log already contains a complete stdout block
- **THEN** its `recoverable` field is `false`

#### Scenario: Escaping or oversized log is not a candidate

- **WHEN** a failed run's stored log path resolves outside `paths.research_logs`, resolves through
  an escaping symlink, or names a file larger than 10 MiB
- **THEN** its `recoverable` field is `false`

### Requirement: UI failure alert renders structured classification

The dashboard SHALL render the "research run failed" alert from the API-provided title, description, and actions as four stacked sections: (1) an icon plus the Chinese category title; (2) a one-paragraph description; (3) a bulleted list of recommended actions (omitted if empty); (4) a collapsed `<details>` block containing the original `last_error` string for engineering debugging. The original raw text MUST always be reachable via the disclosure widget. Frontend category metadata MAY choose icon/tone but MUST NOT duplicate backend description/action rules.

#### Scenario: Operator views a timeout failure
- **WHEN** an operator opens a request whose latest run failed with `last_error_category="timeout"`
- **THEN** the progress card alert displays the timeout icon, the title "研究执行超时" (or the title returned by the API), the description text, a list of at least one recommended action, and a collapsed disclosure containing the raw text `"Hermes exited with 124"`

#### Scenario: Operator views an unknown failure
- **WHEN** an operator opens a request whose latest run failed with `last_error_category="unknown"`
- **THEN** the alert still renders the disclosure with the original raw text, and the recommended actions section is omitted

### Requirement: Run history table shows failure category column

The "运行历史" table in the request detail view SHALL include a column that displays the localized category label for failed runs, and is blank for non-failed runs. The column SHOULD be hidden on viewports narrower than 768px to preserve horizontal space.

#### Scenario: Failed run shows category label in history table
- **WHEN** a request detail view is rendered with at least one failed run in its history
- **THEN** the run history table row for that run includes the corresponding category label in the failure column

#### Scenario: Completed run shows empty failure column
- **WHEN** a run history row's status is `completed`
- **THEN** the failure column for that row is empty
