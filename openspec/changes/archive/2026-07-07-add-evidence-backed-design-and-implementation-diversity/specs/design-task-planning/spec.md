## ADDED Requirements

### Requirement: Every new DesignTask reserves a governed design profile

The planning subsystem SHALL persist one `design_profile_reservations` row
before a new DesignTask can enter Design execution. The reserved
profile SHALL contain the four axes `semantic`, `solve`, `implementation`, and
`presentation`, and SHALL validate against the closed vocabulary and
compatibility rules for the task category.

Reservation allocation SHALL consider the task's research assignment, sibling
reserved/committed profiles, committed historical design evidence, and the
category policy in `generation-profiles.json`. Allocation and signature
generation SHALL be deterministic for the same ordered inputs and policy.

Hard occupancy SHALL include active reservations, live committed evidence, and
published historical evidence. Superseded, rejected, or `design_unbuildable`
evidence SHALL remain available as similarity context but SHALL NOT consume a
hard quota or exclusive-signature slot.

All reservations for one request SHALL be written atomically under the
parent-request lock. Cross-request allocation SHALL additionally serialize on
a category-scoped Design Profile Ledger row with a monotonic `ledger_version`.
Hard-exclusive active signatures SHALL use a policy-derived nullable
`occupancy_scope` plus `exclusive_signature_key`: exclusive active rows store
both values and are covered by a partial unique index over
`(policy_version, occupancy_scope, exclusive_signature_key)`, while
non-exclusive rows store NULL for both. Policy code SHALL compute the scope and
key from normalized profile dimensions; the database constraint SHALL NOT
interpret JSON policy. A conflict SHALL retry allocation from a fresh ledger
snapshot.

Reservations SHALL be versioned with
`unique(design_task_id, reservation_version)` and a partial unique constraint
allowing at most one active (`reserved|committed`) row per task. If no profile
can satisfy a hard constraint, generation SHALL fail with
`design_diversity_exhausted` and SHALL NOT persist a partial task/reservation
set. It SHALL NOT silently replace a required format, language/runtime, solve
action, or concealment mechanism with a generic default.

#### Scenario: Concurrent Design workers receive distinct reserved space

- **GIVEN** two sibling tasks whose applicable policy forbids an identical
  combined governed signature
- **WHEN** their reservations are allocated and both Design workers start
- **THEN** each worker receives its own persisted reservation
- **AND** the two combined signatures are different

#### Scenario: Exhausted profile space aborts atomically

- **GIVEN** a request whose remaining compatible profiles all violate a hard
  uniqueness rule
- **WHEN** planning attempts to create the request's tasks and reservations
- **THEN** it returns `design_diversity_exhausted` with exhausted dimensions
- **AND** no partial new task or reservation rows remain

#### Scenario: Different requests cannot race the same exclusive profile

- **GIVEN** two generation requests concurrently allocate the last available
  hard-exclusive signature in one category
- **WHEN** both transactions attempt reservation
- **THEN** the category ledger and scoped unique key allow at most one to commit
- **AND** the loser retries from the incremented ledger version or fails with
  `design_diversity_exhausted`

### Requirement: Reservation lifecycle follows task regeneration

A reservation SHALL have state `reserved`, `committed`, or `released`.
Successful Design evidence commits the reservation. A Design retry against the
same unchanged task SHALL retain it. Regenerate-one/regenerate-all SHALL release
the affected reservation, supersede any live evidence, allocate a new
reservation under the parent lock, and clear the affected task's
`plan_reviewed_at`.

Released reservations SHALL not consume current-batch quota. Committed and
superseded evidence SHALL remain available as historical ledger context.
Creating a fresh reservation SHALL increment `reservation_version` rather than
overwriting or violating the prior row.

#### Scenario: Regeneration cannot retain stale approval or profile

- **GIVEN** an approved draft task with a reserved profile
- **WHEN** the task is successfully regenerated
- **THEN** the old reservation is released
- **AND** a fresh reservation is attached
- **AND** `plan_reviewed_at` is cleared

### Requirement: Design task detail exposes the current governance chain

The Design Task detail API SHALL expose governance fields from the server-owned
current chain: `current_reservation`, `current_design_evidence`,
`quality_gate_passed`, `build_contract_summary`, and `build_eligibility`.
`build_eligibility` SHALL include a boolean plus machine-readable blocking
reasons such as `missing_design_evidence`, `design_quality_gate_failed`,
`reservation_not_committed`, and `build_contract_incomplete`.

Superseded DesignEvidence, released reservations, failed-quality designs, and
prior BuildAttempts SHALL be exposed only in bounded history arrays or links.
They SHALL NOT be folded into the current chain and SHALL NOT cause
`build_eligibility.eligible = true`.

The dashboard SHALL render current governance state separately from historical
state. It SHALL display server-provided eligibility and reasons and SHALL NOT
derive profile equality, contract completeness, or build admission client-side.

#### Scenario: Failed-quality evidence is inspectable but not build-eligible

- **GIVEN** a task has current DesignEvidence whose ChallengeDesign has
  `quality_gate_passed = false`
- **WHEN** `GET /api/design-tasks/{id}` is called
- **THEN** the response includes the current DesignEvidence for inspection
- **AND** `build_eligibility.eligible` is false
- **AND** the blocking reasons include `design_quality_gate_failed`

#### Scenario: Superseded evidence remains history only

- **GIVEN** a task was revised from evidence E1 to evidence E2
- **WHEN** the Design Task detail API returns governance state
- **THEN** E2 is returned as `current_design_evidence`
- **AND** E1 appears only in a history collection or linked history endpoint
- **AND** no Build action uses E1 as the current contract source

## MODIFIED Requirements

### Requirement: Design task status supports planning before execution

The system SHALL keep the existing DesignTask status values unchanged. Existing planning,
Design worker, and Build ownership rules remain in force, with one explicit
service-owned revision path added by this change:

- `designed -> draft`;
- `build_failed -> draft`;
- `built -> draft` only when the current built version has not been included in
  a released production corpus batch.

These transitions SHALL be performed only by the Design revision service under
the task/request locks. They are not general planning transition endpoints.
Revision supersedes the live DesignEvidence, attaches a fresh reservation,
clears `plan_reviewed_at`, and therefore requires the normal
`draft -> queued` approval gate before another Design attempt.

BuildAttempts bound to prior DesignEvidence versions remain immutable history
and cannot roll the revised parent status forward.

#### Scenario: Revision uses an explicit service transition

- **GIVEN** an unpublished built task with no active BuildAttempt
- **WHEN** the Design revision service succeeds
- **THEN** its status becomes `draft`
- **AND** ordinary planning endpoints still cannot transition arbitrary built
  tasks to draft

#### Scenario: Released built task cannot return to draft

- **GIVEN** the current built version belongs to a released production batch
- **WHEN** Design revision is requested
- **THEN** the status remains `built`
- **AND** a new DesignTask/version is required
