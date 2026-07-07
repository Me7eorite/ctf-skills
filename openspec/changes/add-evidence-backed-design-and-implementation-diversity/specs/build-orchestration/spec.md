## ADDED Requirements

### Requirement: Build admission requires committed evidence and a passing design

For governed `trial` and `production` builds, Build submission SHALL require
the latest ChallengeDesign to have `quality_gate_passed = true`, one live
committed DesignEvidence row, a committed matching reservation, and a
category-valid build contract. New production submissions SHALL always be
governed. Historical designs without evidence MAY be rebuilt only through an
explicit `legacy_trial` mode that is recorded as non-production and cannot pass
production corpus admission.

Evidence, reservation, contract, and quality-gate admission failures SHALL
return a machine-readable reason and SHALL create no BuildAttempt, staged
shard, counter increment, difficulty review, or parent status change.

Governed evidence and quality-gate admission checks SHALL run before the
pre-build difficulty review. When they fail, the system SHALL NOT record a
difficulty review, supersede the current draft, requeue the task, or otherwise
invoke difficulty-review retry behavior. Difficulty review MAY run only after a
live committed DesignEvidence row and category-valid build contract are
available. A later difficulty-review failure SHALL request a new Design
revision rather than mutating a committed DesignEvidence/build contract in
place, SHALL NOT directly requeue the task, and SHALL NOT bypass the `draft ->
queued` plan-review checkpoint.

Governed fields SHALL come from the committed contract. The renderer SHALL NOT
default missing governed language/runtime, artifact format, interaction,
control structure, solve action, or flag-concealment fields.

#### Scenario: Failed design quality cannot enter construction

- **GIVEN** an otherwise eligible designed task whose latest design has
  `quality_gate_passed = false`
- **WHEN** governed trial or production Build submission is requested
- **THEN** it fails with `design_quality_gate_failed`
- **AND** no filesystem or database build work is created
- **AND** no pre-build difficulty review is recorded
- **AND** the design task status remains unchanged

#### Scenario: Legacy trial cannot become production evidence

- **GIVEN** a historical design without committed DesignEvidence
- **WHEN** the operator submits it through explicit `legacy_trial` rebuild mode
- **THEN** the build is marked non-production
- **AND** the resulting attempt cannot satisfy production corpus admission

#### Scenario: Missing governed field does not fall back

- **GIVEN** a committed contract missing its required artifact format
- **WHEN** the shard payload is rendered
- **THEN** submission fails with `build_contract_incomplete`
- **AND** it does not default to ELF

#### Scenario: Governed difficulty failure requests revision instead of requeue

- **GIVEN** a governed task with committed DesignEvidence and a category-valid
  build contract
- **AND** its pre-build difficulty review fails
- **WHEN** Build submission is requested
- **THEN** the failed review is recorded with revision diagnostics
- **AND** no BuildAttempt, staged shard, counter increment, or parent build
  transition is created
- **AND** the committed DesignEvidence and build contract remain immutable
- **AND** any retry requires the service-backed Design revision path and the
  normal `draft -> queued` plan-review checkpoint

### Requirement: Build is a contract-bound construction stage

The attributed shard SHALL include immutable DesignEvidence identity and the
complete build contract. Build SHALL implement that contract and MAY vary only
fields listed in `allowed_implementation_freedom`. Build SHALL NOT change the
governed profile, required player actions, required asset flow, or negative-test
meaning.

When the contract cannot be implemented, Build SHALL report
`design_unbuildable` with a concrete diagnostic and SHALL NOT substitute a
generic implementation. A contract change requires a new Design evidence
version and a clean build.

Each BuildAttempt SHALL persist the exact `design_evidence_id` and contract
hash it builds. Retry/resume keeps those values unchanged. A new DesignEvidence
version creates a fresh build lineage rather than reusing an old attempt.

#### Scenario: Infeasible WASM design is not rewritten as ELF

- **GIVEN** a build contract requiring WASM
- **WHEN** the Build worker cannot produce a valid WASM artifact
- **THEN** the attempt fails as `design_unbuildable`
- **AND** no ELF substitute is accepted

### Requirement: Host validation observes and verifies the built implementation

Before a BuildAttempt can succeed, host validation SHALL create a current
ArtifactObservation version containing the observed profile, contract
comparisons, negative-test results, and fingerprints. Existing artifact and
reference-solve checks remain mandatory.

Host validation SHALL run the contract's closed host-owned acceptance and
negative-test harnesses, compare required governed fields with observed facts,
and verify required asset-flow stages using declared stage checks. Design input
cannot select arbitrary executables or shell commands. The harness registry
SHALL be host-owned and closed: each `test_kind` declares allowed fields,
assertions, artifact references, and fixture references. Contract validation
SHALL reject unknown harness kinds, unknown assertions, undeclared
artifact/fixture IDs, absolute paths, traversal, argv, and shell strings.
Metadata alone is not proof. A required observed field that is unknown SHALL produce an
ArtifactObservation with `status = inconclusive` and cannot be treated as an
accepted build result without a separate allowed observation review whose policy
scope permits build success.

Each observation SHALL bind the exact `build_attempt_id`,
`observation_version`, `design_evidence_id`, canonical `contract_sha256`, and
`artifact_manifest_sha256`. A changed contract or artifact manifest invalidates
the observation. Revalidation SHALL insert a new observation version and
supersede the previous current observation instead of overwriting historical
results.

