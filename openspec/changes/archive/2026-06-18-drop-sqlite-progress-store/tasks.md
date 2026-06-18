## 1. Database schema

- [x] 1.1 Create Alembic revision `0005_progress_events` that creates `progress_events` (BIGSERIAL id, TEXT shard NOT NULL, TEXT challenge_id NOT NULL default '', TEXT worker NOT NULL default '', TEXT stage with CHECK in the 7-stage list, TEXT status with CHECK in the 4-status list, INTEGER percent NOT NULL (denormalized cache of `_percent(stage, status)`), TEXT message NOT NULL default '', TIMESTAMPTZ created_at default now()).
- [x] 1.2 Add indexes `ix_progress_events_shard_id (shard, id)`, `ix_progress_events_challenge_id (shard, challenge_id, id)`, and the partial index `ix_progress_events_claims (shard, id) WHERE challenge_id = '' AND stage = 'queued' AND status = 'running'` in the same revision.
- [x] 1.3 In the same revision, create `progress_snapshots` (TEXT shard, TEXT challenge_id default '', TEXT worker NOT NULL default '', TEXT stage NOT NULL, TEXT status NOT NULL, INTEGER percent NOT NULL, TEXT message NOT NULL default '', TIMESTAMPTZ updated_at default now(), PRIMARY KEY (shard, challenge_id)).
- [x] 1.4 Implement `downgrade()` to drop both tables; verify `alembic downgrade -1` then `alembic upgrade head` is clean on an empty database.
- [x] 1.5 Add `tests/app/test_progress_alembic.py` (mark `@pytest.mark.postgres`) asserting the new revision applies cleanly, the CHECK constraints reject unknown stage/status values, and the snapshot primary key rejects duplicates.

## 2. core/state.py — protocol and in-memory double

- [x] 2.1 Replace `src/core/state.py` contents: keep `STAGES`, `STATUSES`, `utc_now`, `_percent` (unchanged formula); add `ProgressEventInput` and `ProgressStore` typing.Protocol with the 7 methods (`record`, `record_batch`, `events_for_shard`, `events_for_challenge`, `latest_claim_event`, `reset_snapshots`, `dashboard`).
- [x] 2.2 Add an `InMemoryProgressStore` class implementing the full protocol against in-process dicts/lists; enforce id monotonicity, snapshot upsert with the no-regression rule (compare `_percent` on existing vs new), and `dashboard()` shape matching today's response (including `storage={path: 'memory://', fallback: False, warning: ''}`).
- [x] 2.3 Remove the legacy `StateStore` class entirely; remove the `tempfile.gettempdir()` fallback logic; remove the `ProjectPaths.state_database` property and all its callers under `src/`.
- [x] 2.4 Add in-memory protocol tests covering record / record_batch / no-regression / UTC timestamp string serialization / events_for_shard / events_for_challenge boundaries / latest_claim_event / reset_snapshots; cases now live in the rewritten `tests/app/test_state.py` and `tests/app/test_state_queries.py` (kept their names but rebuilt against the `ProgressStore` protocol).

## 3. persistence — ORM and Postgres implementation

- [x] 3.1 Add `src/persistence/models/progress.py` declaring `ProgressEvent` and `ProgressSnapshot` SQLAlchemy mappings against the new tables; re-export from `persistence.models.__init__`.
- [x] 3.2 Add `src/persistence/repositories/progress.py` with `PostgresProgressStore` implementing `ProgressStore`; each public method opens a short transaction via the project's `SessionFactory`. Import `_percent` from `core.state` (single source of truth); do NOT redefine the formula in the repository.
- [x] 3.3 Implement `record(...)`: insert one event with `percent=_percent(stage, status)` (imported from `core.state`), then upsert snapshot — SELECT FOR UPDATE the existing snapshot row, always refresh `updated_at`/`worker`/`message`, and overwrite `(stage, status, percent)` only when the new event's `percent >= snapshot.percent`.
- [x] 3.4 Implement `record_batch(events: Sequence[ProgressEventInput])`: single transaction, raises on the first invalid event with full rollback; reuse the snapshot upsert path per (shard, challenge_id).
- [x] 3.5 Implement read APIs (`events_for_shard`, `events_for_challenge`, `latest_claim_event`) with the documented id-window semantics; ensure ordering by ascending id; `events_for_challenge` rejects empty challenge_id.
- [x] 3.6 Implement `reset_snapshots(shard)` (DELETE from progress_snapshots WHERE shard = :shard).
- [x] 3.7 Implement `dashboard(event_limit)` returning the same JSON shape today's StateStore produces; `storage.path` is the redacted DATABASE_URL, `storage.fallback = False`, `storage.warning = ""`.
- [x] 3.8 Add `tests/app/test_progress_postgres_repository.py` (`@pytest.mark.postgres`) covering insert+upsert, no-regression on real PG, record_batch atomic rollback, fail-loud behavior when the underlying engine raises `sqlalchemy.exc.OperationalError` (raises `PersistenceConnectionError`), UTC timestamp string serialization, `events_for_*` ordering and windows, snapshot reset preserving events.

