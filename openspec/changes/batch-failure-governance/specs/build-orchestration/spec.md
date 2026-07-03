## ADDED Requirements

### Requirement: Build-attempt repair is failure-class aware and attempt-scoped

The system SHALL route validation and repair for validation-phase build-attempt failures according to the normalized failure class of the latest validation round. A class route SHALL select one of the bounded repair actions supported by current services: deterministic mechanical repair, Hermes repair with structured diagnostics, or no-op/escalation when automatic repair is unsafe. Automatic validation repair budgets SHALL be scoped to a single attempt, not shared across a batch. The system SHALL keep sibling attempts in the same batch independent so that one attempt's timeout, service-readiness failure, contract failure, solver failure, or validation repair exhaustion cannot block another attempt's validation, retry, or reporting. This requirement SHALL NOT change existing runner-phase failure taxonomy or sequential consecutive-infrastructure fail-fast behavior.

#### Scenario: Each validation class selects a repair policy route
- **WHEN** a validation-phase failed attempt has normalized class `timeout`, `service-readiness`, `contract`, or `solver`
- **THEN** the repair policy router SHALL select the matching class-specific route for that attempt
- **AND** the route SHALL declare whether it uses deterministic mechanical repair, Hermes repair, or no-op/escalation
- **AND** the selected route SHALL be recorded in the existing diagnostic or progress summary for operator visibility

#### Scenario: Timeout follows its own repair path
- **WHEN** a build attempt fails with a validation timeout
- **THEN** the attempt SHALL use the timeout-specific recovery path
- **AND** the failure summary SHALL remain associated with that attempt only

#### Scenario: Readiness failures prioritize service startup evidence
- **WHEN** a pwn attempt fails because the solver cannot observe a live prompt or menu during validation
- **THEN** the next repair step SHALL prioritize service readiness evidence before exploit payload tuning
- **AND** deterministic repair SHALL be limited to known safe readiness mechanics such as validate.sh probe normalization and scaffold normalization

#### Scenario: Prompt-input failures are not auto-repaired as validation failures
- **WHEN** a build attempt fails before validation because prompt inputs cannot be rendered or supplied
- **THEN** the attempt SHALL preserve the prompt/rendering diagnostic in the existing runner failure surface
- **AND** deterministic validation auto-repair SHALL NOT claim a class-specific prompt route for that failure in the first rollout

#### Scenario: Contract failures use safe mechanical repairs first
- **WHEN** a build attempt fails with normalized class `contract`
- **THEN** the next repair step SHALL apply deterministic repair only for known safe mechanical fixes such as nested output cleanup, document pair sync, source evidence promotion, artifact metadata/hash correction, and validation wrapper normalization
- **AND** unresolved contract failures SHALL carry structured diagnostics into Hermes repair or operator escalation before tuning runtime behavior

#### Scenario: Solver failures use Hermes repair with structured evidence
- **WHEN** a build attempt fails with normalized class `solver`
- **THEN** deterministic mechanical repair SHALL NOT claim to tune arbitrary solver or exploit behavior
- **AND** the next automatic repair step SHALL pass the latest validation evidence, file context, and stdout/stderr tails into the Hermes repair prompt unless the policy chooses no-op/escalation
- **AND** it SHALL not prioritize service startup repair unless new service-readiness evidence appears

#### Scenario: One attempt cannot consume another attempt's budget
- **WHEN** two attempts in the same batch fail during validation
- **THEN** each attempt SHALL have its own retry budget, derived validation class, invocation-local signature state, and failure summary
- **AND** exhausting one attempt's repair loop SHALL not reduce the other attempt's opportunities

### Requirement: Build-attempt diagnostics expose the normalized failure class

The system SHALL expose the normalized validation failure class in build-attempt diagnostics and API-facing summaries whenever a build attempt fails in the validation phase. API payloads SHALL use the field name `validation_failure_class` for this value. The exposed class SHALL be derived from the latest validation result and existing diagnostic evidence, preferring structured `validation_failure_details` from `work/executions/<attempt_id>/current/state/validation-history.json` when present, and MAY be copied into existing progress-event or attempt-summary payloads. The class SHALL be visible alongside the existing concise `failure_summary` so operators can distinguish timeout, service-readiness, contract, and solver validation failures without reading raw logs first. Non-validation runner failures SHALL continue to expose their existing runner failure category and SHALL NOT claim a normalized validation class. Repair diagnostics SHALL preserve `validation_contract_errors` / `contract_errors` compatibility while adding structured `validation_failure_details` where available. Direct repair, retry/repair submission, and attempt-detail API responses SHALL use the same latest failed validation result when deriving route and class data.

#### Scenario: Failed validation attempt summary includes the class
- **WHEN** the dashboard loads a validation-phase failed build attempt
- **THEN** the response SHALL include `validation_failure_class` with the normalized class
- **AND** the human-readable failure summary SHALL continue to be present

#### Scenario: Retry and manual repair share structured diagnostics
- **WHEN** an operator requests retry, repair, or attempt detail for the same failed validation attempt
- **THEN** each path SHALL derive failure class and repair context from the same latest failed validation result when available
- **AND** `validation_failure_details`, stdout/stderr tails, and `failure_summary` SHALL be available to Hermes repair prompts without dropping legacy contract-error fields

#### Scenario: Non-validation attempts do not claim a validation failure class
- **WHEN** a build attempt is queued, running, succeeded, or failed before validation with a runner-phase failure
- **THEN** it SHALL NOT include `validation_failure_class`
