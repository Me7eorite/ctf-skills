## Why

The current New Run surface still does not let an operator reliably run the
challenge-generation workflow from the browser. It exposes backend concepts as
a raw form, looks visually unfinished, and hides the actual dependency chain:
seed authoring, shard planning, worker execution, progress observation, failure
requeue, and final run inspection.

This change replaces the current partial "create one seed and maybe start a
worker" approach with an operator-centered generation workflow. Multi-worker
pool execution is intentionally deferred; the priority is to make the frontend
clear, polished, and able to run the current single-worker/dry-run path end to
end.

## What Changes

- Replace `/generate/new` with a generation cockpit that is organized around
  the real workflow:
  1. define or import a batch of challenge seeds,
  2. validate and preview the resulting shard plan,
  3. choose execution mode (`enqueue only`, `single worker`),
  4. launch,
  5. observe queue, workers, logs, and per-challenge progress without leaving
     the workflow.
- Move away from the current raw, form-heavy UI. The page SHALL use a dense
  operational layout with clear steps, compact seed rows, inline validation,
  an always-visible run summary, and direct links to the generated run/shards.
- Make the frontend and backend share one generation contract. The frontend
  SHALL NOT call separate seed, enqueue, and worker endpoints to approximate a
  run. A single run-creation endpoint owns validation, shard creation, optional
  execution, and the response shape needed by the UI.
- Add a side-effect-free run preview endpoint so the Shard Plan section can
  show planned filenames, validation errors, and execution summary before a
  launch writes any runtime state.
- Defer bounded local worker pool execution to a later OpenSpec change. This
  change MAY leave stable extension points in API shapes, but it SHALL NOT
  implement multi-worker launch controls.
- Preserve lower-level endpoints for advanced operators and backwards
  compatibility, but the primary browser path uses the new run contract.
- Supersede `docs/run-creation-and-worker-pool-proposal.md`; that document
  captured the first diagnosis but solved too little of the UX and execution
  problem.

## Capabilities

### New Capabilities

- `generation-run-orchestration`: browser-driven generation run creation,
  including run IDs, persisted run manifests, submitted seed batches, shard
  planning, launch semantics, and aggregate run status across shards/workers.

### Modified Capabilities

- `web-console`: `/generate/new` changes from a simple three-pane category
  composer into an end-to-end generation cockpit with batch seed editing,
  shard preview, execution-mode selection, and live launch feedback.
- `challenge-seed-management`: seed persistence remains matrix-compatible, but
  run creation now supports submitted seed batches without accidentally
  enqueueing unrelated saved seeds.

## Impact

- **Frontend**: rebuild `frontend/src/pages/NewRunPage.vue` around a polished
  workflow surface; add focused components for seed rows, shard plan preview,
  execution mode, and launch summary. Improve error and progress presentation.
- **Backend**: add or revise a single run-creation API, persist generation run
  manifests, and keep execution compatible with the existing single local
  worker task path.
- **APIs**:
  - `POST /api/runs/preview` returns the planned seed/shard/execution summary
    without writing files or starting workers.
  - `POST /api/runs` becomes the primary generation run creation endpoint.
  - Add `GET /api/generation-runs/{run_id}` and, if useful, a generation-run
    list endpoint so `run_id` can be inspected after shard files are claimed
    and renamed by workers without colliding with the existing shard-oriented
    `/api/runs/{shard}` routes.
  - Existing `/api/seeds`, `/api/seeds/enqueue`, and `/api/actions/worker`
    remain available but are no longer the primary browser workflow.
- **Tests**: add backend contract tests for run creation, plus frontend/component
  tests for New Run validation and launch states. Add a browser smoke test that
  proves a frontend-submitted run creates shards and starts single-worker
  execution in dry-run mode.
