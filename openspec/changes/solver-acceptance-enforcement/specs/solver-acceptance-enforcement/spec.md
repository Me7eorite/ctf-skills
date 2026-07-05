## ADDED Requirements

### Requirement: Web and Pwn solver acceptance is mandatory

The system SHALL treat Web and Pwn reference solver acceptance as a hard build-success condition. A Web or Pwn challenge SHALL NOT be published, marked as `done`, or reconciled to `build_attempts.succeeded` unless the current `validate.sh` runs the current `writenup/exp.py` against the host-provided validation service path and returns a passed validation result. The authoritative default solver path SHALL use `CHAL_HOST` and `CHAL_PORT` to reach the running service. Local-only solver branches MAY exist for debugging, but they SHALL NOT satisfy solver acceptance.

#### Scenario: Passing runtime validation accepts solver
- **WHEN** a Web or Pwn build produces `validate.sh` and `writenup/exp.py`
- **AND** host validation starts the service and runs `validate.sh` with `CHAL_HOST` and `CHAL_PORT`
- **AND** validation returns passed
- **THEN** solver acceptance SHALL be recorded as passed for that validation round
- **AND** the build MAY proceed to publication if all other publication fences pass

#### Scenario: Local-only smoke does not satisfy acceptance
- **WHEN** `writenup/exp.py` only proves a local `process()`, loopback, or offline solve path
- **AND** the default validation path does not solve through `CHAL_HOST` and `CHAL_PORT`
- **THEN** solver acceptance SHALL fail
- **AND** the failure SHALL include a structured solver-quality diagnostic instead of marking the challenge built

#### Scenario: Failed exp blocks publish
- **WHEN** host validation runs `validate.sh` and the reference solver exits non-zero, prints the wrong flag, times out, or cannot connect to the validation target
- **THEN** the build SHALL remain failed or in repair
- **AND** the artifact SHALL NOT be published as a successful challenge

### Requirement: Solver static quality preflight blocks deterministic defects

The system SHALL reuse and extend the existing validation contract checks, including `ChallengeValidator.contract_errors()` and `_solver_integrity_errors()`, to inspect Web and Pwn `writenup/exp.py` and validation wrappers before successful build completion for deterministic solver-quality defects. Existing hardcoded flag, organizer-file leakage, destructive cleanup, compose isolation, and Pwn solver-evidence freshness checks SHALL remain authoritative. The added preflight coverage SHALL emit structured diagnostics for remaining gaps: default-path hardcoded service targets, missing `CHAL_HOST` / `CHAL_PORT` use, missing local helper modules, unbounded receive/read calls, unbounded brute-force or leak loops, and default-path local-only execution. Explicit local debug branches SHALL be allowed only when the default validation path remains service-bound and bounded.

#### Scenario: Hardcoded validation target is blocked
- **WHEN** the default solver path connects to `127.0.0.1`, `localhost`, a container name, or a fixed challenge port instead of `CHAL_HOST` and `CHAL_PORT`
- **THEN** solver static preflight SHALL fail with a structured default-target diagnostic
- **AND** validation repair SHALL be routed toward solver correction before publication

#### Scenario: Explicit local debug branch is allowed
- **WHEN** a solver contains a `LOCAL=1` or equivalent branch for local `process()` debugging
- **AND** the default branch uses `CHAL_HOST` and `CHAL_PORT` with bounded I/O
- **THEN** solver static preflight SHALL NOT fail solely because the local debug branch exists

#### Scenario: Missing helper module is blocked
- **WHEN** `writenup/exp.py` imports a non-standard local helper module
- **AND** that helper is neither present under `writenup/` nor otherwise declared as supported runtime dependency
- **THEN** solver static preflight SHALL fail with a missing-helper diagnostic containing the module name

#### Scenario: Organizer-only flag read is blocked
- **WHEN** the solver reads organizer-only files such as `metadata.json`, `challenge.yml`, or hidden build inputs to obtain the flag
- **THEN** solver static preflight SHALL fail with an organizer-file leakage diagnostic
- **AND** runtime validation SHALL NOT be bypassed by that solver output

#### Scenario: Unbounded Pwn read is blocked
- **WHEN** a Pwn solver uses pwntools, sockets, or subprocess reads without short timeouts or explicit bounded loops in the default validation path
- **THEN** solver static preflight SHALL fail with an unbounded-I/O diagnostic
- **AND** the diagnostic SHALL identify the relevant call or loop when available

### Requirement: Solver repair must prove progress

The system SHALL require each automatic solver repair round to prove progress before consuming additional repair budget. Progress SHALL extend the existing validation failure fingerprint path rather than replacing it, and SHALL be measured from a bounded fingerprint that includes the current solver file hash, validation wrapper hash, debug report hash when present, validation failure class, validation failure signature, solver-quality diagnostic codes, output manifest hash, and concise runtime evidence. A repair round that changes no relevant file, adds no useful diagnostic evidence, and repeats the same validation failure fingerprint and solver acceptance fingerprint SHALL stop that automatic path and record a blocked reason.

#### Scenario: Solver edit permits another validation round
- **WHEN** a solver-class validation failure is repaired
- **AND** the repair changes `writenup/exp.py` or supporting solver evidence
- **THEN** the system SHALL rerun validation and compare the new acceptance fingerprint
- **AND** additional repair MAY continue if the new fingerprint shows material progress and budget remains

