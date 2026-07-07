## ADDED Requirements

### Requirement: Pwn primitive contracts are explicit and versioned
The system SHALL define pwn primitive contracts in a host-owned versioned primitive library. Each primitive definition SHALL include a stable `primitive_id`, `primitive_version`, `requires[]`, `disqualifiers[]`, an `evidence_schema`, an `offset_policy` of `required`, `optional`, or `none`, `difficulty_controls`, and diagnostic precedence metadata. Governed Design SHALL reference these definitions from `build_contract.pwn_primitive_contract`; Design output MUST NOT define or override primitive semantics. Any copied `evidence_requirements`, `offset_policy`, or primitive version fields in the build contract SHALL be validated against the host-owned definition.

The first version SHALL include only primitives whose evidence rules are deterministic enough for governed automation: `stack_overflow_ret2win_basic`, `format_string_controlled_printf_basic`, and `stack_overflow_with_leak_ret2libc`.

#### Scenario: Primitive definition contains positive and negative rules
- **WHEN** the primitive library defines `stack_overflow_ret2win_basic`
- **THEN** the definition includes required evidence for attacker-controlled data reaching a fixed-size stack object without an adequate bound
- **AND** the definition includes evidence for a concrete control-flow target such as a symbolized or host-locatable `win`, `print_flag`, or `read_flag` function, or a dynamic control-flow observation bound to the accepted artifact
- **AND** the definition includes disqualifiers for safe bounded reads or copies that prevent the overflow required by that primitive
- **AND** validation and prompt rendering consume the same host-owned primitive definition

#### Scenario: Unsupported complex primitives are explicit
- **WHEN** a governed pwn contract declares a heap, integer/OOB, SROP, JOP, or custom allocator primitive that is not present in the versioned primitive library
- **THEN** semantic audit fails with `pwn_primitive_unsupported`
- **AND** no BuildAttempt is marked successful for that contract unless a new supported primitive definition and new primitive contract are issued
- **AND** manual review may route the contract to triage but MUST NOT convert the unsupported contract into a governed pass or accepted ArtifactObservation
- **AND** current Design validation rejects unsupported primitive ids before governed BuildAttempt creation when it has access to the same host-owned primitive library

#### Scenario: Primitive evidence schema is field-level
- **WHEN** a primitive definition is loaded from the versioned library
- **THEN** its `evidence_schema` names required and optional fields for `source_evidence`, `binary_evidence`, `solver_evidence`, and `dynamic_evidence`
- **AND** each required field identifies its accepted evidence path, whether Design may declare it as intent, and whether Build/host validation must derive it from artifacts
- **AND** missing required fields produce `pwn_primitive_evidence_missing` with the field name and expected evidence path

#### Scenario: Offset policy is explicit
- **WHEN** a primitive definition does not require memory-layout offsets
- **THEN** its `offset_policy` is `none`
- **AND** validation MUST NOT require cyclic offset or debugger evidence for that primitive

### Requirement: Semantic audit proves declared pwn primitive realization
The system SHALL run semantic checks for governed pwn BuildAttempts in two stages. The pre-build source semantic gate SHALL run after implementation source exists and before image build, integrated with the existing host pre-build contract gate lifecycle; it SHALL compare source-level necessary conditions and disqualifiers against the declared primitive contract. The post-build artifact semantic audit SHALL run after compiled artifacts, attachments, and solver/debug evidence exist; it SHALL prove artifact identity and artifact-derived exploit facts required by the primitive. These checks SHALL fail closed when required evidence is missing, when a disqualifier is hit, or when the implementation realizes a different challenge type than the declared pwn primitive. Failing closed SHALL block success, accepted validation reuse, and production packaging; it SHALL NOT block retry, revalidation, or repair that preserves the same primitive contract. The audit outcome SHALL be persisted through existing validation diagnostics and ArtifactObservation contract checks/fingerprints, bound by the existing canonical `contract_sha256` and ArtifactObservation `artifact_manifest_sha256`.

#### Scenario: Bounded read disqualifies overflow-dependent primitives
- **WHEN** the declared primitive requires a stack overflow
- **AND** the actual source uses a bounded read such as `fgets(buf, sizeof(buf), stdin)` with no later unsafe copy into the same target
- **THEN** the pre-build source semantic gate fails with `pwn_primitive_disqualified`
- **AND** the failure records the bounded-read disqualifier, primitive id, rule id, and source location when available
- **AND** image build is not required before reporting that failure

#### Scenario: Source gate is not final exploit proof
- **WHEN** the pre-build source semantic gate finds source evidence for a declared stack-overflow primitive
- **THEN** Build may proceed to image build
- **AND** post-build artifact semantic audit must still prove binary, attachment, solver, and dynamic evidence required by the primitive
- **AND** a source-gate pass alone MUST NOT produce an accepted ArtifactObservation

#### Scenario: Another valid primitive can still pass
- **WHEN** the source contains a bounded password read and also contains a reachable format-string sink controlled by the player
- **THEN** source semantic gating MUST reject a stack-overflow primitive
- **AND** source semantic gating MAY allow a declared format-string primitive to continue if that primitive's source-level requirements are satisfied and no disqualifier is hit

