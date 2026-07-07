## ADDED Requirements

### Requirement: Governed pwn source is checked before image build
For governed pwn BuildAttempts carrying `build_contract.pwn_primitive_contract`, Build SHALL run a host-owned source semantic gate after implementation source and deployment files exist and before invoking image build, compile-in-container build, or final artifact packaging. The gate SHALL inspect generated source paths and declared primitive requirements/disqualifiers that can be evaluated from source. It SHALL fail fast on source-level primitive mismatches and challenge escapes while leaving binary-only evidence to post-build validation.

The source gate SHALL be a necessary-condition gate only. Passing it SHALL NOT mark validation passed, create an accepted ArtifactObservation, or skip post-build artifact semantic audit.

#### Scenario: Source gate rejects bounded ret2win mismatch before image build
- **GIVEN** a governed pwn BuildAttempt declares `primitive_id = stack_overflow_ret2win_basic`
- **AND** implementation produced `deploy/src/xx.c`
- **AND** the only player input path uses `fgets(buf, sizeof(buf), stdin)` with no later unsafe copy into the target
- **WHEN** Build reaches the pre-build source semantic gate
- **THEN** the gate fails with `pwn_primitive_disqualified`
- **AND** image build is not invoked for that attempt
- **AND** repair guidance points to implementation repair or Design revision rather than binary/debug evidence repair

#### Scenario: Source gate cannot replace artifact proof
- **GIVEN** the source gate finds attacker-controlled overflow source evidence and a ret2win target
- **WHEN** Build continues to image build and post-build validation
- **THEN** the later artifact semantic audit must still prove compiled artifact identity, required symbols/mitigations, solver/debug evidence, and artifact-derived facts required by the primitive
- **AND** source-gate pass alone cannot mark the BuildAttempt succeeded

#### Scenario: Source gate records repairable diagnostics
- **WHEN** the source gate fails because a declared primitive is not represented in source
- **THEN** the failure is recorded as a structured validation/build diagnostic with primitive id, primitive version, rule id, and source location when available
- **AND** retry or automatic repair may regenerate source while preserving the same primitive contract

### Requirement: Governed pwn validation verifies primitive realization
For governed pwn BuildAttempts carrying `build_contract.pwn_primitive_contract`, host validation SHALL run semantic primitive audit after implementation artifacts exist and before marking validation passed. The audit SHALL inspect declared source paths, final player artifacts, binary metadata, solver/debug evidence, and primitive-library disqualifiers. It SHALL write its machine-readable findings into the existing validation result/history surface, preserve `contract_errors` / `validation_contract_errors` compatibility for callers that still consume string diagnostics, add structured entries to validation failure details when available, and persist the semantic-audit outcome in the current ArtifactObservation's `contract_checks` and primitive fingerprint material.

#### Scenario: Declared primitive is disqualified
- **GIVEN** a governed pwn BuildAttempt declares a stack-overflow primitive
- **AND** Build produces source whose only player input path is bounded and does not reach an overflow sink
- **WHEN** host validation runs
- **THEN** validation fails with `pwn_primitive_disqualified`
- **AND** the BuildAttempt cannot become succeeded from flag-match or metadata evidence alone

#### Scenario: Challenge escape is not accepted as primitive realization
- **GIVEN** a governed pwn BuildAttempt declares a memory-corruption primitive
- **AND** Build produces only a fixed secret check, plaintext flag path, or debug backdoor
- **WHEN** host validation runs
- **THEN** validation fails with `pwn_challenge_escape`
- **AND** the diagnostic identifies the challenge-type escape class

#### Scenario: Declared primitive is not realized
- **GIVEN** a governed pwn BuildAttempt declares a memory-corruption primitive
- **AND** Build produces a non-equivalent challenge type that is not a fixed secret check, plaintext flag path, or debug backdoor
- **WHEN** host validation runs
- **THEN** validation fails with `pwn_primitive_not_realized`
- **AND** the diagnostic identifies the observed non-equivalent challenge type when available

#### Scenario: Realized primitive can pass validation
- **GIVEN** a governed pwn BuildAttempt declares a stack-overflow primitive
- **AND** Build produces source, binary, and solver evidence satisfying that primitive's requirements
- **WHEN** host validation runs
- **THEN** the semantic primitive audit passes
- **AND** normal artifact, reference-solve, and contract validation must still pass before the BuildAttempt can succeed

