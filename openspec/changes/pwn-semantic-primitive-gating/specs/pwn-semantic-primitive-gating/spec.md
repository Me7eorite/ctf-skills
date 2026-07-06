## ADDED Requirements

### Requirement: Pwn primitive contracts are explicit and versioned
The system SHALL define pwn primitive contracts in a versioned primitive library. Each primitive definition SHALL include a stable `primitive_id`, `primitive_version`, `requires[]`, `disqualifiers[]`, an `evidence_schema`, an `offset_policy` of `required`, `optional`, or `none`, `difficulty_controls`, and diagnostic precedence metadata.

The first version SHALL include only primitives whose evidence rules are deterministic enough for governed automation: `stack_overflow_ret2win_basic`, `format_string_controlled_printf_basic`, and `stack_overflow_with_leak_ret2libc`.

#### Scenario: Primitive definition contains positive and negative rules
- **WHEN** the primitive library defines `stack_overflow_ret2win_basic`
- **THEN** the definition includes required evidence for attacker-controlled data reaching a fixed-size stack object without an adequate bound
- **AND** the definition includes evidence for a concrete control-flow target such as a symbolized or host-locatable `win`, `print_flag`, or `read_flag` function, or a dynamic control-flow observation bound to the accepted artifact
- **AND** the definition includes disqualifiers for safe bounded reads or copies that prevent the overflow required by that primitive

#### Scenario: Unsupported complex primitives are explicit
- **WHEN** a governed pwn contract declares a heap, integer/OOB, SROP, JOP, or custom allocator primitive that is not present in the versioned primitive library
- **THEN** semantic audit fails with `pwn_primitive_unsupported`
- **AND** no BuildAttempt is marked successful for that contract without an explicit supported primitive definition or bound manual review observation

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
The system SHALL run a semantic audit for governed pwn BuildAttempts after Build has produced implementation artifacts. The audit SHALL compare the declared primitive contract with actual source, binary, and solver evidence. The audit SHALL fail closed when required evidence is missing, when a disqualifier is hit, or when the implementation realizes a different challenge type than the declared pwn primitive.

#### Scenario: Bounded read disqualifies overflow-dependent primitives
- **WHEN** the declared primitive requires a stack overflow
- **AND** the actual source uses a bounded read such as `fgets(buf, sizeof(buf), stdin)` with no later unsafe copy into the same target
- **THEN** semantic audit fails with `pwn_primitive_disqualified`
- **AND** the failure records the bounded-read disqualifier, primitive id, rule id, and source location when available

#### Scenario: Another valid primitive can still pass
- **WHEN** the source contains a bounded password read and also contains a reachable format-string sink controlled by the player
- **THEN** semantic audit MUST reject a stack-overflow primitive
- **AND** semantic audit MAY accept a declared format-string primitive if that primitive's requirements are satisfied and no disqualifier is hit

#### Scenario: Non-pwn challenge type is not realized primitive
- **WHEN** the built source only performs a fixed secret comparison, exposes a plaintext flag path, or uses a debug/backdoor path to reach the flag
- **THEN** semantic audit fails memory-corruption primitives with `pwn_challenge_escape`
- **AND** the diagnostic identifies the escape class rather than synthesizing an exploit path

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
The system SHALL expose semantic audit outcomes as validation diagnostics, not as new design-task statuses. Governed pwn BuildAttempts SHALL use structured failure codes including `pwn_primitive_not_realized`, `pwn_primitive_disqualified`, `pwn_primitive_evidence_missing`, `pwn_primitive_unsupported`, `pwn_challenge_escape`, and `pwn_primitive_inconclusive`.

Diagnostic precedence SHALL be deterministic: unsupported primitive id, primitive-specific disqualifier hit, challenge escape, missing required evidence, non-equivalent realized primitive or non-pwn challenge type, inconclusive evidence, then generic not-realized fallback.

#### Scenario: Missing evidence produces a diagnostic
- **WHEN** the declared primitive requires binary-derived offset evidence
- **AND** validation cannot locate or derive the offset evidence
- **THEN** validation fails with `pwn_primitive_evidence_missing`
- **AND** the diagnostic identifies the missing evidence field and expected evidence path

#### Scenario: Unsupported primitive routes to review
- **WHEN** Design declares a primitive id that is not present in the versioned primitive library
- **THEN** the governed pwn path fails with `pwn_primitive_unsupported`
- **AND** no BuildAttempt is marked successful for that contract

#### Scenario: Inconclusive evidence fails closed without bound review
- **WHEN** semantic audit cannot prove or reject a declared governed pwn primitive
- **AND** there is no manual review observation bound to the same BuildAttempt, DesignEvidence/build-contract version, primitive contract hash, and artifact manifest hash
- **THEN** validation fails closed with `pwn_primitive_inconclusive`
- **AND** release packaging MUST NOT accept the artifact

#### Scenario: Manual review cannot override hard semantic failures
- **WHEN** semantic audit returns `pwn_primitive_unsupported`, `pwn_primitive_disqualified`, `pwn_primitive_not_realized`, `pwn_challenge_escape`, or stale artifact-derived evidence
- **THEN** manual review MUST NOT convert that observation into an accepted governed pass
- **AND** acceptance requires repairing the implementation or issuing a new supported primitive contract
