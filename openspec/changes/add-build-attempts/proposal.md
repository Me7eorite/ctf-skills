## Why

Today's pipeline stops at `design_tasks.status = designed`: the validated
structured design exists in PostgreSQL, but turning it into a buildable
challenge under `work/challenges/<category>/<id>-<slug>/` is still a manual
matrix-write-then-CLI-invoke step. Operators have no UI affordance to
"select a designed task and build it," and there is no audit trail linking
the resulting shard execution back to the design it came from.

This change closes that gap with the minimum machinery that fits the
existing architecture: PostgreSQL owns selection and audit; the existing
file-backed shard queue keeps owning execution; a thin reconciler bridges
file-system state back into PostgreSQL.

## What Changes

- Add `build_attempts` as the PostgreSQL-side editorial unit. One row per
  "submit this design for building" action. Each row carries the
  `design_task_id`, an attempt number, status, the attempt-specific shard basename it
  rendered, the worker that claimed it, the resulting challenge directory
  on success, and an error summary on failure.
- Add a five-state machine for `build_attempts.status`: `queued`,
  `running`, `succeeded`, `failed`, `lost`. `lost` is reserved for a
  non-terminal shard disappearing before an execution outcome is observed.
  Artifact availability is tracked independently as
  `artifact_status = unknown|present|missing`, so deleting an artifact does
  not rewrite a historically successful attempt.
- Extend `design_tasks.status` with three new values
  (`building`, `built`, `build_failed`) so the design-task lifecycle
  spans both planning and build phases without overloading the existing
  `failed` value (which keeps its design-phase meaning).
- Add a `BuildOrchestrationService` that submits a batch of design tasks
  for building: it stages matrix-shaped shard JSON, commits queued
  `build_attempts` rows and `building` design-task states in one PostgreSQL
  transaction, then publishes the staged files to `work/shards/pending/`.
  A recovery pass converges crash-interrupted submissions. A partial unique index on
  `build_attempts (design_task_id) WHERE status IN ('queued','running')`
  prevents the same design task from having two active builds.
- Add a `BuildReconciler` daemon thread launched from `web/server.py`.
  It polls `work/shards/{pending,running,done,failed}/` on a configurable
  interval (`BUILD_RECONCILER_POLL_SECONDS`, default 5), updates
  `build_attempts.status`, populates `worker` / `started_at` /
  `finished_at` / `resulting_challenge_dir`, and rolls the parent
  `design_tasks.status` forward.
- Preserve dry-run isolation by requiring both a running queue file and the
  current shard's non-dry-run progress claim event before promoting a queued
  attempt to `running`.
- Guard the legacy shard-requeue endpoint: attributed shards carrying
  `build_attempt_id` return `409` and must use the build-attempt retry endpoint,
  while hand-written unattributed shards retain existing requeue behavior.
- Add a "构建任务" first-class dashboard view modeled on the existing
  Design Tasks page: filter bar plus a table folded by `design_task_id`
  (one row per design task showing its latest attempt) plus a detail
  sub-view exposing the attempt's basic info, sibling attempts as
  history, the linked progress events (including `carry-forward:`
  prefixes from runner resume), and the resulting challenge directory.
- **BREAKING (dashboard chrome)**: the global header bar's `重新验证`,
  `启动 Worker`, `更新时间`, and refresh icon move out of the
  application-wide header and into the new build-tasks view's filter
  bar. The mobile bottom action bar is removed entirely. Other pages
  no longer expose these actions because they are conceptually
  build-stage operations, not global operations. The HTTP endpoints
  (`/api/actions/worker`, `/api/actions/validate`) keep their paths and
  semantics.
- Extend the shard JSON schema with forward-compatible optional top-level
  fields `build_attempt_id`, `design_task_id`, and
  `resume_from_shard_basename`, plus a
  `design` sub-object inside each `challenges[]` entry carrying the
  validated `challenge_designs.payload` content. Hand-written matrix
  shards that omit these continue to run unchanged; the reconciler
  ignores shards that lack the build_attempt_id.
