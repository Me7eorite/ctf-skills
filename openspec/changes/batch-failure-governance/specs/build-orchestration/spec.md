## ADDED Requirements

### Requirement: Build-attempt repair is failure-class aware and attempt-scoped

The system SHALL route validation and repair for build attempts according to the normalized failure class of the latest validation round. Automatic repair budgets SHALL be scoped to a single attempt, not shared across a batch. The system SHALL keep sibling attempts in the same batch independent so that one attempt's timeout, readiness failure, or repair exhaustion cannot block another attempt's validation, retry, or reporting.

#### Scenario: Timeout follows its own repair path
- **WHEN** a build attempt fails with a timeout
- **THEN** the attempt SHALL use the timeout-specific recovery path
- **AND** the failure summary SHALL remain associated with that attempt only

#### Scenario: Readiness failures prioritize service startup evidence
- **WHEN** a pwn attempt fails because the solver cannot observe a live prompt or menu
- **THEN** the next repair step SHALL prioritize service readiness evidence before exploit payload tuning

#### Scenario: One attempt cannot consume another attempt's budget
- **WHEN** two attempts in the same batch fail
- **THEN** each attempt SHALL have its own retry budget and failure history
- **AND** exhausting one attempt's repair loop SHALL not reduce the other attempt's opportunities

### Requirement: Build-attempt diagnostics expose the normalized failure class

The system SHALL preserve the normalized failure class in build-attempt diagnostics and API-facing summaries whenever a build attempt fails. The class SHALL be visible alongside the existing concise failure summary so operators can distinguish timeout, readiness, contract, and solver failures without reading raw logs first.

#### Scenario: Failed attempt summary includes the class
- **WHEN** the dashboard loads a failed build attempt
- **THEN** the response SHALL include a normalized failure class
- **AND** the human-readable failure summary SHALL continue to be present

#### Scenario: Non-failed attempts do not claim a failure class
- **WHEN** a build attempt is queued, running, or succeeded
- **THEN** it SHALL NOT be presented as having a terminal failure class
