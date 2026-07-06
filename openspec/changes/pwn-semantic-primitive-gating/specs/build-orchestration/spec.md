## ADDED Requirements

### Requirement: Governed pwn validation verifies primitive realization
For governed pwn BuildAttempts carrying a pwn primitive contract, host validation SHALL run semantic primitive audit after implementation artifacts exist and before marking validation passed. The audit SHALL inspect declared source paths, final player artifacts, binary metadata, solver/debug evidence, and primitive-library disqualifiers.

#### Scenario: Declared primitive is not realized
- **GIVEN** a governed pwn BuildAttempt declares a stack-overflow primitive
- **AND** Build produces source whose only player input path is bounded and does not reach an overflow sink
- **WHEN** host validation runs
- **THEN** validation fails with `pwn_primitive_not_realized`
- **AND** the BuildAttempt cannot become succeeded from flag-match or metadata evidence alone

#### Scenario: Realized primitive can pass validation
- **GIVEN** a governed pwn BuildAttempt declares a stack-overflow primitive
- **AND** Build produces source, binary, and solver evidence satisfying that primitive's requirements
- **WHEN** host validation runs
- **THEN** the semantic primitive audit passes
- **AND** normal artifact, reference-solve, and contract validation must still pass before the BuildAttempt can succeed

### Requirement: Pwn primitive mismatch is a build failure, not automatic redesign
When semantic audit fails because the implementation does not realize the declared primitive, Build SHALL report a structured validation failure. Build repair MAY adjust implementation artifacts to satisfy the existing contract, but it SHALL NOT change `primitive_id`, primitive version, or disqualifier semantics without a new DesignEvidence/build-contract version.

#### Scenario: Repair cannot rewrite primitive intent
- **GIVEN** a governed pwn BuildAttempt declares `primitive_id = stack_overflow_basic`
- **WHEN** semantic audit fails because the implementation is only a fixed secret check
- **THEN** automatic repair may fix the implementation toward the declared stack-overflow contract
- **AND** it must not silently change the contract to `static_secret_check`

#### Scenario: Contract change requires new design evidence
- **WHEN** an operator wants to change the intended primitive for a governed pwn task
- **THEN** the system requires a new DesignEvidence/build-contract version
- **AND** retries of the old BuildAttempt continue to use the original primitive contract

### Requirement: Release packaging uses accepted build evidence
Release packaging for governed pwn artifacts SHALL use the existing accepted BuildAttempt artifact, matching contract hash, artifact manifest, and host validation observation. Release packaging SHALL NOT invoke a compiler or relink the pwn binary as part of packaging.

#### Scenario: Release refuses stale validation evidence
- **WHEN** a governed pwn artifact manifest differs from the manifest bound to the accepted validation observation
- **THEN** release packaging MUST reject the artifact as stale or unverified
- **AND** it must not rebuild the binary to make the package pass

#### Scenario: Packaging does not recompile
- **WHEN** a governed pwn BuildAttempt has accepted validation evidence
- **THEN** release packaging copies or bundles the accepted artifacts
- **AND** it does not run a compile, link, or source-regeneration step
