## MODIFIED Requirements

### Requirement: Passed challenges are selected for delivery

In production mode the packer SHALL include a challenge only when all of the
following are true:

- existing build metadata indicates a passed build;
- the corpus membership's immutable BuildAttempt has an effectively accepted
  ArtifactObservation (`status = passed`, or `status = inconclusive` plus a
  valid allowed observation review);
- the challenge belongs to the explicitly requested corpus-admission batch;
- its member decision is corpus-accepted (`passed`, or `review_required`
  with a valid recorded corpus approval);
- the aggregate batch decision is `passed` after the corpus service accounts
  for allowed member reviews without rewriting raw member decisions;
- no non-overrideable corpus rule failed.

Observation review and corpus review are independent. Passing one SHALL NOT
implicitly approve the other. In this spec, the validation layer uses
ArtifactObservation acceptance, while the corpus layer uses corpus-accepted
member decisions.

Production packing SHALL require an explicit `corpus_batch_id` argument and
database access to resolve immutable membership/decision records. It SHALL not
infer a batch from filesystem order, metadata, or latest-created timestamps.

`metadata.build_status = passed` alone SHALL not make a challenge eligible for
a production bundle.

The packer MAY expose explicit `shadow` and `trial` modes. Such outputs SHALL be
marked non-production in their summary/inventory and SHALL not overwrite the
default production bundle without an explicit output path. Shadow/trial packing
SHALL NOT satisfy or publish through the production release gate.

#### Scenario: Individually passed duplicate is excluded

- **GIVEN** a challenge with passed build/solve metadata
- **AND** its corpus decision is blocked as an exact governed duplicate
- **WHEN** production packing runs
- **THEN** the challenge is excluded and the pack operation reports the corpus
  block

#### Scenario: Reviewed borderline similarity may be packed

- **GIVEN** a challenge whose corpus decision is `review_required`
- **AND** an authorized operator recorded an allowed approval with reason and
  timestamp
- **AND** the selected corpus batch's aggregate decision is `passed` after
  accounting for that allowed review
- **WHEN** production packing runs
- **THEN** the challenge is eligible if every other delivery requirement passes

#### Scenario: Member review without aggregate pass is not enough

- **GIVEN** a challenge whose member corpus decision has an allowed approval
- **AND** the selected corpus batch aggregate decision is still
  `review_required` or `blocked`
- **WHEN** production packing runs
- **THEN** the challenge is excluded
- **AND** the pack operation reports the aggregate corpus decision

#### Scenario: Trial bundle is visibly non-production

- **WHEN** the packer runs in explicit trial mode
- **THEN** its summary and inventories identify the bundle as non-production
- **AND** the default production bundle is not silently replaced
