## Context

Today every shard execution produces a stream of `(stage, status)` progress
events. The store is a SQLite database at `work/state.sqlite3` written via
`StateStore` in `src/core/state.py`. When `work/` is unwritable, `StateStore`
silently falls back to a deterministic file under the OS temp directory and
records a warning on the dashboard.

Since the `postgres-persistence` change landed, every other relational fact in
the control plane (research requests / runs, design tasks, design attempts,
challenge designs) lives in PostgreSQL with a strict "no silent fallback"
contract. The forthcoming `add-build-attempts` change will need to join
`build_attempts` rows to their per-stage progress events to produce reconciled
build state for the dashboard and for the operator's "select & build" flow.

Keeping the progress store on SQLite creates four concrete problems:

1. Two failure models. PostgreSQL is fail-loud; SQLite silently degrades to
   temp. Operators have to internalize different recovery procedures for
   different stores.
2. Cross-store joins. `build_attempts.id → progress_events` cannot be enforced
   by a foreign key. The reconciler would need application-side joins across
   two backends.
3. Two read models in the dashboard. `/api/state` already serves SQLite
   snapshots; future endpoints would split between SQLite and PostgreSQL.
4. Two test harnesses. SQLite uses a temp directory; PostgreSQL tests use
   `pytest-postgresql`. They cannot share fixtures.

Collapsing the progress store into PostgreSQL aligns failure model, read
model, and audit chain. The cost is one extra round-trip per `progress` CLI
invocation (acceptable at this project's scale: at most ~50 events per
challenge × handful of parallel workers) and a hard PostgreSQL dependency for
workers, which is consistent with the existing `postgres-persistence` rule.

## Goals / Non-Goals

**Goals:**

- Replace SQLite storage with PostgreSQL `progress_events` and
  `progress_snapshots` tables.
- Introduce a `ProgressStore` protocol so consumers (`HermesRunner`,
  `DashboardService`, resume planner, CLI handler) are decoupled from
  the concrete backend.
- Provide an `InMemoryProgressStore` so unit tests do not require
  PostgreSQL.
- Keep the `progress` CLI's stdout/exit contract unchanged for hermes
  agents (signed-off by the hermes-execution-protocol spec).
- Keep dashboard `/api/state` JSON shape unchanged so the frontend
  requires no edits.
- Preserve the "snapshot percent never regresses within a claim window"
  rule observable to the dashboard.

**Non-Goals:**

- Migrating historical SQLite event data. Existing `work/state.sqlite3`
  is discarded on upgrade.
- Building the `build_attempts` table or the reconciler — that is the
  next change.
- Changing the `_percent(stage, status)` formula. The formula stays
  identical and lives only in `core/state.py`.
- Adding new dependencies. SQLAlchemy and `psycopg` already come with
  `postgres-persistence`.

## Decisions

### Decision 1: ProgressStore as a `typing.Protocol` in `core/state.py`

We introduce a `ProgressStore` Protocol that owns the full contract:
`record`, `record_batch`, `events_for_shard`, `events_for_challenge`,
`latest_claim_event`, `reset_snapshots`, `dashboard`. The protocol lives
in `core/state.py` because `core` is the only place every consumer is
allowed to import.

**Alternatives considered:**

- *Abstract base class.* Heavier than `Protocol`; would also require an
  inheritance contract that the in-memory and PG implementations would
  not naturally share. Protocols are structural and the test double
  needs nothing from the production class.
- *Functional dependency injection (pass a dict of callables).*
  Loses static type checking and produces a more awkward call site
  (`progress.record(...)` reads better than `progress["record"](...)`).
- *Put the protocol in `domain/`.* Would require `core/state.py`'s
  `_percent` and the protocol to live in different packages, splitting
  the formula and its contract. Also forces `core/state.py` to either
  empty out or hold only `_percent`, which is awkward.

### Decision 2: Persist progress in two tables — events and snapshots

We keep the SQLite-era split: an append-only `progress_events` table
plus a denormalized `progress_snapshots` upsert table. The dashboard
queries snapshots directly; resume planning queries events.

**Alternatives considered:**

