## ADDED Requirements

### Requirement: Pwn primitive contracts are explicit and versioned
The system SHALL define pwn primitive contracts in a versioned primitive library. Each primitive definition SHALL include a stable `primitive_id`, `requires[]`, `disqualifiers[]`, an `evidence_schema`, and an `offset_policy` of `required`, `optional`, or `none`.

#### Scenario: Primitive definition contains positive and negative rules
- **WHEN** the primitive library defines `stack_overflow_basic`
- **THEN** the definition includes required evidence for attacker-controlled data reaching a fixed-size object without an adequate bound
- **AND** the definition includes disqualifiers for safe bounded reads or copies that prevent the overflow required by that primitive

#### Scenario: Offset policy is explicit
- **WHEN** a primitive definition does not require memory-layout offsets
- **THEN** its `offset_policy` is `none`
- **AND** validation MUST NOT require cyclic offset or debugger evidence for that primitive

### Requirement: Semantic audit proves declared pwn primitive realization
The system SHALL run a semantic audit for governed pwn BuildAttempts after Build has produced implementation artifacts. The audit SHALL compare the declared primitive contract with actual source, binary, and solver evidence. The audit SHALL fail closed when required evidence is missing, when a disqualifier is hit, or when the implementation realizes a different challenge type than the declared pwn primitive.

#### Scenario: Bounded read rejects only overflow-dependent primitives
- **WHEN** the declared primitive requires a stack overflow
- **AND** the actual source uses a bounded read such as `fgets(buf, sizeof(buf), stdin)` with no later unsafe copy into the same target
- **THEN** semantic audit fails with `pwn_primitive_not_realized`
- **AND** the failure records the bounded-read disqualifier that blocked the declared primitive

#### Scenario: Another valid primitive can still pass
- **WHEN** the source contains a bounded password read and also contains a reachable format-string sink controlled by the player
- **THEN** semantic audit MUST reject a stack-overflow primitive
- **AND** semantic audit MAY accept a declared format-string primitive if that primitive's requirements are satisfied and no disqualifier is hit

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
The system SHALL expose semantic audit outcomes as validation diagnostics, not as new design-task statuses. Governed pwn BuildAttempts SHALL use structured failure codes including `pwn_primitive_not_realized`, `pwn_primitive_disqualified`, `pwn_primitive_evidence_missing`, and `pwn_primitive_unsupported`.

#### Scenario: Missing evidence produces a diagnostic
- **WHEN** the declared primitive requires binary-derived offset evidence
- **AND** validation cannot locate or derive the offset evidence
- **THEN** validation fails with `pwn_primitive_evidence_missing`
- **AND** the diagnostic identifies the missing evidence field

#### Scenario: Unsupported primitive routes to review
- **WHEN** Design declares a primitive id that is not present in the versioned primitive library
- **THEN** the governed pwn path fails with `pwn_primitive_unsupported`
- **AND** no BuildAttempt is marked successful for that contract
