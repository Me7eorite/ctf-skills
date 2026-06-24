## MODIFIED Requirements

### Requirement: Research output minimum quality gate

The research quality gate SHALL require enough **designable mechanism capacity**
in addition to the existing source-shape, hash, parse, and finding-count rules.
A designable finding is exactly one whose `kind` is `technique` or `variant`.
Findings whose kind is `scenario` or `prerequisite` SHALL remain valid evidence
but SHALL NOT:

- count toward primary designable mechanism capacity;
- be selected as a DesignTask primary technique;
- conceal a monocultural technique pool by using distinct labels.

The strict production floor SHALL be evaluated by the profile allocator: the
designable findings must allow `target_count` compatible governed profile
reservations under the active category policy. Distinct sub-technique count MAY
be used as a diagnostic input, but SHALL NOT be the sole production floor.

When the explicit soft-pass setting
`RESEARCH_DIVERSITY_SOFT_PASS_BELOW_BY > 0` allows a run below the allocator
capacity floor, the run SHALL persist `research_runs.trial_only = true`. The
marker SHALL not be duplicated on GenerationRequest. Trial-only evidence MAY be
used for development builds but SHALL NOT pass a production corpus release gate.

#### Scenario: Scenario labels cannot fake designable capacity

- **GIVEN** a request with `target_count = 3`
- **AND** research returns one technique finding plus four scenario findings
  with distinct labels
- **WHEN** the profile allocator evaluates production readiness
- **THEN** it fails with
  `insufficient_designable_capacity`

#### Scenario: Repeated sub-technique can pass with distinct governed profiles

- **GIVEN** a request with `target_count = 3`
- **AND** research returns three designable findings with the same normalized
  sub-technique
- **AND** the active profile policy can reserve three distinct solve and
  implementation profiles from those findings
- **WHEN** the research quality gate runs
- **THEN** the production readiness floor passes
- **AND** same-sub-technique risk remains available to downstream diagnostics

#### Scenario: Soft-passed diversity is trial-only

- **GIVEN** a request with `target_count = 3` and only two distinct designable
  governed profile reservations available
- **AND** the operator configured one item of readiness slack
- **WHEN** the run completes
- **THEN** it is persisted with a trial-only marker
- **AND** that marker is available to downstream corpus admission
