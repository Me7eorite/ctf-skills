## MODIFIED Requirements

### Requirement: Generation requests, design tasks, and build attempts are deletable

Deletion cascades SHALL include governance rows that are scoped to deleted
mutable resources: DesignProfileReservations, DesignEvidence,
ArtifactObservations, corpus batch memberships, member/aggregate decisions,
matches, and unpublished/non-retired review rows whose whole scope is deleted.
The service SHALL delete or detach these rows in an order that preserves
foreign-key integrity and does not leave dangling current references on
DesignTask, ChallengeDesign, or BuildAttempt rows.

Published or retired corpus history entries are not ordinary mutable resource
rows. Normal generation-request, design-task, or build-attempt deletion SHALL
preserve the minimal corpus history projection needed for future duplicate
comparison, including the review provenance required to explain why a
`review_required` member or aggregate was accepted into a published/retired
release. Removing that projection or its retained review provenance requires a
separate explicit governance-history purge with an audit reason.

#### Scenario: Deleting an unpublished governed design task removes mutable governance rows

- **GIVEN** an unpublished governed Design Task has reservations,
  DesignEvidence, BuildAttempts, ArtifactObservations, and corpus batch
  membership rows
- **WHEN** an operator deletes the Design Task through normal resource deletion
- **THEN** the task's mutable governance rows are deleted or detached in the
  same transaction as the task cascade
- **AND** no current governance reference points to a deleted row after commit

#### Scenario: Published corpus history survives normal deletion

- **GIVEN** a published challenge has a minimal corpus history entry
- **WHEN** an operator deletes its mutable request, task, build rows, or
  artifacts through normal resource deletion
- **THEN** the corpus history entry remains available for duplicate comparison
- **AND** the review provenance needed to justify the published/retired
  decision remains available
- **AND** the deletion response reports that governance history was retained

### Requirement: Delete endpoints expose consistent contracts

Deletion responses SHALL report governance cleanup separately from artifact
cleanup. The response SHALL include governance rows deleted or retained, and it
SHALL identify retained corpus history entries and retained review provenance
as `retained_governance_history` rather than as skipped artifact paths.

#### Scenario: Delete response names retained governance history

- **GIVEN** normal deletion affects a published challenge with corpus history
- **WHEN** the delete endpoint succeeds
- **THEN** the response includes the retained corpus history identifier
- **AND** the response includes any retained review provenance identifiers
- **AND** the response does not imply the retained history is an undeleted
  mutable task/build row
