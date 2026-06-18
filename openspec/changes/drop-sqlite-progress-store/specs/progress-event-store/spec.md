## ADDED Requirements

### Requirement: ProgressStore protocol is the only progress write/read API

The system SHALL expose a `ProgressStore` protocol in `core/state.py` that
defines the complete contract for recording and querying shard progress
events. All progress writes and reads inside the application — `progress`
CLI, `HermesRunner`, `DashboardService`, resume planning, duration
metrics — SHALL go through a `ProgressStore` instance. No call site SHALL
import a concrete progress store implementation directly; concrete
implementations SHALL be selected at the composition root (`cli.py`,
`web/server.py`) and injected into consumers.

The protocol SHALL expose exactly these methods:

- `record(*, shard, stage, status, challenge_id="", worker="", message="") -> dict`
- `record_batch(events: Sequence[ProgressEvent]) -> list[dict]`
- `events_for_shard(shard, *, before_id=None) -> list[dict]`
- `events_for_challenge(shard, challenge_id, *, after_id=None, before_id=None) -> list[dict]`
- `latest_claim_event(shard, *, before_id=None) -> dict | None`
- `reset_snapshots(shard) -> None`
- `dashboard(event_limit: int = 60) -> dict`

#### Scenario: All consumers receive ProgressStore via injection

- **WHEN** the dependency direction test parses `src/hermes/`, `src/domain/`,
  and `src/core/state.py`
- **THEN** none of those files import `persistence.repositories.progress`
- **AND** `HermesRunner.__init__` accepts a `progress: ProgressStore`
  parameter that callers (CLI, web server) populate with a concrete
  implementation

#### Scenario: Protocol covers every legacy StateStore use site

- **WHEN** the codebase is grepped for the legacy class name `StateStore`
- **THEN** no occurrence remains
- **AND** every former `StateStore` call site now calls one of the seven
  protocol methods on an injected `ProgressStore`

### Requirement: PostgresProgressStore is the production implementation

The system SHALL provide `PostgresProgressStore` in
`persistence/repositories/progress.py` implementing the `ProgressStore`
protocol against PostgreSQL using the project's existing
`SessionFactory`. The implementation SHALL use short-lived transactions:
each public method opens one transaction, commits on success, and rolls
back on exception. `record_batch` SHALL commit all events in a single
transaction; partial commits are forbidden.

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

### Requirement: InMemoryProgressStore is the test double

The system SHALL provide `InMemoryProgressStore` in `core/state.py`
implementing the full `ProgressStore` protocol against in-process Python
data structures. The double SHALL preserve event id monotonicity, event
ordering, snapshot upsert semantics, and the no-regression rule so unit
tests against it produce the same observable outcomes as the PostgreSQL
implementation.

Tests for `HermesRunner`, resume planning, duration metrics, dashboard
data assembly, and the `progress` CLI handler SHALL use
`InMemoryProgressStore` by default and SHALL NOT require PostgreSQL.

#### Scenario: In-memory double satisfies the protocol

- **WHEN** the test suite runs without `TEST_DATABASE_URL` set
- **THEN** every test that previously constructed `StateStore(paths)`
  passes against `InMemoryProgressStore()`
- **AND** no test in this set imports from `persistence/`

#### Scenario: In-memory double honors no-regression rule

- **WHEN** a snapshot exists at `(stage=document, status=passed)` and a
  caller records `(stage=design, status=running)` for the same
  `(shard, challenge_id)` pair
- **THEN** the snapshot's stage/status update to the new values but the
  derived percent (from `_percent(stage, status)`) recorded on disk does
  not regress below the old derived percent

### Requirement: progress_events PostgreSQL schema

The system SHALL provide a PostgreSQL table `progress_events` with the
following column set:

| Column | Type | Constraints |
| --- | --- | --- |
| `id` | `BIGSERIAL` | PRIMARY KEY |
| `shard` | `TEXT` | NOT NULL |
| `challenge_id` | `TEXT` | NOT NULL DEFAULT `''` |
| `worker` | `TEXT` | nullable |
| `stage` | `TEXT` | NOT NULL, CHECK in (`queued`, `design`, `implement`, `build`, `validate`, `document`, `complete`) |
| `status` | `TEXT` | NOT NULL, CHECK in (`pending`, `running`, `passed`, `failed`) |
| `message` | `TEXT` | nullable |
| `created_at` | `TIMESTAMPTZ` | NOT NULL DEFAULT `now()` |

The table SHALL NOT include a `percent` column. The derived percent is
computed from `(stage, status)` by `_percent` in `core/state.py`, which
remains the single source of truth for the formula.

Two indexes SHALL exist: `(shard, id)` and `(shard, challenge_id, id)`.

#### Scenario: Invalid stage is rejected at the database

- **WHEN** an INSERT into `progress_events` supplies `stage='cleanup'`
- **THEN** PostgreSQL rejects the row with a CHECK constraint violation
- **AND** `PostgresProgressStore.record` translates this into a
  validation-style exception rather than silently swallowing it

