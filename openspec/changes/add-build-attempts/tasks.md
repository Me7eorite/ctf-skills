## 1. Database schema

- [ ] 1.1 Create Alembic revision `0006_build_attempts` that creates `build_attempts` (UUID id PK, UUID design_task_id NOT NULL FK to design_tasks(id), INT attempt_no NOT NULL, TEXT status NOT NULL CHECK in `('queued','running','succeeded','failed','lost')`, TEXT shard_basename NOT NULL, TEXT worker, TEXT resulting_challenge_dir, TEXT error, TIMESTAMPTZ created_at NOT NULL DEFAULT now(), TIMESTAMPTZ started_at, TIMESTAMPTZ finished_at, UNIQUE (design_task_id, attempt_no)).
- [ ] 1.2 Add partial unique index `one_active_build_per_task ON build_attempts (design_task_id) WHERE status IN ('queued','running')`.
- [ ] 1.3 Add ordinary indexes `ix_build_attempts_status_created (status, created_at DESC)` and `ix_build_attempts_shard (shard_basename)`.
- [ ] 1.4 In the same revision, ALTER the `design_tasks.status` CHECK constraint to admit `building`, `built`, and `build_failed` in addition to the current six values. Implement as drop-and-recreate so the constraint name stays stable.
- [ ] 1.5 Implement `downgrade()` to drop the indexes, table, and the new CHECK values; verify `alembic downgrade -1` then `alembic upgrade head` cycle is clean on an empty database.
- [ ] 1.6 Add `tests/app/test_build_alembic.py` (`@pytest.mark.postgres`) asserting the revision applies cleanly, the CHECK constraint rejects an unknown build_attempts status, the partial unique index rejects a second queued/running row for the same design_task, and the new design_tasks CHECK admits the three new values.

## 2. Domain DTOs and validators

- [ ] 2.1 Add `BuildAttemptStatus` enum to `src/domain/design_tasks.py` (or a new `src/domain/build_attempts.py`) carrying the five values. Reuse the project's existing string-enum pattern.
- [ ] 2.2 Extend `DesignTaskStatus` enum to include `building`, `built`, `build_failed`. Keep the existing enum class so all existing imports continue to work.
- [ ] 2.3 Add a domain DTO `BuildAttempt` (frozen dataclass): id, design_task_id, attempt_no, status, shard_basename, worker, resulting_challenge_dir, error, created_at, started_at, finished_at.
- [ ] 2.4 Update `src/domain/design_task_validators.py` (if it gates `status` values) to recognize the new three; existing planning-endpoint validators MUST still reject direct transitions into those states.

## 3. Persistence — ORM and repository

- [ ] 3.1 Add `src/persistence/models/build_attempts.py` with the `BuildAttempt` SQLAlchemy mapping mirroring revision `0006`. Re-export from `persistence/models/__init__.py`.
- [ ] 3.2 Add `src/persistence/repositories/build_attempts.py` with `BuildAttemptsRepository`. Methods: `create_attempt(...)`, `get(id)`, `latest_for_design_task(design_task_id)`, `list_attempts(generation_request_id?, status?, worker?, category?, limit?)` returning the folded "one row per design task" shape used by the list endpoint, `list_for_design_task(design_task_id)` ordered by `attempt_no`, `update_to_running(...)`, `update_to_terminal(...)`. Each method takes a Session and does not open transactions of its own.
- [ ] 3.3 The folded list SQL MUST use a window function or a correlated `MAX(attempt_no)` subquery; no per-row N+1 against `progress_snapshots` or `design_tasks`.
- [ ] 3.4 Add `tests/app/test_build_attempts_repository.py` (`@pytest.mark.postgres`) covering: insert, attempt_no auto-increment, the partial unique index, terminal-status updates with `finished_at`, the folded list ordering and limit, and the join to `progress_snapshots` returning percent.

## 4. Service layer — BuildOrchestrationService