#### Scenario: No solver progress stops automatic repair
- **WHEN** a solver repair round returns successfully but `writenup/exp.py`, `validate.sh`, debug evidence, and validation signature are unchanged
- **THEN** the runner SHALL stop automatic solver repair for that attempt
- **AND** the attempt SHALL expose a blocked reason rather than consuming another equivalent repair round

#### Scenario: Different solver failure may continue
- **WHEN** a repair changes a missing-helper failure into a flag-mismatch failure or a prompt-synchronization failure with new evidence
- **THEN** the system SHALL treat that as material progress
- **AND** the attempt MAY continue within its bounded repair policy

### Requirement: Failed-attempt blocked outcomes are explicit

The system SHALL provide explicit bounded outcomes when automatic solver repair cannot make progress. If runtime evidence indicates the service is reachable and the solver is the failing component, the system SHALL record a solver blocked route such as `solver_unrepairable` or `solver_quality_blocked`; any future solver-only regeneration or rewrite SHALL remain inside the existing current-attempt repair abstraction. Automated challenge regeneration is out of scope for this change. If evidence indicates the generated challenge artifact is internally inconsistent and solver-only repair cannot succeed, the system SHALL fail the attempt with an explicit human-action blocked reason such as `challenge_regeneration_required`. If neither route is safe or budget remains exhausted, the system SHALL fail the attempt with an explicit blocked reason.

#### Scenario: Solver route is selected for reachable-service failures
- **WHEN** service readiness is established
- **AND** validation evidence shows the reference solver is missing dependencies, using a bad target, losing prompt synchronization, or printing the wrong flag
- **THEN** the next escalation route SHALL remain solver-focused
- **AND** it SHALL preserve existing challenge source and deployment files unless evidence proves they are inconsistent

#### Scenario: Challenge regeneration requires evidence
- **WHEN** solver repair fails
- **AND** diagnostics show an artifact contradiction such as missing shipped binary, impossible service path, or metadata inconsistent with runtime behavior
- **THEN** the system SHALL record `challenge_regeneration_required` as a human-action blocked reason
- **AND** the regeneration decision SHALL be recorded in validation history, report, or progress evidence

#### Scenario: Unsafe regeneration blocks attempt
- **WHEN** solver repair cannot make progress
- **AND** solver-only rewrite/regeneration is disabled, unsafe, unimplemented, or out of budget
- **THEN** the attempt SHALL remain in the existing `failed` status with an explicit blocked reason such as `solver_unrepairable`, `solver_quality_blocked`, `solver_regeneration_failed`, or `challenge_regeneration_required`

### Requirement: Solver acceptance evidence is preserved

The system SHALL preserve solver acceptance evidence for every failed and successful Web/Pwn validation round. Evidence SHALL include the acceptance status, validation command, exit code, stdout/stderr tails, final flag candidate when present, structured solver-quality diagnostics, solver acceptance fingerprint, repair/regeneration route when selected, and unavailable markers for fields that could not be captured.

#### Scenario: Failed acceptance preserves evidence
- **WHEN** solver acceptance fails
- **THEN** validation history SHALL preserve the solver-quality details, runtime stdout/stderr tails, command, exit code, final flag candidate when present, and solver acceptance fingerprint
- **AND** repair prompts and attempt-detail APIs SHALL be able to display that evidence

#### Scenario: Successful acceptance preserves final proof
- **WHEN** solver acceptance passes after repair or regeneration
- **THEN** the final validation round SHALL preserve the passed acceptance status and acceptance fingerprint
- **AND** the published artifact SHALL correspond to the output tree that produced that final passed round

#### Scenario: Current blocker does not erase root validation failure
- **WHEN** solver acceptance fails during host validation
- **AND** a later repair-infrastructure failure becomes the current blocker
- **THEN** validation history and attempt diagnostics SHALL preserve the original solver acceptance failure as the root failure
- **AND** they SHALL expose the repair-infrastructure failure separately as the current blocker

### Requirement: Solver acceptance fields are additive and manifest-bound

The system SHALL add solver acceptance evidence without removing or renaming existing validation result fields. Existing `contract_errors`, `failure_details`, `validation_failure_details`, validation failure class, validation failure signature, stdout/stderr tails, command, return code, and final flag candidate fields SHALL remain readable. A passed solver acceptance result SHALL include or reference the output manifest hash for the validated tree, and that acceptance SHALL NOT be reused for a different output manifest.

#### Scenario: Existing validation fields remain available
- **WHEN** Web or Pwn solver acceptance diagnostics are added to a failed validation result
- **THEN** existing consumers SHALL still be able to read legacy-compatible `contract_errors` and structured `validation_failure_details`
- **AND** the solver acceptance details SHALL be additive rather than replacing failure-governance evidence

#### Scenario: Output manifest mismatch invalidates acceptance
- **WHEN** a validation round records solver acceptance passed for output manifest hash `A`
- **AND** the output tree later changes to manifest hash `B`
- **THEN** solver acceptance for hash `A` SHALL NOT authorize publication or revalidation promotion for hash `B`

#### Scenario: Older history reports unavailable acceptance
- **WHEN** an older validation-history entry does not contain solver acceptance fields
- **THEN** API and repair consumers SHALL expose solver acceptance as unavailable
- **AND** they SHALL NOT reinterpret the older entry as a solver acceptance failure solely because the fields are absent
