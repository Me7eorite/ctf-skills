# challenge-corpus-governance Specification

## Purpose
TBD - created by archiving change add-evidence-backed-design-and-implementation-diversity. Update Purpose after archive.
## Requirements
### Requirement: Corpus fingerprints compare batch and historical challenges

The system SHALL generate and persist canonical fingerprints for each observed
challenge:

- semantic profile;
- solve profile;
- implementation profile;
- combined governed profile;
- normalized source tokens;
- normalized solver tokens;
- intended path.

The corpus service SHALL compare candidates with all other challenges in the
candidate production batch, live committed governed evidence, and
published/retired `corpus_history_entries` selected through a bounded indexed
shortlist. It SHALL store each material match's live challenge ID or history
entry ID, fingerprint type, score, threshold, and decision reason.

Corpus persistence SHALL include:

- `corpus_batches` with mode, category scope, policy version, status, creator,
  and timestamps;
- immutable `corpus_batch_members` binding BuildAttempt, DesignEvidence,
  ArtifactObservation, and fingerprint versions;
- member and aggregate `corpus_decisions`;
- pairwise `corpus_matches` that can reference either live batch/history
  candidates or retained published/retired history entries;
- separate `observation_review_decisions` and `corpus_review_decisions`;
- append-only `corpus_history_entries` containing the minimal governed
  signatures/fingerprints needed to detect recurrence after operational
  challenge deletion.

Membership SHALL become immutable when evaluation starts. Rebuilt/revised
challenges require a new membership. Packing SHALL name one batch explicitly;
there is no implicit current batch.

Published/retired corpus history entries SHALL survive normal resource
deletion. Removing them requires a separate explicit governance-history purge
with audit reason. Full source, solver, logs, and artifacts need not be retained
by that projection.

#### Scenario: Rename-only clone is still detected

- **GIVEN** two challenges differ only in title, flag, identifiers, and numeric
  constants
- **WHEN** normalized source/solver fingerprints are compared
- **THEN** those superficial values do not prevent a high-similarity match

#### Scenario: Published fingerprint survives operational deletion

- **GIVEN** a published challenge has a corpus history entry
- **WHEN** its mutable task/build rows and artifacts are deleted through normal
  resource deletion
- **THEN** the minimal corpus history entry remains available for duplicate
  comparison

### Requirement: Corpus gate returns member and aggregate decisions

The corpus gate SHALL return one decision per member plus one aggregate batch
decision. The aggregate batch decision SHALL be exactly one of `passed`,
`review_required`, or `blocked`.

Default hard blocks:

- identical combined governed profile in batch or history, except for an
  explicit same-task revision lineage whose current candidate and prior
  version share the same DesignTask lineage identifier and superseded/live
  evidence chain;
- repeated sub-technique only when solve and implementation signatures also
  match in one production batch;
- source token Jaccard at or above `0.65`;
- solver token Jaccard at or above `0.75`;
- profile quota violation;
- failed ArtifactObservation.

Default review thresholds:

- source token Jaccard at or above `0.45`;
- solver token Jaccard at or above `0.55`;
- an inconclusive ArtifactObservation with an allowed observation review.

Thresholds and quotas MAY be configured per category. Production publication
requires every selected member to be corpus-accepted and the aggregate batch
decision to be `passed`. A member decision is corpus-accepted when it is
`passed`, or `review_required` with an explicit operator approval. Corpus
approval SHALL record actor, reason, and timestamp and SHALL NOT rewrite the
stored `review_required` member decision. Observation review and corpus review
SHALL be separate records. Exact combined-profile duplicates outside the
same-task revision lineage, failed observations, and hard profile mismatches
SHALL not be overrideable.

An inconclusive ArtifactObservation without an allowed observation review SHALL
block corpus admission.

The service SHALL compute both a decision for each member and one aggregate
batch decision. The aggregate decision SHALL be computed from effective member
states while preserving raw member decisions. A member with stored `passed` is
effective-accepted. A member with stored `review_required` is
effective-accepted only when an allowed corpus review exists for that member.
A member with stored `blocked` or any non-overrideable hard rule is never
effective-accepted.

The aggregate decision SHALL be `blocked` if any member is blocked or any
non-overrideable hard rule failed, `review_required` if at least one
overrideable review remains unapproved, and `passed` only when every member is
effective-accepted. An allowed member review does not rewrite the original
`review_required` decision; the aggregator records the review provenance used
to compute the aggregate pass.

#### Scenario: Approved member review can produce aggregate pass

- **GIVEN** a production batch has one member stored as `review_required`
- **AND** an authorized operator recorded an allowed corpus review for that
  member
- **AND** every other member is stored as `passed`
- **WHEN** the aggregate batch decision is computed
- **THEN** the aggregate decision is `passed`
- **AND** the reviewed member's stored decision remains `review_required`

#### Scenario: Unapproved member review keeps aggregate in review

- **GIVEN** a production batch has one member stored as `review_required`
- **AND** no allowed corpus review exists for that member
- **WHEN** the aggregate batch decision is computed
- **THEN** the aggregate decision is `review_required`
- **AND** production packing is not eligible

#### Scenario: Exact governed duplicate is blocked

- **GIVEN** a candidate matches an existing committed challenge on semantic,
  solve, and implementation profiles
- **WHEN** corpus admission runs
- **THEN** the decision is `blocked`
- **AND** no operator override can publish it

#### Scenario: Same-task revision lineage is not treated as a duplicate

- **GIVEN** a candidate is a revision of the same DesignTask lineage and its
  prior design/evidence has been superseded
- **WHEN** corpus admission runs
- **THEN** the prior version does not block the candidate as a duplicate
- **AND** the candidate is still evaluated against all other corpus rules

#### Scenario: Borderline source similarity requires review

- **GIVEN** source similarity is `0.52` and no hard rule fails
- **WHEN** corpus admission runs with default thresholds
- **THEN** the decision is `review_required`
- **AND** the matched challenge and score are exposed to the operator

### Requirement: Production and trial modes are explicit

Corpus governance SHALL support `shadow`, `trial`, and `production` modes.

- `shadow`: records decisions but does not block build or explicit
  non-production trial/shadow outputs; it cannot satisfy or publish through the
  production release gate.
- `trial`: blocks failed validation and exact duplicates, while other review
  findings require operator acknowledgment.
- `production`: enforces every configured block and review rule.

A research run marked trial-only due to diversity soft-pass SHALL never receive
a production `passed` decision.

#### Scenario: Trial-only evidence cannot be released as production

- **GIVEN** a candidate derived from a research run marked trial-only
- **WHEN** production corpus admission runs
- **THEN** it is blocked with a reason identifying research diversity soft-pass