#### Scenario: Non-pwn challenge type is not realized primitive
- **WHEN** the built source only performs a fixed secret comparison, exposes a plaintext flag path, or uses a debug/backdoor path to reach the flag
- **THEN** semantic audit fails memory-corruption primitives with `pwn_challenge_escape`
- **AND** the diagnostic identifies the escape class rather than synthesizing an exploit path

#### Scenario: Stronger primitive superset routes to policy review
- **WHEN** a supported primitive contract declares `stack_overflow_ret2win_basic`
- **AND** the built source appears to expose a stronger memory-corruption superset such as arbitrary write
- **AND** the primitive definition does not explicitly allow or disqualify that superset
- **THEN** semantic audit fails closed with `pwn_primitive_inconclusive`
- **AND** the diagnostic includes a difficulty-policy detail naming the stronger-than-declared primitive evidence
- **AND** only an accepted `observation_review_decision` with the configured pwn-semantic scope, distinct from the production-publication scope, may provide validation-layer effective acceptance for that inconclusive supported-primitive case

#### Scenario: Semantic failure remains repairable
- **WHEN** semantic audit fails with `pwn_primitive_evidence_missing` or `pwn_primitive_not_realized`
- **THEN** repair guidance identifies whether implementation artifacts, solver/debug evidence, or revalidation should be updated
- **AND** retry/repair keeps the original primitive id, primitive version, and canonical `contract_sha256`
- **AND** changing the primitive id requires a new DesignEvidence/build-contract version

### Requirement: Solver facts are artifact-derived
The system SHALL reject pwn primitive evidence that depends only on LLM text or design declarations. Offsets, gadgets, libc assumptions, PIE/canary facts, and final exploit values MUST be derived from actual build artifacts, debug evidence, shipped libraries, or reproducible dynamic probes when the primitive requires them.

#### Scenario: LLM offset is ignored
- **WHEN** a design or repair prompt contains a raw offset value
- **THEN** the semantic audit MUST NOT treat that value as proof
- **AND** required offset evidence must come from the configured artifact-derived evidence path

#### Scenario: Static secret check is not treated as pwn exploitability
- **WHEN** the built source only performs a fixed secret comparison and calls `win()` on equality
- **THEN** semantic audit MUST fail any memory-corruption primitive that lacks supporting source or binary evidence
- **AND** the failure MUST NOT synthesize an exploit plan or offset estimate

### Requirement: Semantic audit emits structured outcomes
The system SHALL expose semantic audit outcomes as validation diagnostics, not as new design-task statuses. Governed pwn BuildAttempts SHALL use structured failure codes including `pwn_primitive_not_realized`, `pwn_primitive_disqualified`, `pwn_primitive_evidence_missing`, `pwn_primitive_unsupported`, `pwn_challenge_escape`, `pwn_primitive_stale_evidence`, and `pwn_primitive_inconclusive`.

Diagnostic precedence SHALL be deterministic: unsupported primitive id, stale or unbound artifact evidence (`pwn_primitive_stale_evidence`), challenge escape, primitive-specific disqualifier hit, missing required evidence, non-equivalent realized primitive or non-pwn challenge type, inconclusive evidence, then generic not-realized fallback. Challenge escapes take precedence over primitive-specific safe-pattern disqualifiers so fixed secrets, plaintext flag paths, debug/backdoors, and organizer-file solver paths are diagnosed as challenge-type escapes even when the same source also lacks the declared unsafe primitive.

#### Scenario: Missing evidence produces a diagnostic
- **WHEN** the declared primitive requires binary-derived offset evidence
- **AND** validation cannot locate or derive the offset evidence
- **THEN** validation fails with `pwn_primitive_evidence_missing`
- **AND** the diagnostic identifies the missing evidence field and expected evidence path

#### Scenario: Unsupported primitive routes to review
- **WHEN** Design declares a primitive id that is not present in the versioned primitive library
- **THEN** the governed pwn path fails with `pwn_primitive_unsupported`
- **AND** manual review may triage the unsupported id but MUST NOT mark the unsupported contract successful

#### Scenario: Inconclusive evidence fails closed without bound review
- **WHEN** semantic audit cannot prove or reject a declared governed pwn primitive
- **AND** there is no accepted `observation_review_decision` with the configured pwn-semantic scope bound to the current ArtifactObservation for the same BuildAttempt, DesignEvidence/build-contract version, canonical `contract_sha256`, and ArtifactObservation `artifact_manifest_sha256`
- **THEN** validation fails closed with `pwn_primitive_inconclusive`
- **AND** production packaging MUST NOT accept the artifact unless validation-layer effective acceptance and corpus admission both pass

#### Scenario: Production review does not imply pwn semantic acceptance
- **WHEN** semantic audit returns `pwn_primitive_inconclusive`
- **AND** an accepted observation review exists only with the production-publication scope
- **THEN** validation-layer effective acceptance for the pwn semantic audit is still false
- **AND** acceptance requires a separate accepted pwn-semantic scoped observation review bound to the same ArtifactObservation and evidence hashes

#### Scenario: Manual review cannot override hard semantic failures
- **WHEN** semantic audit returns `pwn_primitive_unsupported`, `pwn_challenge_escape`, `pwn_primitive_disqualified`, `pwn_primitive_not_realized`, or stale artifact-derived evidence
- **THEN** manual review MUST NOT convert that observation into an accepted governed pass or effective ArtifactObservation acceptance
- **AND** acceptance requires repairing the implementation or issuing a new supported primitive contract
