## ADDED Requirements

### Requirement: Build submission records pre-build difficulty review

Before a design task can be submitted to Build, the system SHALL run a
pre-build difficulty review against the latest draft challenge design and the
parent design task difficulty.

The review SHALL evaluate the design without rewriting it. The review result
SHALL include `pass`, `claimed_difficulty`, `actual_difficulty`, `confidence`,
`reasons`, `detected_risks`, and `required_revision`.

#### Scenario: Passing review is recorded before build attempt creation

- **GIVEN** a design task in `designed` status
- **AND** its latest draft design satisfies the deterministic difficulty and
  asset-flow rubric
- **WHEN** the operator submits the design task for Build
- **THEN** the system records a passed difficulty review
- **AND** the system creates the build attempt and pending shard normally

#### Scenario: Failed review blocks Build submission

- **GIVEN** a design task in `designed` status
- **AND** its latest draft design does not satisfy the claimed difficulty
- **WHEN** the operator submits the design task for Build
- **THEN** the system records a failed difficulty review
- **AND** the system rejects the submission with `difficulty_review_failed`
- **AND** the current draft challenge design is marked `superseded`
- **AND** the latest design attempt stores the review reasons and required
  revisions as retry feedback
- **AND** the design task is returned to `queued` when another design attempt is
  available
- **AND** no build attempt is created
- **AND** no pending shard is written

#### Scenario: Failed review exhausts design attempts

- **GIVEN** a design task in `designed` status
- **AND** its latest draft design does not satisfy the claimed difficulty
- **AND** the latest design attempt already equals the generation request's
  maximum attempts
- **WHEN** the operator submits the design task for Build
- **THEN** the system records a failed difficulty review
- **AND** the current draft challenge design is marked `superseded`
- **AND** the design task is marked `failed`
- **AND** no build attempt is created
- **AND** no pending shard is written

#### Scenario: Review does not rewrite design payload

- **GIVEN** a design task whose latest draft design fails pre-build review
- **WHEN** the system records the failed review
- **THEN** the challenge design payload remains unchanged
- **AND** the required revisions are exposed only as review diagnostics