## 4. Composition root — inject ProgressStore everywhere

- [x] 4.1 Add `src/persistence/__init__.py` factory export `make_postgres_progress_store() -> ProgressStore` (creates a `PostgresProgressStore` bound to the default `SessionFactory`).
- [x] 4.2 Update `HermesRunner.__init__` to accept `progress: ProgressStore`; remove its internal `StateStore(paths)` construction; pass `progress` through `process_one` everywhere a `StateStore` method is called.
- [x] 4.2a Update `src/hermes/progress.py`: change the `state: StateStore` parameter on every helper (e.g. `record_final`) to `progress: ProgressStore`; remove the `from core.state import StateStore` import.
- [x] 4.2b Update `src/hermes/validation.py`: change every `state: StateStore` parameter (e.g. on `run_validation`, `record_per_challenge_complete`, `validate_gate`) to `progress: ProgressStore`; remove the `from core.state import StateStore` import.
- [x] 4.3 Update `DashboardService.__init__` to accept `progress: ProgressStore`; replace internal SQLite usage; update `state()` to call `progress.dashboard(...)`.
- [x] 4.4 In `src/cli.py`: replace every `StateStore(paths)` call with the injected/created `progress` instance. Use `make_postgres_progress_store()` at handler entry; the `progress` CLI subcommand becomes a thin wrapper around `progress.record(...)`; the `run` subcommand passes the instance to `HermesRunner`.
- [x] 4.5 In `src/web/server.py`: build the `PostgresProgressStore` once at `serve(...)` startup and pass it to `DashboardService`. Do NOT inject it into the dashboard's background worker — the dashboard's `TaskManager.start("worker")` spawns a `challenge-factory run --worker dashboard-01 …` subprocess, which constructs its own `ProgressStore` through `cli.main()`. The two stores share the same PostgreSQL backend, so coordination is at the database level, not in-process.
- [x] 4.6 Update `domain/resume.py` and `domain/metrics.py` to accept a `ProgressStore` parameter (the `state:` parameter name was kept for diff minimality, but the type is now the `ProgressStore` protocol from `core.state`); both already use only protocol-shaped methods (`latest_claim_event`, `events_for_challenge`). Do NOT introduce a callable-based alternative interface.
- [x] 4.7 Update `tests/app/conftest.py` to expose a `progress_store` fixture returning `InMemoryProgressStore()`; refactor every test that built `StateStore(paths)` to use the fixture (`tests/app/test_metrics.py`, `test_resume.py`, `test_runner_resume.py` — mechanical search-and-replace).
- [x] 4.7a Replaced the SQLite-flavoured `tests/app/test_state.py` with `ProgressStore`-protocol tests (file kept its name for blame continuity); migrated the no-regression and id-monotonicity cases into protocol-level assertions backed by `InMemoryProgressStore`.
- [x] 4.7b Replaced `tests/app/test_state_queries.py` event-window and `latest_claim_event` cases with `InMemoryProgressStore`-backed protocol tests; PG-side equivalent landed under `tests/app/test_progress_postgres_repository.py` (3.8).
- [x] 4.8 Grep the repo (`rg "StateStore"`) and confirm zero matches under `src/` and `tests/`.

## 5. CLI — progress subcommand fail-loud by default

- [x] 5.1 In `cli.py`'s `progress` handler, surface `PersistenceConfigurationError` and `PersistenceConnectionError`: print `error: <ExcClass>: <message>` to stderr and exit 2; do NOT fall back to any other store.
- [x] 5.2 Confirm the success path prints the JSON returned by `progress.record(...)` unchanged (event_id, shard, challenge_id, worker, stage, status, percent, message, updated_at).
- [x] 5.3 Add `--best-effort` to the `progress` subcommand. On `PersistenceConfigurationError` or `PersistenceConnectionError`, best-effort mode prints a warning to stderr, prints no stdout JSON, exits 0, and still creates no SQLite file. Other exception types remain failures.
- [x] 5.4 Add CLI tests (`tests/app/test_progress_cli.py` or extend existing): success path uses an in-memory store via dependency injection; the fail-loud path is covered by a PG-marked test that points DATABASE_URL at an unreachable host; the best-effort path exits 0 with a warning and no stdout JSON.

## 6. HermesRunner — non-fatal progress writes