### Requirement: Pwn primitive mismatch is a build failure, not automatic redesign
When semantic audit fails because the implementation does not realize the declared primitive, Build SHALL report a structured validation failure. That failure SHALL block BuildAttempt success and production eligibility, but SHALL NOT prohibit retry, revalidation, or automatic repair. Build repair MAY adjust implementation artifacts, solver/debug evidence, or missing artifact-derived proof to satisfy the existing contract, but it SHALL NOT change `primitive_id`, primitive version, disqualifier semantics, or difficulty controls without a new DesignEvidence/build-contract version.

#### Scenario: Repair cannot rewrite primitive intent
- **GIVEN** a governed pwn BuildAttempt declares `primitive_id = stack_overflow_ret2win_basic`
- **WHEN** semantic audit fails because the implementation is only a fixed secret check
- **THEN** automatic repair may fix the implementation toward the declared stack-overflow contract
- **AND** it must not silently change the contract to `static_secret_check` or a different primitive id

#### Scenario: Missing evidence can be repaired
- **GIVEN** a governed pwn BuildAttempt declares `primitive_id = stack_overflow_with_leak_ret2libc`
- **WHEN** semantic audit fails with `pwn_primitive_evidence_missing` for artifact-derived libc base, gadget, or offset evidence
- **THEN** automatic repair may add or rerun debugger/debug-report/solver evidence against the accepted artifact
- **AND** the repaired attempt must still use the original primitive contract and canonical `contract_sha256`

#### Scenario: Retry remains on the same primitive contract
- **GIVEN** a governed pwn BuildAttempt fails semantic audit
- **WHEN** the operator retries or revalidates that attempt lineage
- **THEN** the retry/revalidation uses the same DesignEvidence/build-contract version and primitive contract
- **AND** a primitive-id change requires a Design revision before a new BuildAttempt can use it

#### Scenario: Contract change requires new design evidence
- **WHEN** an operator wants to change the intended primitive for a governed pwn task
- **THEN** the system requires a new DesignEvidence/build-contract version
- **AND** retries of the old BuildAttempt continue to use the original primitive contract

#### Scenario: Semantic audit observation is bound to exact evidence
- **WHEN** host validation records a semantic-audit pass or failure
- **THEN** the observation includes the BuildAttempt id, DesignEvidence/build-contract version, canonical `contract_sha256`, artifact manifest hash, primitive id, primitive version, and validation diagnostic details
- **AND** later retries, revalidations, or release packaging cannot reuse the observation when any bound value differs

### Requirement: Release packaging uses accepted build evidence
Release packaging for governed pwn artifacts SHALL use the existing accepted BuildAttempt artifact and the existing production corpus-admission gate. A pwn primitive pass or scoped observation-review acceptance is a validation-layer prerequisite; it does not bypass corpus membership, member decision, aggregate decision, or non-overrideable corpus rules. Release packaging SHALL require a matching canonical `contract_sha256`, artifact manifest hash, and effectively accepted ArtifactObservation. Release packaging SHALL NOT invoke a compiler or relink the pwn binary as part of packaging.

#### Scenario: Release refuses stale validation evidence
- **WHEN** a governed pwn artifact manifest differs from the manifest bound to the accepted validation observation
- **THEN** release packaging MUST reject the artifact as stale or unverified
- **AND** it must not rebuild the binary to make the package pass

#### Scenario: Packaging does not recompile
- **WHEN** a governed pwn BuildAttempt has accepted validation evidence
- **THEN** release packaging copies or bundles the accepted artifacts
- **AND** it does not run a compile, link, or source-regeneration step

#### Scenario: Manual review cannot float across artifacts
- **WHEN** inconclusive semantic evidence is accepted by manual review
- **THEN** the accepted `observation_review_decision` is valid only for the current ArtifactObservation with the same BuildAttempt, DesignEvidence/build-contract version, canonical `contract_sha256`, artifact manifest hash, review scope, reviewer, and rationale
- **AND** release packaging rejects the artifact if any bound value differs, the scoped observation review is missing, or corpus admission is not effectively accepted