- [ ] 4.1 Add `src/services/build_orchestration_service.py` with `BuildOrchestrationService` exposing `submit_batch(ids) -> list[UUID]`, `submit_single(id) -> UUID`, `retry(attempt_id) -> UUID`, `render_shard_payload(design_task, latest_design) -> dict`.
- [ ] 4.2 `submit_batch` opens one short transaction via `SessionFactory`. For each task: validate current status is `designed` or `build_failed`, compute next `attempt_no`, insert `build_attempts(queued)`, render shard JSON, atomically write to `work/shards/pending/<shard_basename>` via temp-file + rename, set `design_tasks.status = 'building'`. If any single step fails the whole batch rolls back and no shard files survive (clean up partial writes).
- [ ] 4.3 `submit_single` is a thin wrapper around `submit_batch([id])` so behavior stays identical for one or many.
- [ ] 4.4 `retry`: enforce that the source attempt is in a terminal status (`failed` or `lost`). Insert a new attempt with `attempt_no + 1`, write the shard file (overwriting any residual), update design_task to `building`. The existing `work/challenges/<id>-<slug>/` is NOT touched.
- [ ] 4.5 `render_shard_payload`: produce `{"build_attempt_id": ..., "design_task_id": ..., "challenges": [matrix_fields + "design": challenge_designs.payload]}`. The matrix-fields mapping must match `matrix.example.jsonl` keys for the `challenge.category`; new design-only fields go under `challenges[].design`, never inline with matrix keys.
- [ ] 4.6 Re-export `BuildOrchestrationService` from `src/services/__init__.py`.
- [ ] 4.7 Add `tests/app/test_build_orchestration_service.py` (in-memory PG via `pytest-postgresql`) covering: happy path single submit, batch submit ordering, ineligible-status rejection, partial-unique-index conflict propagation as `BuildValidationError`, retry on failed and on lost, atomic rollback when shard write fails.

## 5. Service layer — BuildReconciler

- [ ] 5.1 Add `src/services/build_reconciler.py` with a `BuildReconciler` class and module-level constants `DEFAULT_POLL_INTERVAL_SECONDS = 5` and `POLL_INTERVAL_SECONDS = int(os.environ.get("BUILD_RECONCILER_POLL_SECONDS", str(DEFAULT_POLL_INTERVAL_SECONDS)) or DEFAULT_POLL_INTERVAL_SECONDS)`. On invalid env value, fall back to the default and log a warning once.
- [ ] 5.2 `BuildReconciler.tick(session)` performs the five steps from the build-orchestration spec (`Reconciler mirrors filesystem state` requirement) in order, in one short transaction.
- [ ] 5.3 `BuildReconciler.run_forever()` opens a fresh session per tick (so a long-lived transaction never holds locks), catches all exceptions including `PersistenceConnectionError`, logs a warning, and sleeps `POLL_INTERVAL_SECONDS` before retrying. The thread exits only when the daemon flag flips at shutdown.
- [ ] 5.4 Add a helper `BuildReconciler.tick_once_sync()` that opens a session and runs a single tick. This is used by `/api/state` to refresh state on demand.
- [ ] 5.5 Update `src/web/server.py` `serve(...)` to construct one `BuildReconciler`, start `Thread(target=run_forever, daemon=True)`, and call `tick_once_sync()` inside the `/api/state` handler before serialization.
- [ ] 5.6 Add `tests/app/test_build_reconciler.py` covering each transition (queued→running on running/ shard, running→succeeded on done/ + artifact passed, →failed on done/ + artifact not passed, →lost on done/ + artifact missing, →lost on shard vanished, design_task rollup), invalid env value warning, and a tick that survives a forced PG error.

## 6. HTTP API