- [x] 6.1 Wrap every `progress.record(...)` and `progress.record_batch(...)` call in `HermesRunner` with a try/except; on `PersistenceConnectionError` (and only that), log a warning via the runner's existing logger and continue execution. Other exceptions propagate normally.
- [x] 6.2 Confirm shard queue file transitions (`pending` / `running` / `done` / `failed`) do not depend on `progress.record` success; add a unit test that simulates a raising `ProgressStore` (e.g., a `RaisingProgressStore` test double) and asserts the shard still moves to `done/` on success. Coverage lives in `tests/app/test_runner_resume.py`.
- [x] 6.3 Use `progress.record_batch(...)` for the resume carry-forward events block in `process_one` so the prefix events ship atomically.
- [x] 6.4 Update `hermes.prompt.render_prompt(...)` so the injected `{progress_command}` includes `--best-effort`; add/adjust prompt rendering tests to assert the flag is present.
- [x] 6.4a In `prompts/shard_prompt.md`, replace "The dashboard is backed by a SQLite event store" (line ~26) with language that matches the new backend (e.g. "The dashboard reads progress from PostgreSQL via the `{progress_command}` helper"); audit the rest of the file for any other SQLite reference.
- [x] 6.5 Add a runner test proving resume-read failures are not swallowed: if `latest_claim_event` / `events_for_*` raises before prompt rendering, the runner surfaces the error and does not invoke Hermes with an empty resume plan. Covered by `tests/app/test_runner_resume.py::test_resume_read_failure_surfaces_before_hermes_invocation`.

## 7. Dashboard frontend contract preserved

- [x] 7.1 Confirm `/api/state` response still includes `storage: {path, fallback, warning}` with the documented constant values when backed by `PostgresProgressStore`; add an assertion to `tests/app/test_dashboard.py`.
- [x] 7.2 Confirm `path` masks the DATABASE_URL password (`postgresql+psycopg://user:***@host:port/db`); add a unit test.
- [x] 7.3 Verify the frontend (`src/web/static/`) does not need any change — search the JS for `storage.fallback` / `storage.warning` and confirm rendering still works with the constants.

## 8. Upgrade script and docs

- [x] 8.1 Add cross-platform `tools/scripts/cleanup_sqlite_state.py` that removes `work/state.sqlite3`, `work/state.sqlite3-wal`, `work/state.sqlite3-shm`, and the deterministic temp-dir fallback path when it exists. Make it idempotent and avoid shell-only `rm`.
- [x] 8.2 Update `README.md`: delete the "If `work/` is not writable, the server and workers use the same deterministic database under the operating-system temporary directory" paragraph; add a one-liner saying progress lives in PostgreSQL.
- [x] 8.3 Update `docs/architecture.md`: in the package table, change the `src/core/state.py` row to "stores the `ProgressStore` protocol and the in-memory test double"; in the Runtime State block, drop the `work/state.sqlite3` reference and replace with "progress events live in PostgreSQL".
- [x] 8.4 Update `openspec/project.md` "Tech stack" entry on SQLite: change to "PostgreSQL `progress_events` + `progress_snapshots` for append-only progress event store via `core.state.ProgressStore`"; update the "Progress percent is computed from `(stage, status)` in `core/state.py`" non-obvious-conventions bullet to match the new file layout.
- [x] 8.5 Note the upgrade procedure in `docs/persistence.md` (or create that file if missing): "After pulling this change, run `alembic upgrade head`, then `uv run python tools/scripts/cleanup_sqlite_state.py`. Historical progress events are not migrated or reconstructed."

## 9. Dependency direction guardrail

- [x] 9.1 In `tests/app/test_dependency_direction.py`, add a scenario asserting `src/hermes/` does not import `persistence.repositories.progress` or any other `persistence.*` module after the refactor.
- [x] 9.2 Add a scenario asserting `core.state` no longer exports `StateStore` (the class) and DOES export `ProgressStore`, `ProgressEventInput`, `InMemoryProgressStore`, `STAGES`, `STATUSES`. `_percent` stays private (underscore-prefixed) and is consumed only by the two store implementations inside `core/state.py` and `persistence/repositories/progress.py`.

## 10. End-to-end verification

- [x] 10.1 `uv run alembic upgrade head` on a fresh database succeeds, then `alembic downgrade -1` then re-upgrade.
- [x] 10.2 `uv run pytest --ignore=tests/skills` passes with `TEST_DATABASE_URL` unset (all in-memory paths green).
- [x] 10.3 With `TEST_DATABASE_URL` set, `uv run pytest -m postgres` covers the new repository tests and the fail-loud CLI test.
- [x] 10.4 `uv run challenge-factory init && challenge-factory split --matrix matrix.example.jsonl --size 3 && challenge-factory run --worker dry-01 --dry-run` succeeds with no `work/state.sqlite3` file created.
- [x] 10.5 `DATABASE_URL=postgresql+psycopg://nobody@127.0.0.1:1/none uv run challenge-factory progress --shard x.json --stage build --status running` exits 2 with `PersistenceConnectionError` on stderr (behavior covered by `tests/app/test_progress_cli.py`).
- [x] 10.6 `DATABASE_URL=postgresql+psycopg://nobody@127.0.0.1:1/none uv run challenge-factory progress --best-effort --shard x.json --stage build --status running` exits 0 with a warning on stderr, no stdout JSON, and no `work/state.sqlite3` file (behavior covered by `tests/app/test_progress_cli.py`).
- [x] 10.7 `uv run challenge-factory serve` starts, `/api/state` returns 200 with `storage.fallback=false` and a masked `storage.path`.
