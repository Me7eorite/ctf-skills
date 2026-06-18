## 1. Database schema

- [x] 1.1 Create Alembic revision `0006_build_attempts` that creates `build_attempts` (UUID id PK, UUID design_task_id NOT NULL FK to design_tasks(id), INT attempt_no NOT NULL, TEXT status NOT NULL CHECK in `('queued','running','succeeded','failed','lost')`, TEXT shard_basename NOT NULL, TEXT worker, TEXT resulting_challenge_dir, TEXT artifact_status NOT NULL DEFAULT `'unknown'` CHECK in `('unknown','present','missing')`, TEXT error, TIMESTAMPTZ created_at NOT NULL DEFAULT now(), TIMESTAMPTZ started_at, TIMESTAMPTZ finished_at, UNIQUE (design_task_id, attempt_no)).
- [x] 1.2 Add partial unique index `one_active_build_per_task ON build_attempts (design_task_id) WHERE status IN ('queued','running')`.
- [x] 1.3 Add ordinary indexes `ix_build_attempts_status_created (status, created_at DESC)` and `ix_build_attempts_shard (shard_basename)`.
- [x] 1.4 In the same revision, ALTER the `design_tasks.status` CHECK constraint to admit `building`, `built`, and `build_failed` in addition to the current six values. Implement as drop-and-recreate so the constraint name stays stable.
- [x] 1.5 Implement `downgrade()` to drop the indexes, table, and the new CHECK values; verify `alembic downgrade -1` then `alembic upgrade head` cycle is clean on an empty database.
- [x] 1.6 Add `tests/app/test_build_alembic.py` (`@pytest.mark.postgres`) asserting the revision applies cleanly, the CHECK constraint rejects an unknown build_attempts status, the partial unique index rejects a second queued/running row for the same design_task, and the new design_tasks CHECK admits the three new values.

## 2. Domain DTOs and validators

- [x] 2.1 Add `BuildAttemptStatus` enum to `src/domain/design_tasks.py` (or a new `src/domain/build_attempts.py`) carrying the five values. Reuse the project's existing string-enum pattern.
- [x] 2.2 Extend `DesignTaskStatus` enum to include `building`, `built`, `build_failed`. Keep the existing enum class so all existing imports continue to work.
- [x] 2.3 Add a domain DTO `BuildAttempt` (frozen dataclass): id, design_task_id, attempt_no, status, shard_basename, worker, resulting_challenge_dir, artifact_status, error, created_at, started_at, finished_at.
- [x] 2.4 Update `src/domain/design_task_validators.py` (if it gates `status` values) to recognize the new three; existing planning-endpoint validators MUST still reject direct transitions into those states.

## 3. Persistence — ORM and repository

- [x] 3.1 Add `src/persistence/models/build_attempts.py` with the `BuildAttempt` SQLAlchemy mapping mirroring revision `0006`. Re-export from `persistence/models/__init__.py`.
- [x] 3.2 Add `src/persistence/repositories/build_attempts.py` with `BuildAttemptsRepository`. Methods: `create_attempt(...)`, `get(id)`, `latest_for_design_task(design_task_id)`, `list_attempts(generation_request_id?, status?, worker?, category?, limit?)` returning the folded "one row per design task" shape used by the list endpoint, `list_for_design_task(design_task_id)` ordered by `attempt_no`, `update_to_running(...)`, `update_to_terminal(...)`, `update_artifact_status(...)`. Each method takes a Session and does not open transactions of its own.
- [x] 3.3 The folded list SQL MUST use a window function or a correlated `MAX(attempt_no)` subquery. It MUST select each task's highest attempt before applying status/worker/request/category filters, so filters never expose an older sibling. No per-row N+1 against `progress_snapshots` or `design_tasks`.
- [x] 3.4 Add `tests/app/test_build_attempts_repository.py` (`@pytest.mark.postgres`) covering: insert, attempt_no auto-increment, the partial unique index, terminal-status updates with `finished_at`, the folded list ordering and limit, and the join to `progress_snapshots` returning percent.

## 4. Service layer — BuildOrchestrationService

