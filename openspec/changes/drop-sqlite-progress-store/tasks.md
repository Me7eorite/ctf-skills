## 1. Database schema

- [ ] 1.1 Create Alembic revision `0005_progress_events` that creates `progress_events` (BIGSERIAL id, TEXT shard, TEXT challenge_id default '', nullable worker, TEXT stage with CHECK in the 7-stage list, TEXT status with CHECK in the 4-status list, nullable message, TIMESTAMPTZ created_at default now()).
- [ ] 1.2 Add indexes `progress_events_shard_id_idx (shard, id)` and `progress_events_shard_challenge_id_idx (shard, challenge_id, id)` in the same revision.
- [ ] 1.3 In the same revision, create `progress_snapshots` (TEXT shard, TEXT challenge_id default '', nullable worker, TEXT stage, TEXT status, nullable message, TIMESTAMPTZ updated_at default now(), PRIMARY KEY (shard, challenge_id)).
- [ ] 1.4 Implement `downgrade()` to drop both tables; verify `alembic downgrade -1` then `alembic upgrade head` is clean on an empty database.
- [ ] 1.5 Add `tests/app/test_progress_alembic.py` (mark `@pytest.mark.postgres`) asserting the new revision applies cleanly, the CHECK constraints reject unknown stage/status values, and the snapshot primary key rejects duplicates.

## 2. core/state.py — protocol and in-memory double

- [ ] 2.1 Replace `src/core/state.py` contents: keep `STAGES`, `STATUSES`, `utc_now`, `_percent` (unchanged formula); add `ProgressStore` typing.Protocol with the 7 methods (`record`, `record_batch`, `events_for_shard`, `events_for_challenge`, `latest_claim_event`, `reset_snapshots`, `dashboard`).
- [ ] 2.2 Add an `InMemoryProgressStore` class implementing the full protocol against in-process dicts/lists; enforce id monotonicity, snapshot upsert with the no-regression rule (compare `_percent` on existing vs new), and `dashboard()` shape matching today's response (including `storage={path: 'memory://', fallback: False, warning: ''}`).
- [ ] 2.3 Remove the legacy `StateStore` class entirely; remove the `tempfile.gettempdir()` fallback logic; remove the `ProjectPaths.state_database` property and all its callers under `src/`.
- [ ] 2.4 Add `tests/app/test_progress_in_memory.py` covering record / record_batch / no-regression / events_for_shard / events_for_challenge boundaries / latest_claim_event / reset_snapshots; reuse cases from the deleted `test_state.py` where they apply.

## 3. persistence — ORM and Postgres implementation

- [ ] 3.1 Add `src/persistence/models/progress.py` declaring `ProgressEvent` and `ProgressSnapshot` SQLAlchemy mappings against the new tables; re-export from `persistence.models.__init__`.
- [ ] 3.2 Add `src/persistence/repositories/progress.py` with `PostgresProgressStore` implementing `ProgressStore`; each public method opens a short transaction via the project's `SessionFactory`.
- [ ] 3.3 Implement `record(...)`: insert one event, then upsert snapshot — SELECT FOR UPDATE the existing snapshot row, compare `_percent(old)` vs `_percent(new)`, write either the full new row or keep stage/status and update only updated_at/worker/message.
- [ ] 3.4 Implement `record_batch(events)`: single transaction, raises on the first invalid event with full rollback; reuse the snapshot upsert path per (shard, challenge_id).
- [ ] 3.5 Implement read APIs (`events_for_shard`, `events_for_challenge`, `latest_claim_event`) with the documented id-window semantics; ensure ordering by ascending id; `events_for_challenge` rejects empty challenge_id.
- [ ] 3.6 Implement `reset_snapshots(shard)` (DELETE from progress_snapshots WHERE shard = :shard).
- [ ] 3.7 Implement `dashboard(event_limit)` returning the same JSON shape today's StateStore produces; `storage.path` is the redacted DATABASE_URL, `storage.fallback = False`, `storage.warning = ""`.
- [ ] 3.8 Add `tests/app/test_progress_postgres_repository.py` (`@pytest.mark.postgres`) covering insert+upsert, no-regression on real PG, record_batch atomic rollback, fail-loud on closed engine, `events_for_*` ordering and windows, snapshot reset preserving events.

## 4. Composition root — inject ProgressStore everywhere

- [ ] 4.1 Add `src/persistence/__init__.py` factory export `make_postgres_progress_store() -> ProgressStore` (creates a `PostgresProgressStore` bound to the default `SessionFactory`).
- [ ] 4.2 Update `HermesRunner.__init__` to accept `progress: ProgressStore`; remove its internal `StateStore(paths)` construction; pass `progress` through `process_one` everywhere a `StateStore` method is called.
- [ ] 4.3 Update `DashboardService.__init__` to accept `progress: ProgressStore`; replace internal SQLite usage; update `state()` to call `progress.dashboard(...)`.
- [ ] 4.4 In `src/cli.py`: replace every `StateStore(paths)` call with the injected/created `progress` instance. Use `make_postgres_progress_store()` at handler entry; the `progress` CLI subcommand becomes a thin wrapper around `progress.record(...)`; the `run` subcommand passes the instance to `HermesRunner`.
- [ ] 4.5 In `src/web/server.py`: build the `PostgresProgressStore` once at `serve(...)` startup; pass it to `DashboardService` and any background `HermesRunner` started by the dashboard actions.
- [ ] 4.6 Update `domain/resume.py` and `domain/metrics.py` to accept a `ProgressStore` (or callable) instead of a `StateStore`; both already use only protocol-shaped methods.
- [ ] 4.7 Update `tests/app/conftest.py` to expose a `progress_store` fixture returning `InMemoryProgressStore()`; refactor every test that built `StateStore(paths)` to use the fixture (mechanical search-and-replace).
- [ ] 4.8 Grep the repo (`rg "StateStore"`) and confirm zero matches under `src/` and `tests/`.

