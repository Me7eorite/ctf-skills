## ADDED Requirements

### Requirement: Batch failure taxonomy is normalized and stable

The system SHALL map build-attempt validation failures into a normalized, closed set of failure classes that is stable across runner invocations. Every failed attempt SHALL have exactly one class assigned for the latest validation round. The class SHALL be derivable from the final validation result without requiring new storage tables.

#### Scenario: Timeout is classified deterministically
- **WHEN** a build attempt fails because `validate.sh` exceeds its allotted time
- **THEN** the attempt SHALL be classified as `timeout`
- **AND** the failure summary SHALL preserve the timeout cause

#### Scenario: Service readiness is distinguished from exploit logic
- **WHEN** a pwn attempt fails because the reference solver cannot observe a real banner or menu on a fresh connection
- **THEN** the attempt SHALL be classified as a service-readiness failure
- **AND** the summary SHALL point the operator toward probe or startup issues rather than exploit payload tuning

#### Scenario: Contract failures remain separate from runtime failures
- **WHEN** validation fails because a required file, field, or evidence contract is missing
- **THEN** the attempt SHALL be classified as a contract failure
- **AND** the result SHALL not be classified as a timeout or solver-runtime failure

### Requirement: Automatic repair stops after repeated identical failures

The system SHALL stop automatic repair for a build attempt when the same normalized failure class and essentially the same failure signature repeat across repair rounds without observable progress. The stop condition SHALL be attempt-local. Reaching that stop condition SHALL leave the attempt failed and SHALL not affect the repair budget or progress of sibling attempts in the same batch.

#### Scenario: Repeated timeout stops repair for one attempt
- **WHEN** the same build attempt times out repeatedly with the same signature and no progress change
- **THEN** the system SHALL stop further automatic repair for that attempt
- **AND** the attempt SHALL remain failed with the latest timeout diagnostic

#### Scenario: A different attempt still gets its own budget
- **GIVEN** attempt A has already exhausted its automatic repair budget
- **WHEN** attempt B in the same batch fails later
- **THEN** attempt B SHALL receive its own fresh repair budget
- **AND** attempt A's exhaustion SHALL not reduce attempt B's retry opportunities

### Requirement: Batch processing isolates attempts

The system SHALL treat each build attempt in a batch as an independent failure domain for validation and repair. One attempt's timeout, prompt failure, readiness failure, or repair exhaustion SHALL NOT block other attempts in the same batch from being validated, repaired, or reported.

#### Scenario: One failed attempt does not stall its siblings
- **GIVEN** a batch contains attempts A, B, and C
- **AND** A fails with a timeout
- **WHEN** the batch continues processing
- **THEN** B and C SHALL continue through their own validation paths
- **AND** A's failure SHALL not abort the batch

#### Scenario: Attempt-local failure history remains separate
- **WHEN** two attempts in the same batch fail for different reasons
- **THEN** each attempt SHALL retain its own failure class history and summary
- **AND** neither attempt SHALL overwrite the other's diagnostic state