- [x] 4.1 Add `src/services/build_orchestration_service.py` with `BuildOrchestrationService` exposing `submit_batch(ids) -> list[UUID]`, `submit_single(id) -> UUID`, `retry(attempt_id) -> UUID`, and pure `render_shard_payload(design_task, latest_design, *, build_attempt_id, resume_from_shard_basename=None) -> dict`.
- [x] 4.2 `submit_batch` validates all tasks, pre-allocates each `build_attempt_id` with the repository's existing application-side UUID pattern, ensures `work/shards/staging/build-attempts/` exists (including from `ProjectPaths.initialize()`), and writes attributed payloads to `work/shards/staging/build-attempts/<build_attempt_id>.json`; then one short PostgreSQL transaction inserts all queued rows with those IDs and changes all tasks to `building`; after commit, make a best-effort atomic rename into `pending/`. Pre-commit failures leave neither rows nor pending files. Post-commit publication failures are logged, remain accepted, and converge through recovery.
- [x] 4.3 Add idempotent staging recovery used at server startup and before every reconciler tick: publish a staged payload when its queued row committed, remove only staged payloads older than one hour that have no database row, leave younger in-flight staging files alone, and do not duplicate an already-published shard. A matching staged payload counts as present for that tick and prevents queued→lost even when publication fails again.
- [x] 4.4 `submit_single` is a thin wrapper around `submit_batch([id])` so behavior stays identical for one or many.
- [x] 4.5 For ordinary submit of `build_failed`, require the latest attempt to be `failed`/`lost` and use its basename as the resume source. For `retry`, additionally require the named source to be that latest attempt and the parent to be `build_failed`; reject stale sibling retries. Create the fresh attempt without touching the artifact directory.
- [x] 4.6 `render_shard_payload`: produce `{"build_attempt_id": ..., "design_task_id": ..., "resume_from_shard_basename": ..., "challenges": [matrix_fields + "design": challenge_designs.payload]}`; omit the resume field on initial submissions. The matrix-fields mapping must match `matrix.example.jsonl` keys for the category.
- [x] 4.7 Re-export `BuildOrchestrationService` from `src/services/__init__.py`.
- [x] 4.8 Add service tests covering happy paths, conflicts, pre-commit cleanup, crash-after-commit recovery, idempotent publication, build_failed resume-source linkage, and stale-sibling retry rejection.

## 5. Service layer — BuildReconciler

- [x] 5.1 Add `src/services/build_reconciler.py` with a `BuildReconciler` class and module-level constants `DEFAULT_POLL_INTERVAL_SECONDS = 5` and `POLL_INTERVAL_SECONDS` parsed from `BUILD_RECONCILER_POLL_SECONDS`. On missing, non-integer, or non-positive env value, fall back to the default and log a warning once.
- [x] 5.2 `BuildReconciler.tick(session)` performs staging recovery as a bounded filesystem step, then performs row reconciliation in one short PostgreSQL transaction. queued→running requires both the attributed running file and the current basename's shard-level queued/running progress claim event, preserving dry-run isolation. It also supports queued→terminal fast completion, recovers worker/start time, and updates artifact availability without changing terminal status.
- [x] 5.3 `BuildReconciler.run_forever()` opens a fresh session per tick (so a long-lived transaction never holds locks), catches all exceptions including `PersistenceConnectionError`, logs a warning, and sleeps `POLL_INTERVAL_SECONDS` before retrying. The thread exits only when the daemon flag flips at shutdown.
- [x] 5.4 Add a helper `BuildReconciler.tick_once_sync()` that opens a session and runs a single tick. This is used by `/api/state` to refresh state on demand.
- [x] 5.5 Update `src/web/server.py` so `create_app(...)` can receive or read one optional `BuildReconciler` instance (for example through `app.state.build_reconciler`), `serve(...)` constructs it, runs startup staging recovery, starts `Thread(target=run_forever, daemon=True)`, and the existing `/api/state` handler calls `tick_once_sync()` before serialization when the reconciler is present.
- [x] 5.6 Add reconciler tests covering queued→running with a claim event, running file without claim event remaining queued, dry-run claim/requeue remaining queued, terminal transitions, artifact availability, vanished active shard, staging recovery/failure, attribution guards, configuration, and PG failure resilience.

## 6. HTTP API

- [ ] 6.1 Add `src/web/build_attempts_endpoints.py` exporting `register_build_attempts_endpoints(app)`. Read `BUILD_ATTEMPTS_LIST_DEFAULT_LIMIT` and `BUILD_ATTEMPTS_LIST_MAX_LIMIT` from env at module import with the documented fallbacks and a one-time warning on invalid values.
- [ ] 6.2 Implement `POST /api/design-tasks/build` taking `{"design_task_ids": [...]}`, returning `201 {"build_attempt_ids": [...]}` in the same order; reject malformed UUIDs with `400`; surface ineligible-status as `409` with explanatory message; partial-unique-index conflict as `409`.
- [ ] 6.3 Implement `POST /api/design-tasks/{id}/build` returning `201 {"build_attempt_id": UUID}` or `409` on conflicts.
- [ ] 6.4 Implement `GET /api/build-attempts?status=&worker=&design_task_id=&generation_request_id=&category=&limit=`. Validate inputs; select the highest attempt per task first, then apply all filters, joins, ordering, and limits. Add a regression test proving `status=failed` cannot expose an old failed sibling when the latest attempt is queued. Set `X-Limit-Capped` when capped.
- [ ] 6.5 Implement `GET /api/build-attempts/{id}` returning the attempt itself + sibling attempts ordered by `attempt_no` ascending + progress events for the current attempt's shard (with `carry-forward:` events preserved) + resulting_challenge_dir + artifact_status.
- [ ] 6.6 Implement `POST /api/build-attempts/{id}/retry` returning `201 {"build_attempt_id": UUID}` for the new attempt and `409` for stale siblings or a parent not in `build_failed`.
- [ ] 6.7 Register these endpoints in `src/web/server.py` BEFORE the static catch-all route.
- [ ] 6.8 Add `tests/app/test_build_attempts_api.py` covering: 201 responses on happy path, ordering of returned ids, folded list shape, latest-row filter ordering, sibling-attempt ordering, limit capping behavior including the response header, malformed-id 400, unknown-status 400, ineligible-status 409, stale-retry 409, and partial-index 409. Use the in-memory `pytest-postgresql` fixture.
- [ ] 6.9 Guard the existing `/api/shards/{state}/{name}/requeue` endpoint in `src/web/server.py` before it calls `DashboardService.requeue_shard(...)`: resolve the source queue path, parse the source payload, and return `409` without moving it when top-level `build_attempt_id` is present; keep unattributed shard behavior unchanged. Add API tests for both paths and update the Shards view to direct attributed conflicts to the build-attempt retry UI.