Failure codes SHALL include `implementation_contract_mismatch`,
`unintended_solution_succeeded`, `asset_flow_not_required`,
`solver_not_artifact_derived`, and `observation_inconclusive`.

#### Scenario: Declared language differs from the built artifact

- **GIVEN** a contract requiring Rust and observation establishing a C/GCC
  artifact
- **WHEN** host validation compares profiles
- **THEN** validation fails with `implementation_contract_mismatch`

#### Scenario: Direct-run shortcut breaks the contract

- **GIVEN** a negative test declaring direct execution must not reveal the flag
- **WHEN** direct execution prints the flag
- **THEN** validation fails with `unintended_solution_succeeded`

#### Scenario: Passing contract produces durable observation

- **WHEN** existing validation, acceptance tests, negative tests, profile
  comparison, and asset-flow checks all pass
- **THEN** ArtifactObservation is persisted with `status = passed`
- **AND** normal reconciliation may mark the BuildAttempt succeeded

#### Scenario: Inconclusive observation requires separate review

- **GIVEN** a governed fact cannot be established by the category observer
- **WHEN** validation completes
- **THEN** ArtifactObservation is persisted with `status = inconclusive`
- **AND** the BuildAttempt cannot become succeeded or production-successful
  without an allowed observation review decision whose scope permits that use

### Requirement: Revalidation repeats contract and observation checks

Per-attempt revalidation SHALL use current disk evidence and the committed
DesignEvidence/build contract. It SHALL insert a new current
ArtifactObservation version and SHALL not promote a failed attempt when only the
legacy flag-match checks pass.

#### Scenario: Legacy flag validation passes but contract mismatch remains

- **GIVEN** a failed attempt whose solver now prints the expected flag
- **AND** its artifact still violates the required profile
- **WHEN** revalidation runs
- **THEN** the attempt remains failed with
  `implementation_contract_mismatch`

### Requirement: Build reconciliation follows the current DesignEvidence

BuildReconciler SHALL roll a parent DesignTask to `building`, `built`, or
`build_failed` only from a BuildAttempt whose `design_evidence_id` equals the
task's `current_design_evidence_id`. Attempts for superseded evidence remain
immutable history and SHALL NOT change the revised task's state.

#### Scenario: Old success cannot overwrite revised draft

- **GIVEN** task T has an old succeeded attempt bound to evidence E1
- **AND** T was revised to draft with current evidence/reservation version E2
- **WHEN** reconciliation runs
- **THEN** the E1 attempt remains succeeded as history
- **AND** T remains draft rather than returning to built

### Requirement: BuildAttempt detail exposes validation and corpus governance

The BuildAttempt detail API SHALL expose the attempt's bound
`design_evidence_id`, contract hash, artifact-manifest hash, current
ArtifactObservation, observation review provenance, corpus membership,
member corpus decision, aggregate batch decision, corpus review provenance, and
delivery eligibility. The response SHALL expose raw stored decisions separately
from effective layer-local acceptance:

- validation raw state is the current ArtifactObservation status;
- validation effective acceptance is true only for `passed`, or
  `inconclusive` with an allowed observation review scoped to build success;
- corpus raw state is the stored member decision;
- corpus effective acceptance is true only for `passed`, or
  `review_required` with an allowed corpus review;
- delivery eligibility also requires the selected batch aggregate decision to
  be `passed` and no non-overrideable rule failure.

Because a BuildAttempt may appear in more than one corpus batch over its
lifecycle, delivery eligibility SHALL be computed only for an explicit
`corpus_batch_id` selected by the caller or release workflow. If no batch is
selected, the detail API SHALL list available memberships and raw/effective
states, but SHALL NOT infer delivery eligibility from the latest-created,
latest-evaluated, or only visible batch.

Observation review and corpus review SHALL remain separate fields. A review in
one layer SHALL NOT imply acceptance in the other layer. Older observations and
older sibling attempts SHALL remain visible as history but SHALL NOT replace
the current bound observation used for governed success.

#### Scenario: Detail separates raw review from effective acceptance

- **GIVEN** a BuildAttempt has a current ArtifactObservation with
  `status = inconclusive`
- **AND** an allowed observation review exists for build success
- **AND** its corpus member decision is stored as `review_required` with an
  allowed corpus review
- **WHEN** `GET /api/build-attempts/{id}` is called
- **THEN** the response reports the raw observation status as `inconclusive`
- **AND** validation effective acceptance is true with review provenance
- **AND** the raw corpus member decision remains `review_required`
- **AND** corpus effective acceptance is true with separate corpus review
  provenance

#### Scenario: Member approval cannot override aggregate block

- **GIVEN** a BuildAttempt's member corpus decision has an allowed approval
- **AND** the selected batch aggregate decision is `blocked`
- **WHEN** the BuildAttempt detail API computes delivery eligibility
- **THEN** delivery eligibility is false
- **AND** the response includes the aggregate blocking reason

#### Scenario: Detail without selected batch does not infer eligibility

- **GIVEN** a BuildAttempt belongs to two corpus batches
- **WHEN** `GET /api/build-attempts/{id}` is called without a selected
  `corpus_batch_id`
- **THEN** the response lists both memberships and their raw/effective states
- **AND** production delivery eligibility is reported as not computed or false
- **AND** the API does not choose a batch by creation time or evaluation status