- [ ] 6.1 Add `src/web/build_attempts_endpoints.py` exporting `register_build_attempts_endpoints(app)`. Read `BUILD_ATTEMPTS_LIST_DEFAULT_LIMIT` and `BUILD_ATTEMPTS_LIST_MAX_LIMIT` from env at module import with the documented fallbacks and a one-time warning on invalid values.
- [ ] 6.2 Implement `POST /api/design-tasks/build` taking `{"design_task_ids": [...]}`, returning `201 {"build_attempt_ids": [...]}` in the same order; reject malformed UUIDs with `400`; surface ineligible-status as `409` with explanatory message; partial-unique-index conflict as `409`.
- [ ] 6.3 Implement `POST /api/design-tasks/{id}/build` returning `201 {"build_attempt_id": UUID}` or `409` on conflicts.
- [ ] 6.4 Implement `GET /api/build-attempts?status=&worker=&design_task_id=&category=&limit=`. Validate `status` against the five values, `category` against the supported categories, `design_task_id` as UUID. Folded shape (one row per design task) with title/category/percent joined in. Apply default and max limits; set `X-Limit-Capped` header when capped.
- [ ] 6.5 Implement `GET /api/build-attempts/{id}` returning the attempt itself + sibling attempts ordered by `attempt_no` ascending + progress events for the shard (with `carry-forward:` events preserved) + resulting_challenge_dir.
- [ ] 6.6 Implement `POST /api/build-attempts/{id}/retry` returning `201 {"build_attempt_id": UUID}` for the new attempt.
- [ ] 6.7 Register these endpoints in `src/web/server.py` BEFORE the static catch-all route.
- [ ] 6.8 Add `tests/app/test_build_attempts_api.py` covering: 201 responses on happy path, ordering of returned ids, folded list shape, sibling-attempt ordering, limit capping behavior including the response header, malformed-id 400, unknown-status 400, ineligible-status 409, partial-index 409. Use the in-memory `pytest-postgresql` fixture.

## 7. Shard JSON shape + runner contract

- [ ] 7.1 Confirm (in code) that `core.queue.split_matrix` and `core.queue.split_challenges` treat the `{"challenges": [...]}` envelope as canonical and tolerate the new optional top-level `build_attempt_id` and `design_task_id`. Add a test in `tests/app/test_queue.py` or `tests/app/test_core_queue.py` (whichever exists) asserting an envelope with the new fields round-trips through split unchanged.
- [ ] 7.2 Update `src/hermes/prompt.py` rendering (or the prompt template) so a single new sentence references the `design` sub-object for challenges that include it. Existing matrix-only shards must still render without `design` present.
- [ ] 7.3 Update `prompts/shard_prompt.md` with one sentence: when each challenge carries a `design` sub-object, Hermes SHALL use it as authoritative for deployment / artifacts / flag location / validation steps / hints / operator-facing prompt copy.
- [ ] 7.4 Add a prompt-rendering test verifying the new sentence appears when the shard contains a `design` field and is absent when it doesn't.

## 8. Design-task UI changes

- [ ] 8.1 In `src/web/static/js/views/design-tasks.js`, add a checkbox column on rows whose `status` is `designed` or `build_failed`. Track selection in a view-local set; clear on filter change.
- [ ] 8.2 Add a `构建已选` button to the top of the list view; enable only when at least one checkbox is selected. On click, POST to `/api/design-tasks/build` with the selected ids; show a toast linking to `#/build-attempts?generation_request_id={current}`.
- [ ] 8.3 Add a per-row `构建` button alongside existing per-row actions on the same two eligible statuses. On click, POST to `/api/design-tasks/{id}/build`; same toast on success.
- [ ] 8.4 For rows in `building`/`built`/`build_failed`, replace the action buttons with a small badge linking into the build-attempts view filtered by `design_task_id`.

## 9. 构建任务 view (new)

- [ ] 9.1 Add `src/web/static/js/views/build-attempts.js` modelled on `design-tasks.js`: list mode + detail mode, filter bar with five action buttons on the right (`Apply`, `Clear`, `⟳ 刷新`, `▶ 启动 Worker`, `☑ 重新验证`), polling cadence 2.5s active / 12s settled.
- [ ] 9.2 List view columns: 题目 (title), 分类, 难度, 状态, 进度 (percent or "-"), worker, 尝试 (attempt count), 创建时间, 操作 (`详情`, plus `重试` only when latest status is `failed` or `lost`).
- [ ] 9.3 The `⟳ 刷新` button fetches `/api/state` first (forces a reconciler tick) then refetches `/api/build-attempts` with current filters.
- [ ] 9.4 `▶ 启动 Worker` issues `POST /api/actions/worker`; show toast and update local task-state slice (do not poll global state). `☑ 重新验证` issues `POST /api/actions/validate` similarly.
- [ ] 9.5 Detail view sections: basic info (status, attempt_no, started_at, finished_at, worker), 关联设计 (link to `#/design-tasks/{id}`), 关联 shard 路径, 产物目录 (`resulting_challenge_dir` rendered as a plain code span when set), 历史 attempts table ordered by `attempt_no` showing per-attempt status / worker / timestamps with a clickable row to switch detail to that attempt, progress events list highlighting `carry-forward:` lines.
- [ ] 9.6 Add the sidebar entry "构建任务" under a new group (or the "题目管理" group). Register route `#/build-attempts` and `#/build-attempts/:id` in `src/web/static/js/router.js`.
- [ ] 9.7 Add a `state.buildAttempts` slice in `src/web/static/js/state.js` with its own list/detail/filters/flags subfields, polling tracked separately from `state.designTasks`.

