## Why

Research requests, design tasks, and build attempts are first-class dashboard
resources, but none can currently be deleted. Operators therefore cannot
remove accidental, obsolete, or failed work without editing PostgreSQL and the
file-backed shard queue by hand, which can leave orphaned rows, progress state,
or queued work.

## What Changes

- Add a single resource-deletion service that owns database cascades,
  file-backed queue cleanup, progress cleanup, active-state conflict checks,
  and artifact retention for generation requests, design tasks, and build
  attempts.
- Add a persistent `design_tasks.next_build_attempt_no` counter. Build
  submission consumes and increments it under the task row lock, so deleting a
  build-attempt row never permits an attempt number to be reused.
- Extend `ProgressStore` with transaction-aware shard purging so deletion can
  remove events and snapshots through the progress abstraction in the same
  PostgreSQL transaction as the owning resource.
- Add `DELETE` endpoints for all three resource types. The endpoints accept
  `delete_artifacts=false` by default; challenge directories and referenced
  research/design files are removed only when the caller explicitly sends
  `delete_artifacts=true`.
- Reject deletion with `409 Conflict` when the target or any cascading child
  is actively executing (`research_runs.running`, `design_tasks.designing` or
  `design_tasks.building`, or `build_attempts.running`). Queued work may be
  deleted only after its unpublished/staged/pending queue files are safely
  withdrawn.
- Reject deletion of one build attempt while a different sibling attempt is
  queued or running, because the sibling may use the target shard/progress as
  its retry resume source or may be writing the same challenge directory.
- Always remove operational state that cannot outlive its owner, including
  attributed shard files, claim sidecars, `progress_events`, and
  `progress_snapshots`. These files are queue state, not retained artifacts.
- Preserve artifacts by default. The explicit artifact option removes only
  paths proven to belong to the deleted scope and located under approved
  project work roots; shared or unsafe paths are retained and reported.
- Add Delete actions to the request, Design Task, and Build Attempt list/detail
  views. Each action opens a confirmation dialog with an unchecked “同时删除产物”
  option and describes cascading effects before submission.
- Delete structured-design children in explicit dependency order
  (`challenge_designs` before `design_attempts`) rather than assuming every
  foreign key can cascade through `ON DELETE RESTRICT`.
- After deleting one build attempt, recompute its parent design task from the
  latest remaining attempt. Deleting a request or design task relies on the
  existing relational cascade while applying the same queue/progress/artifact
  rules to every affected build attempt.

## Capabilities

### New Capabilities

- `resource-deletion`: safe deletion semantics, artifact retention policy,
  HTTP contracts, and dashboard interactions for generation requests, design
  tasks, and build attempts.

### Modified Capabilities

- `build-orchestration`: build attempt numbering moves from `MAX(attempt_no)+1`
  to a persistent per-task counter, and build rows/history gain explicit
  deletion behavior.
- `progress-event-store`: the protocol gains atomic, transaction-aware purge of
  events and snapshots for one or more shard keys.
- `design-task-planning`: the dedicated Design Task resource and dashboard view
  gain the destructive action governed by `resource-deletion`.
- `research-planning`: generation requests and their dashboard surfaces gain
  the destructive action governed by `resource-deletion`.

## Impact

- **Code**: add a service under `src/services/`; extend
  `src/web/research_endpoints.py`, `src/web/design_task_endpoints.py`, and
  `src/web/build_attempts_endpoints.py`; add a reusable confirmation dialog or
  helper to the static dashboard; update the three resource views.
- **Database**: add an Alembic migration for
  `design_tasks.next_build_attempt_no`, backfilled to the highest existing
  attempt number plus one. Existing foreign-key cascades delete relational
  children where safe; the service explicitly orders structured-design child
  deletion around the existing restrictive foreign key. Progress rows are
  purged explicitly because they are keyed by shard basename rather than a
  foreign key.
- **Filesystem**: attributed files under `work/shards/` are withdrawn during
  deletion. Files under `work/challenges/`, `work/research/`, and
  `work/design/` are retained unless `delete_artifacts=true` and path ownership
  is verified.
- **API**: new destructive endpoints return `404` for unknown resources, `409`
  for active resources, and a success payload that reports retained, deleted,
  and skipped artifact paths.
- **Dependencies**: `add-build-attempts` is archived as the implementation
  baseline. No new runtime dependency is required.
- **Tests**: add PostgreSQL service/API coverage, filesystem rollback and path
  containment tests, and dashboard interaction tests where the current JS test
  harness permits.