## 7. Shard JSON shape + runner contract

- [ ] 7.1 Confirm (in code) that `core.queue.split_matrix` and `core.queue.split_challenges` continue to accept existing JSONL matrix rows and preserve unknown per-challenge fields such as `design` inside each `challenges[]` entry. Add a test in `tests/app/test_shards.py` asserting a row with `design` survives split output unchanged. The orchestration service writes the generated `{"build_attempt_id": ..., "design_task_id": ..., "resume_from_shard_basename": ..., "challenges": [...]}` envelope directly (omitting the resume field initially) and does not route generated shards through `split_matrix`.
- [ ] 7.2 Update `src/hermes/prompt.py` rendering (or the prompt template) so a single new sentence references the `design` sub-object for challenges that include it. Existing matrix-only shards must still render without `design` present.
- [ ] 7.3 Update `prompts/shard_prompt.md` with one sentence: when each challenge carries a `design` sub-object, Hermes SHALL use it as authoritative for deployment / artifacts / flag location / validation steps / hints / operator-facing prompt copy.
- [ ] 7.4 Add a prompt-rendering test verifying the new sentence appears when the shard contains a `design` field and is absent when it doesn't.
- [ ] 7.5 Extend `HermesRunner` resume planning to validate optional `resume_from_shard_basename`, read historical claim/challenge events from that source, and keep every current progress write under the current original basename. Add tests for retry source reads, current-key writes, omitted-field compatibility, and unsafe-path rejection.

## 8. Design-task UI changes

- [ ] 8.1 In `src/web/static/js/views/design-tasks.js`, add a checkbox column on rows whose `status` is `designed` or `build_failed`. Track selection in a view-local set; clear on filter change.
- [ ] 8.2 Add a `构建已选` button to the top of the list view; enable only when at least one checkbox is selected. On success, link to the request-filtered build view only when all selected tasks share one generation request; otherwise link to unfiltered `#/build-attempts`.
- [ ] 8.3 Add a per-row `构建` button alongside existing per-row actions on the same two eligible statuses. On click, POST to `/api/design-tasks/{id}/build`; same toast on success.
- [ ] 8.4 For rows in `building`/`built`, replace actions with a linked build-attempt badge. For `build_failed`, show the linked badge while retaining checkbox and `构建` actions.

## 9. 构建任务 view (new)

- [ ] 9.1 Add `src/web/static/js/views/build-attempts.js` modelled on `design-tasks.js`: list mode + detail mode, filters for status/worker/category/design task/generation request, five action buttons on the right, and polling cadence 2.5s active / 12s settled. Parse `generation_request_id` from the route into a visible editable filter.
- [ ] 9.2 List view columns: 题目, 分类, 难度, 状态, 产物状态, 进度, worker, 尝试, 创建时间, 操作 (`详情`, plus `重试` only for `failed` or `lost`).
- [ ] 9.3 The `⟳ 刷新` button fetches `/api/state` first (forces a reconciler tick) then refetches `/api/build-attempts` with current filters.
- [ ] 9.4 `▶ 启动 Worker` issues `POST /api/actions/worker`; show toast and update local task-state slice (do not poll global state). `☑ 重新验证` issues `POST /api/actions/validate` similarly.
- [ ] 9.5 Detail view sections: basic info (status, artifact_status, attempt_no, started_at, finished_at, worker), 关联设计 (link to `#/design-tasks/{id}`), 关联 shard 路径, 产物目录 (`resulting_challenge_dir` rendered as a plain code span when set), 历史 attempts table ordered by `attempt_no` showing per-attempt status / artifact_status / worker / timestamps with a clickable row to switch detail to that attempt, progress events list highlighting `carry-forward:` lines.
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
- [ ] 13.5 Manual smoke: after success, rename the artifact directory and observe `artifact_status` become `missing` while attempt remains `succeeded` and task remains `built`; restore it and observe availability return to `present`.
- [ ] 13.6 Confirm the global header on `#/overview`, `#/research-requests`, `#/design-tasks`, etc., no longer contains the four removed elements.
- [ ] 13.7 `BUILD_RECONCILER_POLL_SECONDS=12 uv run challenge-factory serve` logs the parsed interval; setting it to `0` or `abc` logs the fallback warning and uses 5.
