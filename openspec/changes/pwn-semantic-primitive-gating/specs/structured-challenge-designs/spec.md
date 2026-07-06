## ADDED Requirements

### Requirement: Pwn Design declares a primitive contract
For `category = pwn`, structured Design SHALL declare a pwn primitive contract as part of the validated design/build-contract payload. The contract SHALL include `primitive_id`, `primitive_version`, `intended_implementation_features`, `evidence_requirements`, `offset_policy`, and `difficulty_controls`. The Design stage SHALL NOT claim that generated source, compiled binary evidence, debugger output, offsets, gadgets, libc bases, or runtime proof has already been observed.

The primitive contract SHALL describe implementation intent and validation requirements, not proof. Disqualifier hits SHALL be computed by Build/host validation from actual artifacts rather than accepted from Design-stage self-attestation. A governed pwn primitive contract SHALL enable strict semantic validation by default; it MUST NOT rely on a later manual metadata toggle to activate primitive gating.

#### Scenario: Pwn design includes primitive contract
- **WHEN** a pwn Design attempt validates successfully
- **THEN** the persisted design payload or associated build contract includes a pwn primitive contract
- **AND** the contract identifies the intended primitive without raw offsets or final exploit constants
- **AND** the contract uses intent-oriented fields rather than claiming source or binary proof
- **AND** the contract carries difficulty controls for intended mitigations, intended solve steps, and forbidden shortcuts

#### Scenario: Design cannot claim source proof before Build
- **WHEN** a pwn Design output includes semantic proof that depends on generated source, compiled layout, debugger output, or runtime traces
- **THEN** validation MUST reject that proof as Design-stage evidence
- **AND** source or binary proof remains owned by Build/host validation

#### Scenario: Unsupported primitive is not silently accepted
- **WHEN** a pwn Design output declares a primitive id outside the current versioned primitive library
- **THEN** the Design attempt fails with a machine-readable unsupported-primitive diagnostic or routes to explicit review
- **AND** the contract cannot be treated as governed automation until a supported primitive definition and new primitive contract exist
- **AND** review may triage the unsupported primitive but MUST NOT mark that unsupported contract as a governed pass

### Requirement: Pwn primitive contract preserves existing design shape
The pwn primitive contract SHALL extend the existing structured design and build-contract model without replacing the required challenge JSON fields. Existing required fields such as `id`, `title`, `category`, `difficulty`, `points`, `deployment`, `primary_technique`, `learning_objective`, `prompt`, `artifacts`, `flag_location`, `validation`, and `hints` SHALL remain required.

#### Scenario: Existing challenge fields remain required
- **WHEN** a pwn Design output includes a valid primitive contract but omits `deployment`
- **THEN** the Design attempt fails existing JSON validation
- **AND** no `challenge_designs` row is inserted

#### Scenario: Primitive contract does not replace primary technique
- **WHEN** a pwn Design output declares `primitive_id = stack_overflow_ret2win_basic`
- **THEN** it still includes the existing `primary_technique` field
- **AND** downstream shard rendering can preserve both the old technique field and the new primitive contract

#### Scenario: Difficulty controls describe player experience
- **WHEN** a pwn Design output declares a primitive contract
- **THEN** the contract includes difficulty controls such as intended mitigations, intended solve steps, and forbidden shortcuts
- **AND** those controls do not replace normal difficulty, prompt, validation, or artifact fields
- **AND** forbidden shortcuts include fixed passwords, plaintext flag exposure, debug/backdoor paths, solver reads from organizer files, and artifact/source evidence drift

### Requirement: No-valid pwn primitive is recorded as attempt failure or review outcome
If structured Design cannot declare a supported pwn primitive contract for a pwn task, the system SHALL fail the Design attempt with a machine-readable diagnostic or route it to an explicit human-review/non-pwn workflow. The system SHALL NOT add `NO_VALID_PWN_PRIMITIVE` to the `design_tasks.status` enum.

#### Scenario: Design cannot declare a pwn primitive
- **WHEN** a pwn Design output describes only a fixed secret check and declares no supported pwn primitive
- **THEN** the Design attempt fails with a machine-readable no-valid-pwn-primitive diagnostic
- **AND** the parent task follows existing retry or failure rules

#### Scenario: No new design-task status is introduced
- **WHEN** the no-valid-pwn-primitive diagnostic is recorded
- **THEN** `design_tasks.status` remains within the existing lifecycle values
- **AND** operators can inspect the diagnostic through attempt or review details
