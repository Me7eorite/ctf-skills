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

