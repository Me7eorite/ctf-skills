## 1. Schema

- [x] 1.1 Add Alembic revision `0003_design_tasks` after the research tables.
- [x] 1.2 Add `design_tasks` with columns described in `design.md`.
- [x] 1.3 Add constraints/indexes:
  - `unique(generation_request_id, task_no)`
  - `unique(generation_request_id, challenge_id)` — scoped per request
    because the planner numbers challenge ids deterministically per request
    (e.g. `web-0001..web-000N`); global uniqueness is deferred to the future
    shard-export change, which will assign final namespaced ids.
  - index `(generation_request_id, status)`
  - `points > 0`
  - `task_no > 0`
  - status check `draft|queued|designing|designed|failed|archived`
- [x] 1.4 Down-migration drops `design_tasks` only.

## 2. Domain and Validation

- [x] 2.1 Add `DesignTask` DTO and `DesignTaskStatus` constants.
- [x] 2.2 Add validation for shard-compatible fields:
  `challenge_id`, `title`, `category`, `difficulty`,
  `primary_technique`, `learning_objective`, `points`, and `port`.
- [x] 2.3 Add validation that task category equals parent request category.
- [x] 2.4 Add validation that generated task count and difficulty distribution
  match the parent request.
- [x] 2.5 Add tests for valid/invalid task candidates.

## 3. Persistence

- [x] 3.1 Add SQLAlchemy model `DesignTask`.
- [x] 3.2 Add repository methods:
  - `list_design_tasks(generation_request_id)`
  - `create_design_tasks(generation_request_id, research_run_id, rows)`
  - `set_design_task_status(task_id, status)`
  - `get_design_task(task_id)`
- [x] 3.3 Ensure repository writes use the supplied session and do not commit.
- [x] 3.4 Add postgres tests for round-trip, constraints, filtering, and status
  updates.

## 4. Planning Service

- [x] 4.1 Add `DesignTaskPlanningService`.
- [x] 4.2 Implement `generate_for_request(request_id)`:
  - require request exists
  - require latest research run is completed
  - require sources/findings exist
  - create exactly `target_count` draft design tasks
- [x] 4.3 Reject regeneration when any task for the request is already
  `queued|designing|designed|failed`. In this change only `queued` is
  actually reachable; the worker states are included as a defensive guard so
  the rule still holds once the future design-worker change starts writing
  `designing|designed|failed`.
- [x] 4.4 Allow regeneration to replace only existing `draft`/`archived`
  tasks, by deleting the old rows and inserting the new set within the same
  transaction.
- [x] 4.5 Keep prompt rendering out of the service; store only structured fields.

## 5. HTTP API

- [x] 5.1 Extend `GET /api/research/requests/{id}` to include `design_tasks`.
- [x] 5.2 Add `POST /api/research/requests/{id}/design-tasks/generate`.
- [x] 5.3 Add `POST /api/design-tasks/{id}/queue`.
- [x] 5.4 Add `POST /api/design-tasks/{id}/archive`.
- [x] 5.5 Add API tests for success, unknown request, request not researched,
  regeneration conflict, queue, and archive.

## 6. Dashboard

- [x] 6.1 Add a `Design Tasks` section to the request detail page.
- [x] 6.2 Add `Generate design tasks` button.
- [x] 6.3 Render task counts and task rows with shard-compatible fields.
- [x] 6.4 Add per-task `Queue` and `Archive` actions.
- [x] 6.5 Keep rendered prompts hidden/out of the UI for this change.

## 7. Validation

- [x] 7.1 Run `uv run ruff check`.
- [x] 7.2 Run focused app tests for research API, design task planning, and
  repository behavior.
- [x] 7.3 Run OpenSpec validation for this change.