- Retry is incremental: the new attempt emits a fresh attempt-specific
  shard basename, points `resume_from_shard_basename` at the source attempt,
  but does **not** clear
  `work/challenges/<category>/<id>-<slug>/`. The
  runner reads the source attempt's progress window, re-evaluates evidence,
  and only re-executes stages that did not pass. Current progress is written
  under the fresh basename, preserving both token reuse and per-attempt audit.
- Add two more configuration knobs alongside the reconciler interval:
  `BUILD_ATTEMPTS_LIST_DEFAULT_LIMIT` (default 100) and
  `BUILD_ATTEMPTS_LIST_MAX_LIMIT` (default 500) for the new list
  endpoint, mirroring the design-tasks endpoint conventions.

## Capabilities

### New Capabilities
- `build-orchestration`: the database schema, state machines, services,
  reconciler, HTTP endpoints, and dashboard view that take a validated
  design task to a built challenge artifact and surface the result for
  operator monitoring and retry.

### Modified Capabilities
- `design-task-planning`: the design-task status enum gains
  `building`, `built`, and `build_failed`; design-task release for
  build is the only legal trigger out of `designed`/`build_failed`;
  the design-tasks list view exposes a multi-select-with-build batch
  action and a per-row build button. No request-detail or list
  endpoint payload shape changes.
- `hermes-execution-protocol`: generated shards may name a previous shard
  basename as their resume source. Resume reads use that source while
  current-run progress writes continue to use the current basename.

## Impact

- **Code**: add
  `src/persistence/models/build_attempts.py`,
  `src/persistence/repositories/build_attempts.py`,
  `src/services/build_orchestration_service.py`,
  `src/services/build_reconciler.py`,
  `src/web/build_attempts_endpoints.py`, and
  `src/web/static/js/views/build-attempts.js`.
  Edit `src/services/__init__.py` re-exports, `src/web/server.py`
  (register endpoints, spawn reconciler thread), `src/web/dashboard.py`
  (drop the parts of `TaskManager`/`DashboardService` state that the
  removed header buttons consumed), `src/domain/design_tasks.py`
  (status enum), `src/domain/resume.py` and `src/hermes/runner.py`
  (previous-shard resume reads), `src/services/design_task_planning_service.py`
  (release-for-build transition), `src/web/static/index.html`
  (remove header-right and mobile bottom bar), `src/web/static/js/main.js`
  / `state.js` / `router.js` (remove globals; add the new view),
  `src/web/static/js/views/design-tasks.js` (multi-select + build
  button), `src/web/static/js/views/shards.js` (attributed requeue conflict),
  and `prompts/shard_prompt.md` (a single sentence noting the
  optional `design` sub-object).
- **Database**: new Alembic revision `0006_build_attempts` creating
  `build_attempts` plus its indexes, and altering the `design_tasks`
  status `CHECK` constraint to admit the three new values.
- **Tests**: add
  `tests/app/test_build_alembic.py` (PG),
  `tests/app/test_build_attempts_repository.py` (PG),
  `tests/app/test_build_orchestration_service.py`,
  `tests/app/test_build_reconciler.py`,
  `tests/app/test_build_attempts_api.py`,
  runner/resume tests for cross-attempt carry-forward,
  and extend `tests/app/test_dependency_direction.py` with scenarios
  for the new modules. Adjust existing `tests/app/test_design_task_*`
  to cover the three new status values and the new transition rules.
- **Docs**: extend `README.md`'s pipeline section with the build stage,
  add a row each to `docs/architecture.md`'s package table for the new
  services and the new web module, and update
  `openspec/project.md`'s pipeline flow to include build orchestration
  and reconciliation.
- **Dependencies**: no new runtime dependencies. Existing SQLAlchemy,
  FastAPI, and Alembic are sufficient.
- **Operational**: build orchestration requires PostgreSQL reachability
  for the new endpoints to succeed. The reconciler runs once per server
  process, and connection failures inside it are logged as warnings
  without crashing the server. The dashboard "启动 Worker" semantics
  are unchanged at the wire level; only the visual location moves.
