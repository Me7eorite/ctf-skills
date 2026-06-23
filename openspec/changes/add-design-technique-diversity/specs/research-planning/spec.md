## ADDED Requirements

### Requirement: Research findings carry a weakly-enforced technique family

Every research finding SHALL carry an optional `technique_family` drawn from a
controlled per-category lane vocabulary (the Technique Lanes defined in
`skills/design-challenges/references/category-tactics.md`, exposed in code by
`src/domain/design/technique_taxonomy.py`). The Hermes Research Agent prompt SHALL
instruct the agent to set `technique_family` for each finding from that
vocabulary.

Enforcement SHALL be weak and derivable:

- A finding that omits `technique_family`, or supplies a value outside the
  vocabulary, SHALL be accepted with the value coerced to `other` and a logged
  warning. A single unclassifiable finding SHALL NOT fail the research run.
- Consumers SHALL resolve the effective family through
  `resolve_family(finding)`, which returns the stored value when present and
  valid, otherwise derives a family from the finding `label`, otherwise
  `other`. Legacy findings persisted before this requirement (NULL column)
  SHALL therefore resolve without backfill.

#### Scenario: Agent-supplied valid family is preserved

- **GIVEN** a finding whose agent-supplied `technique_family` is a valid lane for the run category
- **WHEN** the finding is persisted
- **THEN** `technique_family` is stored verbatim
- **AND** `resolve_family` returns that stored value

#### Scenario: Unknown family is coerced, not rejected

- **GIVEN** a finding whose `technique_family` is not in the category lane vocabulary
- **WHEN** the run is parsed and persisted
- **THEN** the finding is accepted
- **AND** its effective family resolves to `other`
- **AND** a warning is logged

#### Scenario: Legacy finding without the field still resolves

- **GIVEN** a finding persisted before this requirement with NULL `technique_family`
- **WHEN** a consumer calls `resolve_family`
- **THEN** a family is derived from the finding `label` (or `other` when no keyword matches)
- **AND** no backfill or migration of the legacy row is required

### Requirement: Research run report surfaces technique-family distribution

The research run report SHALL include the distribution of effective
`technique_family` values across the run's findings and the ratio of findings
resolving to `other`. When the `other` ratio exceeds
`RESEARCH_FAMILY_OTHER_WARN_RATIO` (non-negative float, default `0.30`) the
report SHALL emit a neutral warning indicating the classification miss-rate is
high and that either the lane vocabulary or the research scope may need review.
The warning SHALL NOT assert a single cause and SHALL NOT fail the run.

#### Scenario: High other-ratio raises a neutral warning

- **GIVEN** a completed research run where more than 30% of findings resolve to family `other`
- **WHEN** the run report is generated
- **THEN** the report includes the family distribution
- **AND** the report carries a neutral "classification miss-rate high" warning
- **AND** the run status is unaffected

#### Scenario: Distribution is reported even for legacy findings

- **GIVEN** a run whose findings all have NULL `technique_family`
- **WHEN** the run report is generated
- **THEN** the report shows a distribution derived via `resolve_family`
