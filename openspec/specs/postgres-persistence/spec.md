# postgres-persistence Specification

## Purpose
TBD - created by archiving change add-postgres-persistence. Update Purpose after archive.
## Requirements
### Requirement: PostgreSQL is the only supported persistence backend

The system SHALL connect to PostgreSQL using a `DATABASE_URL` of the form `postgresql+psycopg://<user>:<password>@<host>:<port>/<database>`. The system SHALL NOT construct an SQLite engine, in-memory engine, or any other backend as a fallback when `DATABASE_URL` is missing, malformed, or unreachable.

#### Scenario: Missing DATABASE_URL is fatal

- **WHEN** the environment has no `DATABASE_URL` value and `persistence.create_engine_from_env()` is called
- **THEN** it raises `PersistenceConfigurationError`
- **AND** no engine, session factory, or connection is created

#### Scenario: Non-Postgres scheme is rejected

- **WHEN** `DATABASE_URL=sqlite:///work/state.sqlite3` is set and `persistence.create_engine_from_env()` is called
- **THEN** it raises `PersistenceConfigurationError` whose message names the rejected scheme

#### Scenario: Unreachable database surfaces a connection error

- **WHEN** a syntactically valid `DATABASE_URL` points at a host that refuses connections and the application performs its first session checkout
- **THEN** the call raises `PersistenceConnectionError` with the underlying `psycopg` exception chained as `__cause__`

### Requirement: Session lifecycle is bounded by an explicit transaction

The system SHALL expose a `transaction()` context manager from `persistence.session` that yields a SQLAlchemy `Session`, commits on successful exit, and rolls back on any exception while re-raising the original exception unchanged.

#### Scenario: Successful block commits

- **WHEN** code runs to completion inside `with transaction() as session:`
- **THEN** the transaction is committed
- **AND** the session is closed

#### Scenario: Exception inside block rolls back

- **WHEN** code inside `with transaction() as session:` raises `ValueError`
- **THEN** the transaction is rolled back
- **AND** `ValueError` propagates to the caller unchanged
- **AND** the session is closed

### Requirement: Alembic drives all schema changes

The system SHALL manage every PostgreSQL schema change through Alembic revisions checked into `alembic/versions/`. A fresh, empty database SHALL upgrade to `head` and downgrade back to `base` without manual intervention. Schema changes SHALL NOT be applied via runtime `CREATE TABLE`, ORM metadata `create_all`, or ad-hoc SQL.

#### Scenario: Empty database upgrades to head

- **WHEN** `alembic upgrade head` runs against an empty PostgreSQL database
- **THEN** the command exits with status 0
- **AND** `alembic current` reports the baseline revision id

#### Scenario: Head downgrades back to base

- **WHEN** `alembic downgrade base` runs against a database currently at `head`
- **THEN** the command exits with status 0
- **AND** no application tables remain in the public schema

### Requirement: Persistence package boundary

The `persistence` package SHALL expose `create_engine_from_env`, `SessionFactory`, `transaction`, `PersistenceConfigurationError`, and `PersistenceConnectionError` through `persistence/__init__.py`. The package SHALL NOT import from `web`, `hermes`, `packing`, or `cli`. `hermes` SHALL NOT import from `persistence`.

#### Scenario: Public API import succeeds

- **WHEN** running `python -c "from persistence import create_engine_from_env, SessionFactory, transaction, PersistenceConfigurationError, PersistenceConnectionError"`
- **THEN** the command exits with status 0 and no import error

#### Scenario: Hermes cannot import persistence

- **WHEN** a hypothetical `src/hermes/runner.py` contains `from persistence import transaction` and the dependency direction test runs
- **THEN** the test fails and the diagnostic identifies `hermes -> persistence` as the forbidden edge

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

Published or retired release provenance SHALL survive normal operational
deletion even when mutable request/task/build rows are removed. Persistence
SHALL retain or project the minimal `corpus_history_entries`, accepted member or
aggregate review provenance, and blocking/acceptance reasons needed to explain
why the released/retired corpus was admitted. Foreign-key cascades SHALL NOT be
the only copy of that release audit trail; if mutable rows are deleted, the
service must detach, project, or otherwise preserve the retained governance
history before commit.

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

