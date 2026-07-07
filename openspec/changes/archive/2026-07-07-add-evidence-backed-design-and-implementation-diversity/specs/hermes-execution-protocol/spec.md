## MODIFIED Requirements

### Requirement: Runner owns validate execution and validate events

Hermes SHALL generate validation artifacts but SHALL NOT own host validation or
validate progress events. For non-dry-run execution the runner SHALL verify
design, implement, build, and document prerequisites, then invoke host-owned
validation unless a resume carry-forward is valid under this requirement.

For a governed BuildAttempt, host-owned validation SHALL be contract-aware and
SHALL produce a validation-layer ArtifactObservation bound to the exact
BuildAttempt, DesignEvidence, canonical contract hash, and artifact-manifest
hash before the runner writes a successful `validate/*` or `complete/*`
terminal event. Existing artifact, reference-solve, and flag checks remain
mandatory.

For the validation layer, an observation is effectively accepted only when:

- its status is `passed`; or
- its status is `inconclusive` and an allowed observation review decision exists
  for that exact observation.

An observation with `failed`, stale binding/hash, or no allowed review for
`inconclusive` SHALL produce validation failure. Validation-layer acceptance
does not authorize corpus publication by itself.

Carry-forward `validate/passed` remains available for legacy challenges. For a
governed BuildAttempt it is allowed only when the prior effectively accepted
observation still matches current BuildAttempt, DesignEvidence, contract hash,
and artifact-manifest hash. Therefore the prior general rule that skipped
validate is never re-executed has a governed-build exception: stale or missing
observation evidence forces contract-aware validation again.

The same host-owned behavior applies to build-attempt revalidation without
invoking Hermes.

#### Scenario: Matching passed observation permits completion

- **GIVEN** contract-aware validation creates a passed observation whose
  BuildAttempt, evidence, contract hash, and artifact hash match current state
- **WHEN** runner validation completes
- **THEN** it writes validate/passed and may write complete/passed

#### Scenario: All-skipped resume revalidates after artifact change

- **GIVEN** all authoring stages have valid carry-forward evidence
- **AND** the current artifact manifest differs from the prior observation
- **WHEN** resume evaluates the governed challenge
- **THEN** it does not short-circuit with `skipped_resume`
- **AND** contract-aware validation runs again

#### Scenario: Legacy skipped validation remains compatible

- **GIVEN** a grandfathered challenge has no governed DesignEvidence
- **AND** its legacy resume prefix validly skips validation
- **WHEN** resume runs
- **THEN** existing carry-forward behavior remains unchanged

### Requirement: Timeout recovery cannot bypass validation

When Hermes times out, the runner SHALL re-evaluate current-window events and
deterministic evidence for design, implement, build, and document. It SHALL NOT
treat metadata build/solve status alone as complete and SHALL NOT synthesize
missing stage events.

If prerequisites are complete, timeout recovery SHALL continue into mandatory
host validation. A governed BuildAttempt may skip re-execution only when an
effectively accepted ArtifactObservation still matches the current
BuildAttempt, DesignEvidence, contract hash, and artifact-manifest hash.
Otherwise contract-aware validation SHALL run before final done/failed status.

#### Scenario: Timeout with matching observation may carry forward

- **GIVEN** all prerequisites are complete
- **AND** a matching effectively accepted observation already exists
- **WHEN** timeout recovery runs
- **THEN** it may carry forward validation success

#### Scenario: Timeout with stale observation validates again

- **GIVEN** all prerequisites are complete
- **AND** the prior observation's artifact hash is stale
- **WHEN** timeout recovery runs
- **THEN** contract-aware validation executes
- **AND** final status depends on its result

## ADDED Requirements

### Requirement: Reconciliation requires an accepted observation

For governed BuildAttempts, BuildReconciler SHALL transition an attributed row
to `succeeded` only when existing queue/artifact/solve requirements pass and the
row has an effectively accepted ArtifactObservation with matching
DesignEvidence, contract hash, and artifact-manifest hash.

`metadata.solve_status = passed` alone SHALL not produce success. Legacy
BuildAttempts without governed evidence retain their grandfathered
reconciliation behavior.

#### Scenario: Passed metadata without observation is insufficient

- **GIVEN** a governed shard is done and metadata says `solve_status = passed`
- **AND** no matching accepted ArtifactObservation exists
- **WHEN** reconciliation runs
- **THEN** the BuildAttempt does not become succeeded
- **AND** its failure reason identifies missing or stale observation evidence