#### Scenario: Schema is created by an Alembic revision

- **WHEN** `alembic upgrade head` runs against a fresh database
- **THEN** revision `0005_progress_events` creates `progress_events` with
  the specified columns, CHECK constraints, and both indexes

### Requirement: progress_snapshots is the dashboard read model

The system SHALL provide a PostgreSQL table `progress_snapshots` keyed by
`(shard, challenge_id)` and updated through `INSERT ... ON CONFLICT DO
UPDATE`. Columns mirror `progress_events` except `id` and `created_at`
are replaced by `updated_at TIMESTAMPTZ NOT NULL DEFAULT now()`.

The dashboard SHALL query `progress_snapshots` directly for the
per-`(shard, challenge_id)` latest-state read model and SHALL NOT
recompute snapshots from `progress_events` on each request.

The "progress never regresses" rule SHALL be enforced in `PostgresProgressStore`
service code (and mirrored in `InMemoryProgressStore`): before performing the
upsert, the implementation computes `new_percent = _percent(new_stage, new_status)`
and `old_percent = _percent(existing_stage, existing_status)` for the existing
snapshot row (if any); if `new_percent < old_percent` the upsert still updates
`updated_at`, `worker`, and `message` but does NOT overwrite `stage` and
`status`.

#### Scenario: Snapshot upsert preserves higher progress

- **WHEN** an existing snapshot has `(stage=document, status=passed)` and
  the caller records `(stage=build, status=running)` for the same
  `(shard, challenge_id)` pair
- **THEN** the resulting snapshot retains `stage=document` and
  `status=passed`
- **AND** `updated_at`, `worker`, and `message` reflect the new event

#### Scenario: Snapshot upsert accepts equal or higher progress

- **WHEN** an existing snapshot has `(stage=build, status=running)` and
  the caller records `(stage=validate, status=running)` for the same
  `(shard, challenge_id)` pair
- **THEN** the resulting snapshot has `stage=validate` and
  `status=running`

### Requirement: progress CLI writes to PostgreSQL and fails loud

The `challenge-factory progress` subcommand SHALL accept the same arguments
as today (`--shard`, `--stage`, `--status`, `--challenge`, `--worker`,
`--message`) and SHALL write through `PostgresProgressStore` to the PostgreSQL
`progress_events` and `progress_snapshots` tables. The CLI MUST print the
inserted event as a JSON object on stdout matching the existing `record`
return shape.

When PostgreSQL is unreachable, malformed-URL, or missing
`DATABASE_URL`, the command SHALL exit with code 2, print a
`PersistenceConfigurationError` or `PersistenceConnectionError`
diagnostic on stderr, and SHALL NOT create any file under `work/` or
the OS temp directory.

#### Scenario: progress reports the inserted event

- **WHEN** an operator runs
  `challenge-factory progress --shard web-0001.json --challenge web-0001 --stage build --status running --message "compiling"`
- **THEN** the command exits 0 and stdout contains a JSON object with
  `event_id` (integer), `shard`, `challenge_id`, `stage`, `status`,
  `percent`, `message`, `worker`, and `updated_at`

#### Scenario: progress fails loud on database unreachable

- **WHEN** `DATABASE_URL` points at an unreachable host and
  `challenge-factory progress ...` is invoked
- **THEN** the command exits with code 2
- **AND** stderr contains `PersistenceConnectionError`
- **AND** no `work/state.sqlite3` file is created

### Requirement: HermesRunner treats progress write failures as warnings

`HermesRunner` SHALL NOT abort a shard claim because a `ProgressStore`
write raised. When `progress.record` or `progress.record_batch` raises,
the runner SHALL log the failure as a warning, continue executing the
shard (artifacts on disk remain authoritative), and let the next
successful write reconcile state. Shard queue file transitions
(`pending` → `running` → `done` / `failed`) SHALL NOT depend on
progress writes succeeding.

#### Scenario: Progress write failure does not abandon shard

- **WHEN** Hermes finishes a shard, the runner attempts to write a
  shard-level `complete/passed` event, and the `ProgressStore` raises
  `PersistenceConnectionError`
- **THEN** the runner logs a warning, still moves the shard file from
  `running/` to `done/`, and still writes the shard report

### Requirement: Dashboard /api/state preserves response shape

`/api/state` SHALL keep its current top-level shape, including the
`storage` object with keys `path`, `fallback`, and `warning`. After
this change the values SHALL be drawn from the PostgreSQL connection
URL: `path` is the redacted `DATABASE_URL` (password masked),
`fallback` is always `false`, and `warning` is the empty string. The
frontend SHALL NOT be modified.

#### Scenario: Storage field is permanent PostgreSQL metadata

- **WHEN** the dashboard fetches `/api/state`
- **THEN** `storage.fallback` is `false`
- **AND** `storage.warning` is the empty string
- **AND** `storage.path` matches `postgresql+psycopg://<user>:***@<host>:<port>/<database>`
