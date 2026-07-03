## ADDED Requirements

### Requirement: Batch failure taxonomy is normalized and stable

The system SHALL map build-attempt validation-phase failures into a normalized, closed set of failure classes that is stable across runner invocations. The first-rollout closed set SHALL be exactly `timeout`, `service-readiness`, `contract`, and `solver`. These exact slugs are the canonical API and repair-policy values for attempts whose terminal runner phase is `validation`. Every failed validation-phase attempt SHALL have exactly one class assigned for the latest validation round. Attempts that fail before or outside validation, including runner phases such as `hermes_auth`, `hermes_rate_limit`, `hermes_timeout`, `terminal_workspace`, `materialize`, and `contract_prepare`, SHALL NOT be assigned a normalized validation failure class by this capability. The class SHALL be derivable from the final validation result and existing diagnostic evidence without requiring new storage tables or a new durable source-of-truth field. Derivation SHALL prefer structured `validation_failure_details` from the latest failed validation result in `work/executions/<attempt_id>/current/state/validation-history.json` when present, then fall back to report entries that preserve `validation_failure_details`, `validation_status`, `validation_contract_errors`, latest terminal validation progress-event messages, and artifact metadata. The system MAY copy the derived class into existing progress-event or attempt-summary payloads for visibility, but such copies SHALL NOT replace derivation as the source of truth.

#### Scenario: Timeout is classified deterministically
- **WHEN** a build attempt fails because `validate.sh` exceeds its allotted time during validation
- **THEN** the attempt SHALL be classified as `timeout`
- **AND** the failure summary SHALL preserve the timeout cause

#### Scenario: Service readiness is distinguished from exploit logic
- **WHEN** a pwn attempt fails during validation because the reference solver cannot observe a real banner or menu on a fresh connection
- **THEN** the attempt SHALL be classified as `service-readiness`
- **AND** the summary SHALL point the operator toward probe or startup issues rather than exploit payload tuning

#### Scenario: Latest validation history supplies structured details
- **WHEN** a failed validation attempt has `validation_failure_details` recorded in `current/state/validation-history.json`
- **THEN** the classifier SHALL use the latest failed validation result from that history as the primary structured source
- **AND** it SHALL NOT rely only on artifact `metadata.json` or progress messages when structured history is available

#### Scenario: Readiness detail codes outrank contract status
- **WHEN** a validation result has `validation_status` `contract_failed` but `validation_failure_details` includes readiness-specific codes such as `pwn_port_only_readiness` or `pwn_bad_readiness_probe`
- **THEN** the attempt SHALL be classified as `service-readiness`
- **AND** the classifier SHALL NOT route it as `contract` solely because the coarse validation status is `contract_failed`

#### Scenario: Non-validation runner phases remain outside the validation taxonomy
- **WHEN** a build attempt fails before validation with runner phase `hermes_auth`, `hermes_rate_limit`, `hermes_timeout`, `terminal_workspace`, `materialize`, or `contract_prepare`
- **THEN** the attempt SHALL preserve that runner phase as the failure category
- **AND** the attempt SHALL NOT expose `timeout`, `service-readiness`, `contract`, or `solver` as a normalized validation failure class

#### Scenario: Prompt failures are deferred until prompt diagnostics exist
- **WHEN** a build, validation, or repair prompt cannot be rendered or supplied because required prompt inputs are missing or invalid
- **THEN** the attempt SHALL preserve the existing runner failure category and diagnostic summary
- **AND** the attempt SHALL NOT be assigned a normalized validation failure class unless a future change adds stable prompt capture points and diagnostic fields

#### Scenario: Contract failures remain separate from runtime failures
- **WHEN** validation fails because a required file, field, or evidence contract is missing
- **THEN** the attempt SHALL be classified as `contract`
- **AND** the result SHALL not be classified as a timeout, service-readiness, or solver failure

#### Scenario: Solver runtime failures remain separate from contracts
- **WHEN** `validate.sh` runs and the reference solver exits non-zero, emits a wrong flag, or otherwise fails after required files and service readiness have been established
- **THEN** the attempt SHALL be classified as `solver`
- **AND** the summary SHALL preserve the solver-runtime evidence instead of routing the failure as a contract or service-readiness problem

### Requirement: Automatic repair stops after repeated identical failures

The system SHALL stop automatic validation repair for a build attempt when the same normalized validation failure class and essentially the same failure signature repeat across repair rounds inside the same active runner validation/repair invocation without observable progress. The signature SHOULD be derived from structured `validation_failure_details` code/message/path data when available, then fall back to validation status, concise error text, and stdout/stderr tail evidence. The stop condition SHALL be attempt-local and invocation-local. Reaching that stop condition SHALL leave the attempt failed and SHALL not affect the repair budget or progress of sibling attempts in the same batch. Cross-request suppression across separate retry or revalidate requests is out of scope unless a future change adds durable failure-signature storage.

#### Scenario: Repeated timeout stops repair for one attempt
- **WHEN** the same build attempt times out repeatedly with the same structured-or-derived signature and no progress change inside one validation/repair invocation
- **THEN** the system SHALL stop further automatic repair for that attempt
- **AND** the attempt SHALL remain failed with the latest timeout diagnostic

#### Scenario: A different attempt still gets its own budget
- **GIVEN** attempt A has already exhausted its automatic repair budget
- **WHEN** attempt B in the same batch fails later
- **THEN** attempt B SHALL receive its own fresh repair budget
- **AND** attempt A's exhaustion SHALL not reduce attempt B's retry opportunities

### Requirement: Batch processing isolates attempts

The system SHALL treat each build attempt in a batch as an independent failure domain for validation and repair. One attempt's timeout, service-readiness failure, solver failure, contract failure, or validation repair exhaustion SHALL NOT block other attempts in the same batch from being validated, repaired, or reported. This capability SHALL NOT disable the existing sequential consecutive-infrastructure fail-fast behavior for non-validation infrastructure failures.

#### Scenario: One failed attempt does not stall its siblings
- **GIVEN** a batch contains attempts A, B, and C
- **AND** A fails with a timeout during validation
- **WHEN** the batch continues processing
- **THEN** B and C SHALL continue through their own validation paths
- **AND** A's validation failure SHALL not abort the batch

#### Scenario: Consecutive infrastructure fail-fast is preserved
- **GIVEN** the sequential driver observes enough consecutive infrastructure failures to trigger its configured fail-fast threshold
- **WHEN** the threshold is reached
- **THEN** the sequential driver MAY still abort tail attempts with the existing `consecutive_infra` reason
- **AND** this behavior SHALL NOT be treated as a violation of validation/repair batch isolation

#### Scenario: Attempt-local failure history remains separate
- **WHEN** two attempts in the same batch fail for different reasons
- **THEN** each attempt SHALL retain its own derived failure class, invocation-local signature state, and summary
- **AND** neither attempt SHALL overwrite the other's diagnostic state
