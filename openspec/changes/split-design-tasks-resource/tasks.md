## 1. Pre-flight

- [ ] 1.1 Archive `add-design-task-planning` change so
  `openspec/specs/design-task-planning/spec.md` exists as the base spec this
  delta modifies (run `opsx:archive` / `openspec-archive-change`).
- [ ] 1.2 Re-validate this change after archive:
  `openspec validate --change split-design-tasks-resource`.

## 2. Repository / data access

- [ ] 2.1 Add `DesignTaskRepository.get_with_history(task_id) -> (DesignTask,
  list[DesignAttempt], ChallengeDesign | None)` — single round-trip via
  `selectinload`/explicit JOIN.
- [ ] 2.2 Add `DesignTaskRepository.list_with_history(generation_request_id,
  status, category, limit) -> list[(DesignTask, list[DesignAttempt],
  ChallengeDesign | None)]` — bounded round-trips (one for tasks, one
  IN-list for attempts, one IN-list for latest_design); enforce `limit`
  default 100, max 500; ordering `(generation_request_id, task_no)`.
- [ ] 2.3 Add `DesignTaskRepository.summarize_for_request(generation_request_id)
  -> dict[str, int]` returning `{ "total": int, "by_status": { ... } }` from
  a single `GROUP BY status` query against the existing
  `ix_design_tasks_generation_request_status` index.
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
- [ ] 3.5 Modify `_register_design_task_endpoints` generate handler: return a
  slim payload `{ "request_id", "design_task_ids": [...], "total": N }`
  instead of inlining task rows.
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
  `renderDesignAttempts`, `renderLatestDesign`, the `designTaskNow` /
  `transitionDesignTask` / `generateDesignTasks` actions if they are not
  used elsewhere, and the `hasRunningDesign` polling branch.
- [ ] 4.7 Add a `Design Tasks` summary card to the research request detail
  page: shows `total` and per-status counts from `design_tasks_summary`;
  primary action `View design tasks →` navigates to
  `#/design-tasks?generation_request_id={id}`.
- [ ] 4.8 Make the `Generate design tasks` button live on the summary card
  in research detail (semantically still a research-request action) and
  on its success refetch ONLY `design_tasks_summary` + navigate to the
  list view; do not refetch the full research detail.

## 5. Tests

- [ ] 5.1 Update `tests/app/test_design_task_api.py`: remove assertions
  that the research request detail response contains `design_tasks`;
  add assertions that it contains `design_tasks_summary` with `total`
  and `by_status`.
- [ ] 5.2 Add `tests/app/test_design_task_list_endpoint.py` covering: empty
  list, filter by `generation_request_id`, filter by `status` (incl.
  unknown 400), filter by `category`, limit/default ordering, round-trip
  count assertion.
- [ ] 5.3 Add `tests/app/test_design_task_detail_endpoint.py` covering: 200
  with full task + attempts + latest_design, 404 on unknown id, 404 on
  malformed id, round-trip count assertion.
- [ ] 5.4 Add a repository unit test asserting
  `summarize_for_request` issues exactly one SQL statement and returns
  zeros for absent statuses.

## 6. Validation

- [ ] 6.1 `uv run ruff check src/ tests/`.
- [ ] 6.2 `uv run pytest tests/app/test_design_task_*.py
  tests/app/test_research_api.py -q`.
- [ ] 6.3 Manual smoke test in browser: research detail shows summary +
  link only; Design Tasks view lists/filters/edits; generate from
  research detail refreshes summary and navigates to list.
- [ ] 6.4 `openspec validate --change split-design-tasks-resource`.
