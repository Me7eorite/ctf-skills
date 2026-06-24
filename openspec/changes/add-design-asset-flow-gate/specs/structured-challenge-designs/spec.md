## ADDED Requirements

### Requirement: Medium and harder designs declare asset-flow gate fields

The system SHALL require structured challenge designs with difficulty `medium`,
`hard`, or `expert` to declare a substantive `difficulty_reason`, non-empty
`asset_flow`, non-empty `shortcut_closure`, and a shape-level `fingerprint`.

The `fingerprint` object SHALL include non-empty `entrypoint_type`,
`asset_flow_shape`, `flag_access_model`, and `scenario_type`.

#### Scenario: Medium design without difficulty reason is rejected

- **GIVEN** a parent design task with `difficulty = "medium"`
- **WHEN** the design JSON omits `difficulty_reason`
- **THEN** the design attempt is rejected
- **AND** no `challenge_designs` row is inserted

#### Scenario: Medium design without shortcut closure is rejected

- **GIVEN** a parent design task with `difficulty = "medium"`
- **WHEN** the design JSON has an otherwise valid asset flow
- **AND** `shortcut_closure` is missing or empty
- **THEN** the design attempt is rejected

#### Scenario: Medium design without complete fingerprint is rejected

- **GIVEN** a parent design task with `difficulty = "medium"`
- **WHEN** the design JSON omits `fingerprint.asset_flow_shape`
- **THEN** the design attempt is rejected

### Requirement: Asset-flow transitions must be concrete

The system SHALL count an asset-flow transition only when the produced asset or
capability is concrete and the next-stage dependency is specific. Generic
produced assets such as `access`, `data`, `result`, or `permission` SHALL NOT
count as effective transitions by themselves.

#### Scenario: Generic asset does not satisfy medium transition

- **GIVEN** a parent design task with `difficulty = "medium"`
- **WHEN** the only asset-flow stage produces `access`
- **AND** `why_next_stage_requires_it` says only `needed for next step`
- **THEN** the validator does not count that stage as an effective transition
- **AND** the design attempt is rejected