## 5. CLI — progress subcommand fail-loud

- [ ] 5.1 In `cli.py`'s `progress` handler, surface `PersistenceConfigurationError` and `PersistenceConnectionError`: print `error: <ExcClass>: <message>` to stderr and exit 2; do NOT fall back to any other store.
- [ ] 5.2 Confirm the success path prints the JSON returned by `progress.record(...)` unchanged (event_id, shard, challenge_id, worker, stage, status, percent, message, updated_at).
- [ ] 5.3 Add a CLI test (`tests/app/test_progress_cli.py` or extend existing): success path uses an in-memory store via dependency injection; the fail-loud path is covered by a PG-marked test that points DATABASE_URL at an unreachable host.

## 6. HermesRunner — non-fatal progress writes

- [ ] 6.1 Wrap every `progress.record(...)` and `progress.record_batch(...)` call in `HermesRunner` with a try/except; on `PersistenceConnectionError` (and only that), log a warning via the runner's existing logger and continue execution. Other exceptions propagate normally.
- [ ] 6.2 Confirm shard queue file transitions (`pending` / `running` / `done` / `failed`) do not depend on `progress.record` success; add a unit test that simulates a raising `ProgressStore` (e.g., a `RaisingProgressStore` test double) and asserts the shard still moves to `done/` on success.
- [ ] 6.3 Use `progress.record_batch(...)` for the resume carry-forward events block in `process_one` so the prefix events ship atomically.

## 7. Dashboard frontend contract preserved

- [ ] 7.1 Confirm `/api/state` response still includes `storage: {path, fallback, warning}` with the documented constant values when backed by `PostgresProgressStore`; add an assertion to `tests/app/test_dashboard.py`.
- [ ] 7.2 Confirm `path` masks the DATABASE_URL password (`postgresql+psycopg://user:***@host:port/db`); add a unit test.
- [ ] 7.3 Verify the frontend (`src/web/static/`) does not need any change — search the JS for `storage.fallback` / `storage.warning` and confirm rendering still works with the constants.

## 8. Upgrade script and docs

- [ ] 8.1 Add `tools/scripts/cleanup_sqlite_state.sh` that runs `rm -f work/state.sqlite3 work/state.sqlite3-wal work/state.sqlite3-shm` and any temp-dir fallback path that may exist. Make it idempotent.
- [ ] 8.2 Update `README.md`: delete the "If `work/` is not writable, the server and workers use the same deterministic database under the operating-system temporary directory" paragraph; add a one-liner saying progress lives in PostgreSQL.
- [ ] 8.3 Update `docs/architecture.md`: in the package table, change the `src/core/state.py` row to "stores the `ProgressStore` protocol and the in-memory test double"; in the Runtime State block, drop the `work/state.sqlite3` reference and replace with "progress events live in PostgreSQL".
- [ ] 8.4 Update `openspec/project.md` "Tech stack" entry on SQLite: change to "PostgreSQL `progress_events` + `progress_snapshots` for append-only progress event store via `core.state.ProgressStore`"; update the "Progress percent is computed from `(stage, status)` in `core/state.py`" non-obvious-conventions bullet to match the new file layout.
- [ ] 8.5 Note the upgrade procedure in `docs/persistence.md` (or create that file if missing): "After pulling this change, run `alembic upgrade head`, then `tools/scripts/cleanup_sqlite_state.sh`. Historical progress events are not migrated."

## 9. Dependency direction guardrail

- [ ] 9.1 In `tests/app/test_dependency_direction.py`, add a scenario asserting `src/hermes/` does not import `persistence.repositories.progress` or any other `persistence.*` module after the refactor.
- [ ] 9.2 Add a scenario asserting `core.state` no longer exports `StateStore` (the class) and DOES export `ProgressStore`, `InMemoryProgressStore`, `STAGES`, `STATUSES`, `_percent`.

## 10. End-to-end verification

- [ ] 10.1 `uv run alembic upgrade head` on a fresh database succeeds, then `alembic downgrade -1` then re-upgrade.
- [ ] 10.2 `uv run pytest --ignore=tests/skills` passes with `TEST_DATABASE_URL` unset (all in-memory paths green).
- [ ] 10.3 With `TEST_DATABASE_URL` set, `uv run pytest -m postgres` covers the new repository tests and the fail-loud CLI test.
- [ ] 10.4 `uv run challenge-factory init && challenge-factory split --matrix matrix.example.jsonl --size 3 && challenge-factory run --worker dry-01 --dry-run` succeeds with no `work/state.sqlite3` file created.
- [ ] 10.5 `DATABASE_URL=postgresql+psycopg://nobody@127.0.0.1:1/none uv run challenge-factory progress --shard x.json --stage build --status running` exits 2 with `PersistenceConnectionError` on stderr.
- [ ] 10.6 `uv run challenge-factory serve` starts, `/api/state` returns 200 with `storage.fallback=false` and a masked `storage.path`.