## 10. Global header cleanup

- [ ] 10.1 In `src/web/static/index.html`, remove `#updatedAt`, `#refreshButton`, `#validateButton`, `#workerButton` from `<header class="layout-header"> .header-right`. Leave the `<header>` element itself for breadcrumb/title.
- [ ] 10.2 Remove the entire `<div class="layout-mobile-bar">` block (`#mobileValidateButton`, `#mobileWorkerButton`).
- [ ] 10.3 In `src/web/static/js/main.js`, remove event bindings for those removed DOM ids. Remove the polling code that updates `#updatedAt`.
- [ ] 10.4 Audit `src/web/static/js/views/*.js` for any leftover references to `#workerButton` / `#validateButton` / `#refreshButton` / `#updatedAt` and delete.
- [ ] 10.5 Audit `src/web/static/css/` for layout selectors that targeted these removed buttons and remove orphan rules.

## 11. Docs

- [ ] 11.1 Update `README.md`'s pipeline section: add a `build` step after `design` describing the `构建任务` view, the `POST /api/design-tasks/build` action, and the env knob list with defaults.
- [ ] 11.2 Update `docs/architecture.md` package table: one new row for `src/services/build_orchestration_service.py` and one for `src/services/build_reconciler.py`. Update the runtime-state diagram caption to mention `build_attempts` as a PG-side row that mirrors filesystem queue state.
- [ ] 11.3 Update `openspec/project.md` pipeline diagram with the build orchestration + reconciliation step. Add the three new env knobs to a "Configuration knobs" subsection.
- [ ] 11.4 Note in `docs/persistence.md` (or whatever upgrade doc currently exists) that running `alembic upgrade head` is the only operator step; no data migration is required.

## 12. Dependency direction guardrail

- [ ] 12.1 Extend `tests/app/test_dependency_direction.py` with a scenario asserting `services/build_orchestration_service.py` does not import `web.*`.
- [ ] 12.2 Add a scenario asserting `services/build_reconciler.py` does not import `web.*` either.
- [ ] 12.3 Add a scenario asserting `web/build_attempts_endpoints.py` imports `services`/`persistence` but not `hermes` (it has no business reaching into the runner).

## 13. End-to-end verification

- [ ] 13.1 `uv run alembic upgrade head` succeeds on a database that already has revision `0005_progress_events` applied; `alembic downgrade -1` followed by `alembic upgrade head` is clean.
- [ ] 13.2 `uv run pytest --ignore=tests/skills` passes with `TEST_DATABASE_URL` unset (all in-memory paths green).
- [ ] 13.3 With `TEST_DATABASE_URL` set, `uv run pytest -m postgres` covers the new alembic, repository, and reconciler PG tests.
- [ ] 13.4 Manual smoke: in a clean checkout, `uv run challenge-factory serve`, navigate to `#/build-attempts` → empty state renders, navigate to `#/design-tasks`, select a designed task, click `构建已选` → row moves to `building`, navigate to `#/build-attempts` → see the queued attempt, start a worker via `▶ 启动 Worker`, wait for completion, observe transition through `running` and into `succeeded`.
- [ ] 13.5 Manual smoke: rename `work/challenges/<id>-<slug>/` and observe the next reconciler tick (or trigger `⟳ 刷新`) flipping the attempt to `lost`.
- [ ] 13.6 Confirm the global header on `#/overview`, `#/research-requests`, `#/design-tasks`, etc., no longer contains the four removed elements.
- [ ] 13.7 `BUILD_RECONCILER_POLL_SECONDS=12 uv run challenge-factory serve` logs the parsed interval; setting it to `0` or `abc` logs the fallback warning and uses 5.
