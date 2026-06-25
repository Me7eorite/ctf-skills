## ADDED Requirements

### Requirement: Difficulty review results are persisted append-only

The system SHALL persist each pre-build difficulty review in PostgreSQL as an
append-only `design_difficulty_reviews` row linked to both `design_tasks.id`
and `challenge_designs.id`.

Each row SHALL store whether the review passed, the claimed and actual
difficulty, confidence, reasons, detected risks, required revisions, reviewer
identifier, and creation timestamp.

#### Scenario: Failed review remains available for diagnostics

- **GIVEN** a pre-build difficulty review fails for a design task
- **WHEN** Build submission is rejected
- **THEN** a `design_difficulty_reviews` row remains available for that design
  task
- **AND** the row includes at least one reason and at least one required
  revision

#### Scenario: Repeated reviews append rows

- **GIVEN** a design task has already been reviewed before Build submission
- **WHEN** the same or updated latest design is reviewed again
- **THEN** the system inserts another `design_difficulty_reviews` row
- **AND** the previous review row is not overwritten

### Requirement: Design task reads expose review diagnostics

The system SHALL expose per-design-task difficulty review diagnostics so batch
operators can distinguish active Design retries from repeated pre-build review
failures.

The diagnostics SHALL include total review count, failed review count, and the
latest review result with reasons and required revisions when present.

#### Scenario: Design task detail includes latest review

- **GIVEN** a design task has at least one pre-build difficulty review
- **WHEN** the operator reads the design task detail
- **THEN** the response includes `difficulty_review_summary.total`
- **AND** the response includes `difficulty_review_summary.failed`
- **AND** the response includes the latest review's reasons and required
  revisions

#### Scenario: Design task list includes review failure count

- **GIVEN** a batch contains tasks that failed pre-build difficulty review
- **WHEN** the operator lists design tasks for that batch
- **THEN** each row includes `difficulty_review_summary.failed`
- **AND** the operator can identify tasks repeatedly returning from Build review
  to Design retry
