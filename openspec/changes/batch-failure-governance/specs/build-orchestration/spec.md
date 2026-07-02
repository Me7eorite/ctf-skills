## ADDED Requirements

### Requirement: Build-attempt repair is failure-class aware and attempt-scoped

The system SHALL route validation and repair for build attempts according to the normalized failure class of the latest validation round. Automatic repair budgets SHALL be scoped to a single attempt, not shared across a batch. The system SHALL keep sibling attempts in the same batch independent so that one attempt's timeout, service-readiness failure, prompt failure, contract failure, solver failure, or repair exhaustion cannot block another attempt's validation, retry, or reporting.

#### Scenario: Each class selects a deterministic repair route
- **WHEN** a failed attempt has normalized class `timeout`, `service-readiness`, `prompt`, `contract`, or `solver`
- **THEN** deterministic auto-repair SHALL select the matching class-specific route for that attempt
- **AND** the selected route SHALL be recorded in the existing diagnostic or progress summary for operator visibility

#### Scenario: Timeout follows its own repair path
- **WHEN** a build attempt fails with a timeout
- **THEN** the attempt SHALL use the timeout-specific recovery path
- **AND** the failure summary SHALL remain associated with that attempt only

#### Scenario: Readiness failures prioritize service startup evidence
- **WHEN** a pwn attempt fails because the solver cannot observe a live prompt or menu
- **THEN** the next repair step SHALL prioritize service readiness evidence before exploit payload tuning

#### Scenario: Prompt failures repair prompt inputs before rerunning validation
- **WHEN** a build attempt fails with normalized class `prompt`
- **THEN** the next repair step SHALL repair the missing or invalid prompt inputs before rerunning validation

#### Scenario: Contract failures repair required files and metadata
- **WHEN** a build attempt fails with normalized class `contract`
- **THEN** the next repair step SHALL repair the missing file, field, or evidence contract before tuning runtime behavior

#### Scenario: Solver failures tune runtime exploit behavior
- **WHEN** a build attempt fails with normalized class `solver`
- **THEN** the next repair step SHALL tune solver/runtime behavior using the latest validation evidence
- **AND** it SHALL not prioritize service startup repair unless new service-readiness evidence appears

#### Scenario: One attempt cannot consume another attempt's budget
- **WHEN** two attempts in the same batch fail
- **THEN** each attempt SHALL have its own retry budget and failure history
- **AND** exhausting one attempt's repair loop SHALL not reduce the other attempt's opportunities

### Requirement: Build-attempt diagnostics expose the normalized failure class

The system SHALL expose the normalized failure class in build-attempt diagnostics and API-facing summaries whenever a build attempt fails. The exposed class SHALL be derived from the latest validation result and existing diagnostic evidence, and MAY be copied into existing progress-event or attempt-summary payloads. The class SHALL be visible alongside the existing concise failure summary so operators can distinguish timeout, service-readiness, prompt, contract, and solver failures without reading raw logs first.

#### Scenario: Failed attempt summary includes the class
- **WHEN** the dashboard loads a failed build attempt
- **THEN** the response SHALL include a normalized failure class
- **AND** the human-readable failure summary SHALL continue to be present

#### Scenario: Non-failed attempts do not claim a failure class
- **WHEN** a build attempt is queued, running, or succeeded
- **THEN** it SHALL NOT be presented as having a terminal failure class