- *Single table, `DISTINCT ON (shard, challenge_id) ORDER BY ... DESC`
  for snapshots.* Removes a table and an upsert path. Rejected because
  it tightens coupling between the dashboard read model and the event
  log: any future change to event shape immediately ripples into the
  dashboard query. Keeping a stable snapshots view shape gives the
  dashboard a contract independent of how events are written.
- *Materialized view over events.* Same coupling problem plus refresh
  scheduling. Not worth it at this scale.

### Decision 3: Drop the `percent` column; compute in Python

The current SQLite schema stores `percent` as an integer. The value is
deterministic from `(stage, status)` via `_percent(stage, status)` in
`core/state.py`. We remove the column. Both `PostgresProgressStore` and
`InMemoryProgressStore` call `_percent` at write time when populating
the API response dict and when comparing snapshots for the no-regression
rule.

**Rationale:** the SQLite era stored a copy because SQLite cannot
inexpensively call back into Python during a query. PostgreSQL can use a
view, but doing so duplicates the formula in SQL and Python. The
dashboard and the runner already touch Python before exposing percent to
the frontend, so application-side computation is both simpler and
safer.

### Decision 4: Snapshot no-regression enforced in service code

The SQLite-era upsert used `percent = MAX(progress_snapshots.percent,
excluded.percent)`. With `percent` gone, we cannot do the same in SQL
without re-encoding `_percent` as a CASE expression. We instead
implement the rule in service code: each `record(...)` call performs a
SELECT (FOR UPDATE) on the snapshot row, computes `_percent` for old
and new, and either UPDATEs full row or UPDATEs only `(updated_at,
worker, message)` while keeping the higher-percent `(stage, status)`.

**Concurrency:** the SELECT FOR UPDATE serializes concurrent updates
to the same `(shard, challenge_id)` row. Different rows are
independent. Two workers updating the same challenge are already a
rare condition (one shard → one worker), so the lock contention cost
is negligible.

**Alternatives considered:**

- *Inline `_percent` as SQL CASE.* Two sources of truth for the
  formula; high risk of drift.
- *Trigger.* Same SQL duplication problem.
- *Ignore the no-regression rule.* Visible regression in the dashboard
  UI (stuck at 99% pattern would also break). The rule has product
  value.

### Decision 5: `HermesRunner` accepts `progress: ProgressStore` at construction

`HermesRunner` currently constructs `StateStore(paths)` internally. We
change the constructor to `HermesRunner(paths, progress: ProgressStore)`.
The composition root (`cli.py` for the `run` subcommand, `web/server.py`
for the dashboard-triggered worker action) is the only place that knows
about `PostgresProgressStore`.

The `hermes` package may not import `persistence` (dependency direction).
Injection preserves that boundary; `HermesRunner` only sees the
`ProgressStore` protocol from `core.state`.

### Decision 6: Single-flush short transactions; runner can batch

Each `record(...)` opens its own short transaction. This keeps semantics
simple and rollback boundaries obvious. For the runner, where a single
`process_one` call writes 5–10 events, we add `record_batch(events) ->
list[dict]` that submits all events in one transaction. The CLI handler
keeps using single-event `record`.

`record_batch` is atomic: any constraint violation rolls back the entire
batch.

### Decision 7: fail-loud `progress` CLI

When `DATABASE_URL` is missing/malformed or PG is unreachable, the
`progress` CLI exits 2 with the persistence error class name on stderr.
No fallback to a temp-dir SQLite file. This is consistent with the
`postgres-persistence` capability's existing fail-loud contract.

The runner, however, treats progress write failures as **warnings, not
shard failures**. Artifacts on disk remain the source of truth for build
status; losing one progress event does not invalidate a successful
shard. The shard queue transition (`pending` → `running` → `done` /
`failed`) is governed by file-system rename success, not progress
writes.

### Decision 8: Discard historical SQLite data

