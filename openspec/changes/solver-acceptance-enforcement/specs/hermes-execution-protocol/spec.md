## ADDED Requirements

### Requirement: Runner records solver acceptance rounds

The Hermes runner and validation path SHALL record solver acceptance evidence in validation history for Web and Pwn validation rounds. Each round SHALL include whether solver acceptance passed, failed, was blocked by static preflight, or was unavailable, plus the solver acceptance fingerprint and structured solver-quality diagnostics when present. Historical attempts without these fields SHALL remain readable through existing validation-history and report fallbacks.

#### Scenario: Static preflight failure is recorded
- **WHEN** solver static preflight rejects `writenup/exp.py`
- **THEN** the runner SHALL append a failed validation-history round with solver acceptance failed or blocked
- **AND** the round SHALL include structured solver-quality diagnostics before any publish decision

#### Scenario: Runtime solver acceptance is recorded
- **WHEN** `validate.sh` runs the reference solver against the validation service
- **THEN** the runner SHALL preserve command, return code, stdout/stderr tails, final flag candidate when present, solver acceptance status, and solver acceptance fingerprint in validation history

#### Scenario: Older validation history remains readable
- **WHEN** an older attempt lacks solver acceptance fields
- **THEN** APIs and repair services SHALL treat those fields as unavailable
- **AND** they SHALL continue to expose existing validation failure evidence

### Requirement: Runner enforces solver repair progress

The Hermes runner SHALL compare solver acceptance fingerprints after each deterministic repair, Hermes repair, or solver-only regeneration round. If the fingerprint proves no material solver progress and validation does not pass, the runner SHALL stop the automatic path and record an explicit blocked reason. The comparison SHALL be invocation-local unless a future change adds durable cross-request suppression.

#### Scenario: No-progress Hermes repair is blocked
- **WHEN** Hermes repair exits successfully
- **AND** the output tree, solver file, debug report, solver-quality details, and validation signature are unchanged
- **THEN** the runner SHALL stop automatic repair for that attempt
- **AND** it SHALL record a solver blocked reason in progress or validation history

#### Scenario: Solver regeneration changes fingerprint
- **WHEN** solver regeneration rewrites `writenup/exp.py`
- **AND** the next validation round produces a different acceptance fingerprint
- **THEN** the runner MAY continue within the bounded solver regeneration budget
- **AND** it SHALL still require final validation passed before publishing

### Requirement: Runner publishes only clean final validation output

The Hermes runner SHALL publish Web/Pwn output only after the exact output tree to be published has passed final host validation with solver acceptance passed. Any mutation to the output tree after a passed validation SHALL invalidate the publish candidate and require validation to run again.

#### Scenario: Post-validation mutation invalidates publish
- **WHEN** Web or Pwn validation passes
- **AND** a later repair, regeneration, or metadata stamping step changes the output tree in a way that affects the publish manifest
- **THEN** the runner SHALL rerun final validation before publishing
- **AND** it SHALL fail publication if final solver acceptance does not pass

#### Scenario: Clean final validation publishes
- **WHEN** the runner captures the publish candidate
- **AND** final validation passes with solver acceptance passed on that exact tree
- **THEN** the runner MAY publish the artifact according to existing publication rules

### Requirement: Runner preserves existing validation history semantics

The Hermes runner SHALL store solver acceptance as additive validation-history evidence. Existing history readers that depend on validation status, failure class, failure signature, contract errors, and stdout/stderr tails SHALL remain compatible. Missing solver acceptance fields in old entries SHALL be represented as unavailable.

#### Scenario: Solver acceptance enriches validation history
- **WHEN** a Web or Pwn validation round completes
- **THEN** the runner SHALL append solver acceptance evidence to the validation-history round
- **AND** the round SHALL still expose existing validation failure governance fields when validation fails

#### Scenario: Repeated solver failures use existing no-progress guard
- **WHEN** solver repair produces the same solver acceptance fingerprint and the same validation failure fingerprint
- **THEN** the runner SHALL use the existing repeated-failure/no-progress repair stop path
- **AND** it SHALL record a solver blocked reason rather than invoking an unrelated repair loop


## MODIFIED Requirements

### Requirement: Resume evidence is deterministic

Validate resume evidence SHALL remain deterministic, but Web and Pwn validate-stage resume skips for new attempts SHALL require historical solver acceptance passed for the same carried-forward output manifest in addition to the existing `validate.sh`, `writenup/exp.py`, `metadata.solve_status == "passed"`, and historical `validate/passed` event evidence. A historical `validate/passed` event or `metadata.solve_status == "passed"` without solver acceptance evidence SHALL NOT skip validation for Web/Pwn attempts created after this change is enabled. Older attempts MAY surface solver acceptance as unavailable for display, but unavailable acceptance SHALL NOT authorize a new Web/Pwn validate skip.

#### Scenario: Web/Pwn validate skip requires acceptance evidence
- **WHEN** the runner evaluates validate resume evidence for a Web or Pwn shard
- **AND** the historical output has `metadata.solve_status == "passed"` and a `validate/passed` event
- **BUT** it lacks solver acceptance passed for the carried-forward output manifest
- **THEN** validate SHALL NOT be skipped
- **AND** the runner SHALL execute fresh host validation before any publication decision

### Requirement: Reports preserve per-challenge validation results

Reports SHALL preserve solver acceptance evidence in addition to the existing per-challenge validation fields. For successful Web/Pwn validation results, the runner SHALL merge enough evidence into the report/history/result surface to prove solver acceptance: solver acceptance status, fingerprint, output manifest hash or validation round binding, command, return code when available, stdout/stderr tails or explicit unavailable markers, final flag candidate when available, and structured solver-quality diagnostics when present. Successful rounds SHALL NOT drop all command/stdout/stderr/final-flag evidence merely because validation passed.

#### Scenario: Successful solver acceptance preserves proof fields
- **WHEN** a Web or Pwn validation round passes and solver acceptance passes
- **THEN** validation history and report merge output SHALL include solver acceptance passed plus its fingerprint and manifest binding
- **AND** command/stdout/stderr/final-flag fields SHALL be present when captured or explicitly marked unavailable

### Requirement: Runner owns validate execution and validate events

Fresh Web/Pwn `validate/passed` events written by the runner SHALL correspond to validation results whose solver acceptance passed for the current output manifest. Carry-forward `validate/passed` events MAY remain historical evidence, but they SHALL NOT be sufficient for new Web/Pwn publication or resume skip unless paired with solver acceptance evidence for the carried-forward output manifest.

#### Scenario: Fresh validate passed requires accepted solver
- **WHEN** the runner is about to record a fresh Web or Pwn `validate/passed` event
- **THEN** the underlying validation result SHALL include solver acceptance passed for the current output manifest
- **AND** otherwise the runner SHALL record failed validation diagnostics instead of a passing validate event
