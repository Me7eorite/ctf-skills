## ADDED Requirements

### Requirement: Build-attempt repair is failure-class aware and attempt-scoped

The system SHALL route validation and repair for validation-phase build-attempt failures according to the normalized failure class and signature derived by the `batch-failure-governance` capability for the latest validation round. This capability consumes that derivation; it SHALL NOT redefine the class set or readiness semantics. A class route SHALL select one of the bounded repair actions supported by current services: deterministic mechanical repair, Hermes repair with structured diagnostics, or no-op/escalation when automatic repair is unsafe. Runner automatic validation repair budgets SHALL be scoped to a single attempt and a single runner invocation, not shared across a batch. Dashboard manual repair, retry, and revalidate requests SHALL use the latest validation class and signature for context, but Phase 1 SHALL NOT suppress those operator-triggered requests across invocations. The system SHALL keep sibling attempts in the same batch independent so that one attempt's timeout, service-readiness failure, contract failure, solver failure, or validation repair exhaustion cannot block another attempt's validation, retry, or reporting. This requirement SHALL NOT change existing runner-phase failure taxonomy or sequential consecutive-infrastructure fail-fast behavior.

#### Scenario: Each validation class selects a repair policy route
- **WHEN** a validation-phase failed attempt has normalized class `timeout`, `service-readiness`, `contract`, or `solver`
- **THEN** the repair policy router SHALL select the matching class-specific route for that attempt
- **AND** the route SHALL declare whether it uses deterministic mechanical repair, Hermes repair, or no-op/escalation
- **AND** the selected route SHALL be recorded in the existing diagnostic or progress summary for operator visibility

#### Scenario: Timeout follows its own repair path
- **WHEN** a build attempt fails with a validator or `validate.sh` wrapper timeout during validation
- **THEN** the attempt SHALL preserve timeout evidence and use a timeout-specific bounded route
- **AND** when the timeout signature contains a stable subreason such as solver I/O, service readiness, wrapper bounds, or missing diagnostic capture, the route MAY use that subreason to choose bounded diagnostic, solver-context, or escalation behavior while keeping `validation_failure_class=timeout`
- **AND** the route SHALL apply only safe wrapper or diagnostic normalization when the missing bound is obvious, otherwise no-op/escalate rather than blindly increasing timeouts or looping Hermes repair
- **AND** the failure summary SHALL remain associated with that attempt only

#### Scenario: Readiness failures prioritize service startup evidence
- **WHEN** a pwn attempt fails because a fresh readiness probe cannot observe a live application prompt or menu before solver payloads are sent
- **THEN** the next repair step SHALL prioritize service readiness evidence before exploit payload tuning
- **AND** deterministic repair SHALL be limited to known safe readiness mechanics such as validate.sh probe normalization and diagnostic capture

#### Scenario: Prompt-input failures are not auto-repaired as validation failures
- **WHEN** a build attempt fails before validation because prompt inputs cannot be rendered or supplied
- **THEN** the attempt SHALL preserve the prompt/rendering diagnostic in the existing runner failure surface
- **AND** deterministic validation auto-repair SHALL NOT claim a class-specific prompt route for that failure in the first rollout
- **AND** retry, repair, list, and detail derivation SHALL preserve the terminal runner phase or otherwise prove the source failure was validation before emitting `validation_failure_class` from fallback report/progress evidence

#### Scenario: Contract failures use safe mechanical repairs first
- **WHEN** a build attempt fails with normalized class `contract`
- **THEN** the next repair step SHALL apply deterministic repair only for known safe mechanical fixes such as nested output cleanup, document pair sync, source evidence promotion, artifact metadata/hash correction, and validation wrapper normalization
- **AND** unresolved contract failures SHALL carry structured diagnostics into Hermes repair or operator escalation before tuning runtime behavior

#### Scenario: Solver failures use Hermes repair with structured evidence
- **WHEN** a build attempt fails with normalized class `solver`
- **THEN** deterministic mechanical repair SHALL NOT claim to tune arbitrary solver or exploit behavior
- **AND** the next automatic repair step SHALL pass the latest validation evidence, file context, and stdout/stderr tails into the Hermes repair prompt unless the policy chooses no-op/escalation
- **AND** it SHALL not prioritize service startup repair unless new service-readiness evidence appears

#### Scenario: Solver repair carries exp-specific context
- **WHEN** the repair policy routes a solver-class validation failure to Hermes repair
- **THEN** the repair context SHALL include the current `writenup/exp.py`, `validate.sh`, relevant solver debug reports, latest `validation_failure_details`, stdout/stderr tails, and failure summary
- **AND** the context SHALL identify dependency, synchronization, flag mismatch, offset/payload, leak parsing, or remote/local mismatch evidence when the classifier can derive it
- **AND** the repair prompt SHALL instruct the repair agent to retest through the validation service path rather than only local/offline solver paths

#### Scenario: Pwn solver repair is evidence-backed
- **WHEN** a solver-class Pwn failure involves overflow offsets, libc/PIE bases, ROP gadgets, leak parsing, or menu synchronization
- **THEN** the repair route SHALL request recalculation or verification against the actual shipped ELF, libc, attachments, or container/chroot path
- **AND** it SHALL prefer updating or creating structured debug evidence over replacing constants with new guesses

