## ADDED Requirements

### Requirement: Governance persistence is additive and versioned

The persistence layer SHALL add governance tables and nullable references
without requiring historical backfill. The schema SHALL include at least:

- `research_runs.trial_only`;
- `design_profile_reservations`;
- `design_profile_ledgers`;
- `design_evidence`;
- `artifact_observations`;
- corpus batch, membership, decision, match, observation-review,
  corpus-review, and history tables;
- nullable current references from `design_tasks`, `challenge_designs`, and
  `build_attempts` where required by the governance lifecycle.

Every versioned governance table SHALL preserve historical rows instead of
mutating audit-significant results in place. Current/live rows SHALL be
identified by explicit state or current-reference fields plus database
constraints.

`design_profile_reservations` SHALL include nullable `occupancy_scope` and
`exclusive_signature_key` columns. Active exclusive reservations SHALL be unique
by `(policy_version, occupancy_scope, exclusive_signature_key)` through a
partial unique index where both scoped fields are non-null and state is active.

`design_evidence` SHALL store supersession fields
`superseded_at`, `superseded_by_evidence_id`, and `supersession_reason`, and
SHALL enforce at most one unsuperseded row per DesignTask.

#### Scenario: Historical task loads without governance rows

- **GIVEN** a pre-change design task has no reservation, evidence, observation,
  or corpus rows
- **WHEN** the repository loads it
- **THEN** the task remains readable as legacy data
- **AND** new production build submission still requires the governed evidence
  path

### Requirement: Observation versions preserve validation history

The persistence layer SHALL store ArtifactObservations as versions per
BuildAttempt. It SHALL enforce `unique(build_attempt_id, observation_version)`
and at most one `is_current = true` observation per BuildAttempt.

Revalidation SHALL insert a new observation version and mark the prior current
observation `is_current = false` with `superseded_at`. It SHALL NOT overwrite
prior observed profile, contract-check, negative-test, or fingerprint results.

#### Scenario: Revalidation creates a new observation version

- **GIVEN** BuildAttempt A has current observation version 1
- **WHEN** revalidation runs and records a new observation
- **THEN** version 2 is inserted
- **AND** version 1 remains queryable as historical evidence
- **AND** version 2 becomes the current observation for BuildAttempt A

### Requirement: Research trial-only marker is queryable downstream

The persistence layer SHALL store whether a ResearchRun completed through an
explicit diversity soft-pass as `trial_only = true`. Downstream design evidence
and corpus governance SHALL be able to trace a candidate back to that source
ResearchRun.

The marker SHALL not be duplicated on GenerationRequest.

#### Scenario: Corpus admission can detect trial-only source research

- **GIVEN** a DesignEvidence row cites findings from ResearchRun R
- **AND** R has `trial_only = true`
- **WHEN** production corpus admission evaluates the candidate
- **THEN** it can block the candidate because its source research was trial-only
