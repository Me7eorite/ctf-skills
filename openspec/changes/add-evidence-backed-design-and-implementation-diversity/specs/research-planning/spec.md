## MODIFIED Requirements

### Requirement: Research output minimum quality gate

In addition to the existing source-shape, hash, parse, and finding-count rules,
the research quality gate SHALL require enough **designable** distinct
sub-techniques for the requested batch. A designable finding is exactly one
whose `kind` is `technique` or `variant`. Findings whose kind is `scenario` or
`prerequisite` SHALL remain valid evidence but SHALL NOT:

- count toward the distinct sub-technique floor;
- be selected as a DesignTask primary technique;
- conceal a monocultural technique pool by using distinct labels.

The strict production floor is:

`distinct(resolve_sub_technique(finding) for designable findings) >= target_count`.

When the explicit soft-pass setting
`RESEARCH_DIVERSITY_SOFT_PASS_BELOW_BY > 0` allows a run below that floor, the
run SHALL persist `research_runs.trial_only = true`. The marker SHALL not be
duplicated on GenerationRequest. Trial-only evidence MAY be used for development
builds but SHALL NOT pass a production corpus release gate.

#### Scenario: Scenario labels cannot fake technique diversity

- **GIVEN** a request with `target_count = 3`
- **AND** research returns one technique finding plus four scenario findings
  with distinct labels
- **WHEN** the research quality gate runs in strict mode
- **THEN** it fails with
  `insufficient_diversity:distinct=1,need=3`

#### Scenario: Technique and variant findings satisfy the floor

- **GIVEN** a request with `target_count = 3`
- **AND** research returns three designable findings resolving to three distinct
  sub-techniques
- **WHEN** the research quality gate runs
- **THEN** the diversity floor passes

#### Scenario: Soft-passed diversity is trial-only

- **GIVEN** a request with `target_count = 3` and only two distinct designable
  sub-techniques
- **AND** the operator configured one item of diversity slack
- **WHEN** the run completes
- **THEN** it is persisted with a trial-only marker
- **AND** that marker is available to downstream corpus admission