#### Scenario: Missing diagnostics are repaired before payload guesses
- **WHEN** a solver-class validation failure lacks bounded solver stdout/stderr tails, readiness evidence, service logs, or structured failure details
- **THEN** the selected route SHALL first normalize `validate.sh` or the validation wrapper to capture those diagnostics when a safe wrapper or context-only diagnostic repair is available
- **AND** otherwise the selected route SHALL no-op/escalate rather than rewrite challenge-specific solver or scaffold logic without proof
- **AND** Hermes repair SHALL NOT be asked to tune arbitrary payload logic from an empty or generic failure summary
- **AND** diagnostic normalization inside the solver route SHALL NOT reclassify missing readiness evidence as `service-readiness`; only a later validation result with explicit failed-fresh-connection evidence may change that class

#### Scenario: Exp stability contract failures get bounded repair
- **WHEN** a Web/Pwn solver violates stable validation-target requirements such as hardcoded service host/port in the default path or unbounded Pwn receive/process interactions
- **THEN** the failure SHALL be routed as a bounded validation repair with contract or solver evidence according to the diagnostic
- **AND** when the repair is safe and evidence-backed, the route MAY normalize the solver toward `CHAL_HOST`/`CHAL_PORT`, bounded reads, and explicit local debug branches without consuming sibling attempts' budgets
- **AND** Phase 1 SHALL only act on such violations when they are visible in validation evidence or repair context; broad static solver-quality scanning and hard document-completion gates remain out of scope

#### Scenario: One attempt cannot consume another attempt's budget
- **WHEN** two attempts in the same batch fail during validation
- **THEN** each attempt SHALL have its own retry budget, derived validation class, invocation-local signature state, and failure summary
- **AND** exhausting one attempt's repair loop SHALL not reduce the other attempt's opportunities

#### Scenario: Repeated signatures are checked after deterministic reruns
- **WHEN** deterministic validation repair changes or normalizes wrapper/diagnostic files and then reruns validation
- **THEN** orchestration SHALL compare the new normalized class/signature against prior failures in the same runner invocation before any further deterministic or Hermes repair
- **AND** a repeated class/signature without progress SHALL stop automatic repair for that attempt

### Requirement: Build-attempt diagnostics expose the normalized failure class

The system SHALL expose the normalized validation failure class in build-attempt diagnostics and API-facing summaries whenever a build attempt fails in the validation phase. API payloads SHALL use the field name `validation_failure_class` for this value. The exposed class SHALL be derived from the latest validation result and existing diagnostic evidence, preferring structured `validation_failure_details` from `work/executions/<attempt_id>/current/state/validation-history.json` when present, and MAY be copied into existing progress-event or attempt-summary payloads. The class SHALL be visible alongside the existing concise `failure_summary` so operators can distinguish timeout, service-readiness, contract, and solver validation failures without reading raw logs first. Non-validation runner failures SHALL continue to expose their existing runner failure category and SHALL NOT claim a normalized validation class. Repair diagnostics SHALL preserve `validation_contract_errors` / `contract_errors` compatibility while adding structured `validation_failure_details` where available. Direct repair, retry/repair submission, and attempt-detail API responses SHALL use the same shared latest-failed-validation derivation helper when deriving route, class, signature, and repair context data. Attempt-list payloads SHALL expose the same class/signature/summary semantics for the returned folded rows, but SHALL keep derivation bounded to the returned attempt set by using copied progress/summary fields or bounded per-row history reads rather than scanning unrelated execution histories.

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

#### Scenario: Attempt list derives failure summaries without global history scans
- **WHEN** the dashboard requests a bounded build-attempt list
- **THEN** the list response SHALL expose `validation_failure_class`, normalized signature, and concise summary for returned validation-phase failed attempts when available
- **AND** the derivation work SHALL be bounded to the returned folded attempt rows
- **AND** the list path SHALL NOT scan execution histories for attempts outside the returned row set

### Requirement: Risky enforcement is staged and observable

The system SHALL roll out validation failure governance in stages so operators can see classifications and diagnostics before stricter artifact-normalization or solver-quality blockers affect batch throughput. Phase 1 SHALL enable classification, signature derivation, diagnostic preservation, API visibility, repair context, class-aware deterministic repair routing, and runner invocation-local repeated-signature stops before pre-validation normalization, scaffold overwrite, or hard exp-stability blockers. Pwn evidence-profile enforcement is out of scope for Phase 1 and SHOULD be introduced by a follow-up change after profile-specific tests are in place. Existing runner-phase taxonomy and historical artifacts SHALL continue to be readable without requiring a schema migration or retroactive evidence generation.

#### Scenario: Diagnostics are visible before hard blockers
- **WHEN** the governance change is first enabled
- **THEN** validation failure classes, signatures, and diagnostic envelopes SHALL be visible in attempt detail and repair prompts
- **AND** hard exp-stability blockers SHALL NOT be enabled until those diagnostics are available for repair decisions and covered by dedicated enforcement tests

#### Scenario: Existing artifacts remain inspectable
- **WHEN** an older failed attempt lacks new solver-quality evidence or diagnostic-envelope fields
- **THEN** the API SHALL still expose available legacy diagnostics
- **AND** missing new fields SHALL be represented as unavailable rather than breaking attempt detail rendering
