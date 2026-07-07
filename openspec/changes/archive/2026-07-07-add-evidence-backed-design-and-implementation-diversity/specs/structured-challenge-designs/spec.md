## ADDED Requirements

### Requirement: Design consumes research plus a bounded corpus ledger

The Design prompt SHALL include the task reservation and an authoritative
bounded ledger snapshot containing aggregate occupancy over all sibling
reservations/committed designs, the configured number of nearest sibling and
historical designs, current quota usage, forbidden combined signatures, and a
`ledger_version`.

The Design prompt SHALL require the output to identify which supplied
research findings and compared challenge IDs support the proposed design. It
SHALL NOT invite Design to choose different governed profile values.

#### Scenario: Next Design sees earlier committed evidence

- **GIVEN** task A committed DesignEvidence before task B's prompt is rendered
- **WHEN** task B starts Design
- **THEN** B's ledger snapshot includes A's challenge ID and governed profile
- **AND** B must explain its solve/implementation difference from relevant
  compared entries

### Requirement: Successful Design commits evidence and a build contract

A successful Design SHALL create one live `design_evidence` row linked to the
ChallengeDesign and parent task. The evidence SHALL:

- cite only findings belonging to the task's ResearchRun;
- cite at least one designable finding;
- list concrete research claims used;
- compare against actual IDs present in the supplied ledger;
- provide a non-empty distinctness claim covering solve and implementation;
- reproduce the reserved profile exactly;
- provide a valid structured `build_contract`.

The build contract SHALL contain `required_profile`,
`required_player_actions`, `required_components`, `required_asset_flow`,
`forbidden_shortcuts`, `acceptance_tests`, and
`allowed_implementation_freedom`. `required_player_actions` SHALL contain at
least one non-empty action for every difficulty and SHALL agree with
`required_profile.solve.required_action`.

Negative and acceptance tests SHALL use a closed host-owned harness vocabulary.
Design may reference only declared artifacts/fixtures and closed assertions; it
SHALL NOT provide an executable name, arbitrary argv, shell string, or path
outside the challenge contract.

The harness vocabulary SHALL be defined in host code and rendered into prompts
from the same source. Initial harness kinds SHALL include only
`artifact_direct_run`, `fixture_assertion`, `solver_with_fixture`,
`solver_without_fixture`, and category-permitted `random_flag_rebuild`.
Each harness kind SHALL define its accepted fields and assertions. Artifact and
fixture references SHALL be symbolic IDs declared in the build contract, not
paths. Unknown harness kinds, assertions, undeclared references, path traversal,
argv, or shell strings SHALL fail contract validation.

Every entry in `required_asset_flow` SHALL contain a stable `stage_id`, a
verification harness proving the stage's declared output/capability exists, and
a dependency harness proving the downstream solve fails when that
output/capability is withheld or invalidated.

ChallengeDesign insertion, DesignEvidence insertion, and reservation
`reserved -> committed` SHALL happen in one transaction. A conflicting ledger
advance SHALL fail with `stale_design_ledger`.
Evidence SHALL be versioned with `unique(design_task_id, evidence_version)` and
a partial unique constraint allowing at most one row with
`superseded_at IS NULL` per task. Supersession SHALL store
`superseded_at`, `superseded_by_evidence_id`, and `supersession_reason`.

#### Scenario: Invented evidence is rejected

- **GIVEN** Design output cites a finding or compared challenge ID absent from
  its authoritative inputs
- **WHEN** output validation runs
- **THEN** the attempt fails
- **AND** no ChallengeDesign or DesignEvidence is committed
- **AND** the reservation remains reserved for retry

#### Scenario: Design cannot drift from reserved implementation

- **GIVEN** the reservation requires WASM/Rust and runtime-derived-key
  concealment
- **WHEN** Design returns ELF/C with single-byte XOR concealment
- **THEN** validation rejects it as a profile mismatch

### Requirement: Persisted Designs can be revised without in-place contract mutation

The system SHALL expose a service-backed Design revision operation for a task
in `designed`, `build_failed`, or `built` when it has no queued/running
BuildAttempt. A built task is eligible only when its current version has not
been included in a released production corpus batch. The operation SHALL run
under the task/request locks and SHALL:

- mark the live ChallengeDesign and DesignEvidence superseded;
- release the old reservation;
- allocate and attach a fresh reservation, allowing the same governed profile
  only as a revision of the same task;
- clear stale plan review metadata;
- transition the task to `draft`.

The next Design attempt creates a new ChallengeDesign/DesignEvidence version.
The operation SHALL never edit a committed build contract in place. Tasks with
an active BuildAttempt are rejected. A production-released built version is
also rejected and requires a new DesignTask/version. Prior BuildAttempts and
observations remain immutable history. The revised draft SHALL pass the
existing plan-review checkpoint before it can transition `draft -> queued`.

#### Scenario: Failed quality Design is revised

- **GIVEN** a task in `designed` whose latest Design has
  `quality_gate_passed = false`
- **WHEN** the operator requests Design revision
- **THEN** the old design/evidence are superseded
- **AND** a fresh reservation is attached
- **AND** the task returns to `draft`
- **AND** it must be approved before queueing another Design attempt

#### Scenario: Active Build prevents revision

- **GIVEN** a task with a queued or running BuildAttempt
- **WHEN** Design revision is requested
- **THEN** the request is rejected
- **AND** no design, evidence, reservation, or task status changes

#### Scenario: Corpus-blocked unpublished build can be redesigned

- **GIVEN** a built task was blocked by corpus review
- **AND** it has not been included in a released production batch
- **WHEN** Design revision is requested
- **THEN** the prior build remains historical
- **AND** the task returns to `draft` with a fresh reservation

#### Scenario: Released production version is immutable

- **GIVEN** a built task belongs to a released production corpus batch
- **WHEN** in-place Design revision is requested
- **THEN** the request is rejected
- **AND** remediation requires a new DesignTask/version

## MODIFIED Requirements

### Requirement: Quality gate is checked and recorded but does not block persistence

The quality gate SHALL continue to be recorded without blocking persistence of
a structurally valid Design, preserving the operator's ability to inspect the
failed Design. However, `quality_gate_passed = false` SHALL make that Design
ineligible for governed trial or production Build submission. A later valid
Design revision is required before governed construction can start.

#### Scenario: Failing quality gate persists but cannot build

- **WHEN** a Design passes structural validation but fails the quality gate
- **THEN** ChallengeDesign and its evidence are persisted for inspection with
  `quality_gate_passed = false`
- **AND** a governed trial or production Build submission is rejected with
  `design_quality_gate_failed`