Existing `work/state.sqlite3` is removed at upgrade time. The events
inside it can be reconstructed from per-shard logs if anyone needs them,
and the data is mostly an observation stream of past runs whose
artifacts are already on disk. A row-by-row migration would require:
(a) reading every legacy event, (b) translating the legacy `percent`
column to nothing (we don't store it), (c) handling the temp-dir
fallback location, (d) running before any worker can write new events.
The cost of all that to preserve runs whose artifacts are mostly
already packed and shipped is not worth it.

### Decision 9: Dashboard `storage` field shape preserved

`/api/state` currently returns `{storage: {path, fallback, warning}}`,
and the frontend reads it. We keep the shape but always return
`fallback=false`, `warning=""`, and `path` = redacted DATABASE_URL.
Removes any frontend change. A later cleanup can drop the field.

## Risks / Trade-offs

| Risk | Mitigation |
| --- | --- |
| Every `progress` call adds a PG round-trip; many calls during a shard | `record_batch` for the runner-side multi-event writes; expected total volume (~10⁴ events / day on a busy day) is well within PG capacity |
| Workers now hard-require PG reachability | Matches the existing `postgres-persistence` contract; runner downgrades progress write failures to warnings so a brief PG hiccup does not lose a shard |
| Test ergonomics: many tests built around `StateStore(paths)` need a rewrite | `InMemoryProgressStore` implements the full protocol; mechanical search-and-replace; a single conftest fixture (`progress_store`) makes the migration linear |
| Snapshot no-regression rule moved from SQL to Python; potential lock contention | `SELECT ... FOR UPDATE` on `(shard, challenge_id)` is per-row; no two workers normally update the same challenge concurrently |
| Frontend depends on `storage.fallback` field | Shape preserved; values are constants; no UI change |
| Composition-root churn: every `StateStore(paths)` call site changes | Mostly in `cli.py` (8 call sites) and `web/server.py` (factory at startup); changes are mechanical and covered by `test_dependency_direction.py` |
| Hidden imports inside service handlers (`from core.state import StateStore`) | Add a single grep step in tasks.md so the implementer audits the full repository |

## Migration Plan

1. Land schema first: new Alembic revision `0005_progress_events` creates
   `progress_events` and `progress_snapshots`. Idempotent; no data movement.
2. Implement `core.state.ProgressStore` protocol and `InMemoryProgressStore`
   class. Keep the legacy `StateStore` class temporarily importable but mark
   it as deprecated within this commit only.
3. Implement `persistence.models.progress.{ProgressEvent, ProgressSnapshot}`
   and `persistence.repositories.progress.PostgresProgressStore`.
4. Refactor `HermesRunner`, `DashboardService`, and all `cli.py` command
   handlers to receive an injected `ProgressStore`.
5. Add a factory helper `persistence.factory.make_postgres_progress_store()`
   (used by `cli.py` and `web/server.py`).
6. Update `tests/app/conftest.py` to provide a `progress_store` fixture that
   returns `InMemoryProgressStore()` by default; PG-backed tests opt in via
   `@pytest.mark.postgres`.
7. Migrate existing tests (mechanical: `StateStore(paths) → progress_store`).
8. Add `tests/app/test_progress_postgres_repository.py` covering schema,
   no-regression rule, `record_batch` atomicity, and fail-loud error.
9. Remove the legacy `StateStore` class. Remove `paths.state_database`. Add
   `tools/scripts/cleanup_sqlite_state.sh` to delete `work/state.sqlite3*`
   on upgrade.
10. Update `README.md`, `docs/architecture.md`, `openspec/project.md` to
    reflect the PostgreSQL progress store and the removed temp-dir fallback.

**Rollback strategy:** revert the change; `alembic downgrade -1` drops the
two new tables. There is no SQLite state to restore because the upgrade
already removed it. Operators should run the rollback only if they can
re-source the live shard state from logs.

## Open Questions

- **Per-event timestamp source.** We use server-side `TIMESTAMPTZ DEFAULT
  now()` to avoid clock skew between worker hosts and the PG server. Are
  there any audit consumers that need the *client* timestamp recorded
  separately? (Default: no.)
- **`progress_events` retention policy.** Today the SQLite table grew
  unbounded. PostgreSQL works fine at that volume too, but if we ever
  need cleanup, a follow-up change can add a partitioning or retention
  job. Out of scope here.
