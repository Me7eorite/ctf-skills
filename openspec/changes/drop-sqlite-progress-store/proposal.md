## Why

`work/state.sqlite3` is the last non-PostgreSQL data store left in the control
plane. It forces two divergent storage models (file + temp-dir SQLite fallback
vs. fail-loud PostgreSQL), splits audit data across stores so future
`build_attempts` joins must cross databases, and keeps the dashboard read
model split between two backends. Collapsing the progress event store into
PostgreSQL gives a single source of truth, single failure mode, and lets the
upcoming build-orchestration change link build attempts to their own progress
events through a normal foreign key.

## What Changes

- **BREAKING**: `work/state.sqlite3` is removed. The OS-temp-directory
  fallback (triggered today when `work/` is not writable) goes away with it.
  Existing progress event history is discarded on upgrade — events are
  reconstructed from logs and shard files, not migrated row-by-row.
- **BREAKING**: `progress` CLI now writes to PostgreSQL. When `DATABASE_URL`
  is missing, malformed, or unreachable, the command exits non-zero
  (fail-loud). It no longer silently falls back to a temp-dir SQLite store.
- Introduce a `ProgressStore` protocol in `core/state.py` (6 query methods +
  `record` + `record_batch`).
- Add `InMemoryProgressStore` in `core/state.py` for tests and offline tooling.
- Add `PostgresProgressStore` in `persistence/repositories/progress.py`
  backed by two new tables, `progress_events` and `progress_snapshots`.
- Drop the `percent` column. The percent is derived from `(stage, status)`
  via the existing `_percent` function, which stays the only source of truth.
  The "progress never regresses per `(shard, challenge_id)`" rule is enforced
  in the service layer (Python compares old vs. new derived percent before
  overwriting the snapshot row).
- `HermesRunner` gains a constructor parameter `progress: ProgressStore`.
  `cli.py` and `web/server.py` inject the PostgreSQL implementation at
  composition time. The `hermes` package still does not import `persistence`.
- Dashboard `/api/state` keeps its existing `storage` field shape
  (`{path, fallback, warning}`), but the values are now permanent
  PostgreSQL metadata so the frontend needs no changes.
- Schema details: `progress_events` uses `BIGSERIAL` ids, `TIMESTAMPTZ` server
  clock, nullable `worker` / `message`, and `CHECK` constraints on `stage` /
  `status` matching the current Python validators.

## Capabilities

### New Capabilities
- `progress-event-store`: PostgreSQL-backed progress event and snapshot store
  exposed through a storage-agnostic `ProgressStore` protocol with an
  in-memory test double; covers schema, no-regression upsert rule, fail-loud
  connection contract, and the dashboard read model.

### Modified Capabilities
- `hermes-execution-protocol`: replaces SQLite-specific language and the
  temp-dir fallback contract with the `ProgressStore` protocol; the
  `progress` CLI's behavior on connection failure changes from "silent
  fallback" to "fail-loud". Resume queries and snapshot resets continue to
  apply, but against PostgreSQL.
- `module-architecture`: `core/state.py` no longer owns SQLite. It owns the
  `ProgressStore` protocol and the in-memory test double. The PostgreSQL
  implementation lives in `persistence/repositories/progress.py`. The
  composition-root smoke import line is updated accordingly. Dependency
  direction is unchanged.

## Impact

- **Code**: rewrite `src/core/state.py`; add
  `src/persistence/models/progress.py` and
  `src/persistence/repositories/progress.py`; thread `ProgressStore` through
  `HermesRunner`, `DashboardService`, and every `cli.py` handler that today
  builds `StateStore(paths)`.
- **Database**: new Alembic revision `0005_progress_events` creating
  `progress_events` and `progress_snapshots`. No data migration.
- **Filesystem**: `work/state.sqlite3` and its WAL/SHM siblings are deleted on
  upgrade by a small script under `tools/scripts/`. The `state_database`
  property on `ProjectPaths` is removed.
- **Tests**: all unit tests built around `StateStore(paths)` switch to
  `InMemoryProgressStore()`. A new `tests/app/test_progress_postgres_repository.py`
  exercises the PostgreSQL implementation under
  `@pytest.mark.postgres`. The `progress_store` fixture in `conftest.py`
  defaults to the in-memory double.
- **Docs**: `README.md`, `docs/architecture.md`, and `openspec/project.md`
  drop the "SQLite WAL progress events" wording and the temp-dir fallback
  paragraph; replace with the PostgreSQL progress store description and the
  fail-loud contract.
- **Dependencies**: no new runtime dependencies; SQLAlchemy and `psycopg` are
  already present from `postgres-persistence`.
- **Operational**: workers now require PostgreSQL reachability for the
  `progress` CLI to succeed. The shard claim, artifact build, and
  `validate.sh` paths still work without progress writes, and the runner
  treats `progress` write failures as warnings rather than failing the
  shard (artifacts on disk remain the source of truth for build status).
