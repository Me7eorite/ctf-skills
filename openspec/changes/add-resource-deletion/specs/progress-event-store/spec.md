## MODIFIED Requirements

### Requirement: ProgressStore protocol is the only progress write/read API

The system SHALL expose a `ProgressStore` protocol in `core/state.py` that
defines the complete contract for recording, querying, and lifecycle-purging
shard progress events. All progress writes, reads, and purges inside the
application — `progress` CLI, `HermesRunner`, `DashboardService`, resume
planning, duration metrics, and resource deletion — SHALL go through a
`ProgressStore` instance. Non-composition consumers SHALL NOT import a concrete
progress store implementation directly; concrete implementations SHALL be
selected only at composition roots and injected into consumers.

The system SHALL define a core-owned `ProgressEventInput` DTO in
`core/state.py` for `record_batch` inputs. It SHALL NOT reuse the SQLAlchemy
`persistence.models.progress.ProgressEvent` ORM class as the protocol input
type.

`ProgressEventInput` SHALL expose exactly these fields, matching the keyword
arguments of `record`:

| Field | Type | Default |
| --- | --- | --- |
| `shard` | `str` | required |
| `stage` | `str` (one of `STAGES`) | required |
| `status` | `str` (one of `STATUSES`) | required |
| `challenge_id` | `str` | `""` (shard-level event when empty) |
| `worker` | `str` | `""` |
| `message` | `str` | `""` |

The DTO SHALL NOT carry a `percent` or `created_at` field; percent is derived
by `_percent(stage, status)` at write time and stored in the row, and
`created_at` is set server-side by PostgreSQL or by the in-memory clock helper.

The protocol SHALL expose exactly these methods:

- `record(*, shard, stage, status, challenge_id="", worker="", message="") -> dict`
- `record_batch(events: Sequence[ProgressEventInput]) -> list[dict]`
- `events_for_shard(shard, *, before_id=None) -> list[dict]`
- `events_for_challenge(shard, challenge_id, *, after_id=None, before_id=None) -> list[dict]`
- `latest_claim_event(shard, *, before_id=None) -> dict | None`
- `reset_snapshots(shard) -> None`
- `purge_shards(shards: Collection[str], *, transaction: object | None = None) -> None`
- `dashboard(event_limit: int = 60) -> dict`

`purge_shards` SHALL atomically remove both events and snapshots for every
supplied shard. The optional transaction is an opaque caller-owned context:
the PostgreSQL implementation SHALL join it rather than commit independently.
The core protocol SHALL NOT import SQLAlchemy or expose a SQLAlchemy type.

#### Scenario: All consumers receive ProgressStore via injection

- **WHEN** the dependency direction test parses `src/hermes/`, `src/domain/`,
  `src/services/`, and `src/core/state.py`
- **THEN** none of those files directly delete progress ORM rows
- **AND** runtime consumers receive an injected `ProgressStore`

#### Scenario: Protocol covers every progress operation

- **WHEN** application code records, queries, resets, or lifecycle-purges progress
- **THEN** it calls one of the eight `ProgressStore` methods
- **AND** no consumer bypasses the protocol with direct progress-table SQL

#### Scenario: Caller transaction is opaque to core

- **WHEN** static dependency tests inspect `core/state.py`
- **THEN** no SQLAlchemy or persistence import exists
- **AND** `purge_shards` accepts its optional transaction as an opaque object

### Requirement: PostgresProgressStore is the production implementation

The system SHALL provide `PostgresProgressStore` in
`persistence/repositories/progress.py` implementing the `ProgressStore`
protocol against PostgreSQL using the project's existing `SessionFactory`.
Except when `purge_shards` receives a caller transaction, each public method
SHALL open one short transaction, commit on success, and roll back on
exception. `record_batch` SHALL commit all events in a single transaction;
partial commits are forbidden. Transaction-aware `purge_shards` SHALL execute
inside the supplied active SQLAlchemy session/transaction and SHALL neither
commit nor roll it back itself.

Connection failures SHALL surface as
`PersistenceConnectionError` (already defined under `persistence.errors`),
NOT as silent no-ops. The implementation SHALL NOT construct or fall back
to a SQLite engine, in-memory store, or any other backend under any
condition.

#### Scenario: Record batch is atomic

- **WHEN** `record_batch([e1, e2, e3])` is called and the third row would
  violate a CHECK constraint
- **THEN** none of the three events appear in `progress_events`
- **AND** the snapshot row for the affected `(shard, challenge_id)` pair
  reflects no state from this batch

#### Scenario: Postgres unreachable is fail-loud

- **WHEN** `PostgresProgressStore.record(...)` is called with PostgreSQL
  unreachable
- **THEN** the method raises `PersistenceConnectionError`
- **AND** no temp-directory SQLite file is created

#### Scenario: Purge joins resource deletion transaction

- **WHEN** `purge_shards([S], transaction=session)` runs and the caller later rolls back
- **THEN** events and snapshots for S remain present after rollback
- **AND** the progress store does not commit independently

#### Scenario: Standalone purge is atomic

- **WHEN** `purge_shards([A, B])` is called without a transaction
- **THEN** events and snapshots for A and B are removed in one short transaction
- **AND** failure removes none of them

### Requirement: InMemoryProgressStore is the test double

The system SHALL provide `InMemoryProgressStore` in `core/state.py`
implementing the full `ProgressStore` protocol against in-process Python data
structures. The double SHALL preserve event id monotonicity, event ordering,
snapshot upsert semantics, the no-regression rule, and atomic shard purge so
unit tests produce the same observable outcomes as PostgreSQL. It SHALL accept
and ignore the optional opaque transaction argument to `purge_shards`.

Tests for `HermesRunner`, resume planning, duration metrics, dashboard data
assembly, the `progress` CLI handler, and pure deletion coordination SHALL use
`InMemoryProgressStore` by default and SHALL NOT require PostgreSQL.

#### Scenario: In-memory double satisfies the protocol

- **WHEN** the test suite runs without `TEST_DATABASE_URL` set
- **THEN** all protocol consumer tests pass against `InMemoryProgressStore()`
- **AND** no test in this set imports from `persistence/`

#### Scenario: In-memory double honors no-regression rule

- **WHEN** a snapshot exists at `(stage=document, status=passed)` and a caller
  records `(stage=design, status=running)` for the same key
- **THEN** the snapshot keeps `stage=document` and `status=passed`
- **AND** the returned event records the lower-progress event

#### Scenario: In-memory purge removes events and snapshots

- **WHEN** `purge_shards([S])` is called after multiple challenge and shard-level events for S
- **THEN** all events and snapshots for S are removed atomically
- **AND** progress for other shard keys remains unchanged
