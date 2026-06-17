## 1. Pre-flight

- [ ] 1.1 Archive `add-design-task-planning` change so
  `openspec/specs/design-task-planning/spec.md` exists as the base spec this
  delta modifies (run `opsx:archive` / `openspec-archive-change`).
- [ ] 1.2 Re-validate this change after archive:
  `openspec validate --change split-design-tasks-resource`.

## 2. Repository / data access

- [ ] 2.1 Add `DesignTaskRepository.get_with_history(task_id) -> (DesignTask,
  list[DesignAttempt], ChallengeDesign | None)` — explicit JOIN or fixed
  bounded queries; no per-task/per-history N+1.
- [ ] 2.2 Add `DesignTaskRepository.list_tasks(generation_request_id,
  status, category, limit) -> list[DesignTask]`; enforce `limit` default
  100, max 500; ordering `(generation_request_id, task_no)`.
- [ ] 2.3 Add `DesignTaskRepository.summarize_for_request(generation_request_id)
  -> dict[str, Any]` returning exactly
  `{ "total": int, "by_status": { "draft": int, "queued": int, "designing": int, "designed": int, "failed": int, "archived": int } }`
  — `by_status` MUST contain all six status keys (value 0 when absent) so the
  API contract has a fixed shape. Use a single `GROUP BY status` query against
  the existing `ix_design_tasks_generation_request_status` index, then merge
  into the zero-filled skeleton in Python.
- [ ] 2.4 Add repository tests asserting round-trip counts (e.g. via
  `sqlalchemy.event.listen("after_execute")` or a session-level counter) so
  future regressions don't silently reintroduce N+1.

## 3. HTTP API

- [ ] 3.1 Create `src/web/design_task_endpoints.py` and register it from
  `src/web/server.py` BEFORE the static catch-all route.
- [ ] 3.2 Implement `GET /api/design-tasks?generation_request_id=&status=&
  category=&limit=`. Validate `status` against `DesignTaskStatus`
  (400 on unknown). Validate `generation_request_id` as UUID (400 on
  malformed). Empty result returns `[]` with 200.
- [ ] 3.3 Implement `GET /api/design-tasks/{id}`. 404 on missing or malformed
  id. Response includes full design task fields + `attempts` (ordered by
  `attempt`) + `latest_design`.
- [ ] 3.4 Modify `_register_request_detail` in
  `src/web/research_endpoints.py`: remove the `design_tasks` field and the
  `attempts_by_task` / `latest_design_by_task` loops; add
  `design_tasks_summary` computed via `summarize_for_request`.
- [ ] 3.5 Modify `_register_design_task_endpoints` generate handler: return
  exactly `{ "request_id": str(UUID), "design_task_ids": [str(UUID), ...],
  "total": N }` (ids ordered by `task_no` ascending) instead of inlining task
  rows. Contract owned by spec *Generate endpoint returns task identifiers,
  not full rows*.
- [ ] 3.6 Leave `POST /api/design-tasks/{id}/queue|archive|design` and
  `POST /api/research/requests/{id}/design-tasks/generate` paths/semantics
  unchanged.

## 4. Frontend

- [ ] 4.1 Create `src/web/static/js/views/design-tasks.js` with two modes:
  list and detail. Register routes `#/design-tasks` and
  `#/design-tasks/:id` in `router.js`.
- [ ] 4.2 Implement list mode: filters for `generation_request_id` (from
  query param), `status`, `category`; table rows show `task_no`,
  `challenge_id`, `title`, `difficulty`, `primary_technique`, `status`,
  evidence count; per-row `Queue`/`Archive`/`Design` actions; toolbar
  shows active filters.
- [ ] 4.3 Implement detail mode: full task fields + attempts table +
  latest_design panel; same action buttons as list rows with
  status-driven enable/disable; "back to list (filtered)" navigation.
- [ ] 4.4 Add `state.designTasks` slice (list / listFilters / detail /
  detailId / flags) and dedicated polling that does NOT touch
  `state.detail`.
- [ ] 4.5 Add a `Design Tasks` entry to the sidebar/top-nav so the view is
  reachable without going through a research request.
- [ ] 4.6 In `src/web/static/js/views/research-requests.js`, remove
  `renderDesignTasks`, `renderDesignTasksTable`, `renderDesignPanel`,
  `renderDesignAttempts`, `renderLatestDesign`, the `designTaskNow` and
  `transitionDesignTask` actions, and the `hasRunningDesign` polling branch.
  **Keep `generateDesignTasks`** — it is still wired to the summary card's
  `Generate design tasks` button (see 4.7/4.8).
- [ ] 4.7 Add a `Design Tasks` summary card to the research request detail
  page rendering the spec-defined `design_tasks_summary` shape: `total` and
  all six per-status counts (zero-filled). Card includes a
  `View design tasks →` link navigating to
  `#/design-tasks?generation_request_id={id}` and a `Generate design tasks`
  button (both are on the same card, per spec
  *Request detail exposes design tasks*).
- [ ] 4.8 On `Generate design tasks` success, refetch ONLY
  `design_tasks_summary` (via a targeted summary endpoint or by re-reading
  the request detail and picking only that field) and navigate to the list
  view filtered by the current request. Do NOT refetch the full research
  detail payload, and do NOT iterate the returned `design_task_ids` to fetch
  each task individually.

## 5. Tests

- [ ] 5.1 Update `tests/app/test_design_task_api.py`:
  - remove assertions that the research request detail response contains
    `design_tasks`;
  - assert response includes `design_tasks_summary` with exactly the keys
    `total` and `by_status`;
  - assert `by_status` contains all six status keys
    (`draft|queued|designing|designed|failed|archived`) even when count is
    zero;
  - assert the example two-task fixture produces
    `{ "total": 2, "by_status": { "draft": 1, "queued": 1, "designing": 0, "designed": 0, "failed": 0, "archived": 0 } }`;
  - assert the empty-request fixture produces a zero-filled summary with
    `total = 0`.
- [ ] 5.2 Add `tests/app/test_design_task_list_endpoint.py` covering: empty
  list, filter by `generation_request_id`, filter by `status` (incl.
  unknown 400), filter by `category`, limit/default ordering, and that list
  rows do not inline `attempts` or `latest_design`.
- [ ] 5.3 Add `tests/app/test_design_task_detail_endpoint.py` covering: 200
  with full task + attempts + latest_design, 404 on unknown id, 404 on
  malformed id, round-trip count assertion (fixed bound, regardless of
  attempts count).
- [ ] 5.4 Add a repository unit test asserting
  `summarize_for_request` issues exactly one SQL statement and returns
  the zero-filled six-status skeleton for absent statuses.
- [ ] 5.5 Add a generate-endpoint test asserting the slim payload contract:
  response body keys are exactly `{request_id, design_task_ids, total}`,
  `design_task_ids` is ordered by created tasks' `task_no` ascending,
  `total == len(design_task_ids)`, and the body does NOT include a
  `design_tasks` array of row objects.

## 6. Validation

- [ ] 6.1 `uv run ruff check src/ tests/`.
- [ ] 6.2 `uv run pytest tests/app/test_design_task_*.py
  tests/app/test_research_api.py -q`.
- [ ] 6.3 Manual smoke test in browser: research detail shows summary +
  link only; Design Tasks view lists/filters/edits; generate from
  research detail refreshes summary and navigates to list.
- [ ] 6.4 `openspec validate --change split-design-tasks-resource`.
