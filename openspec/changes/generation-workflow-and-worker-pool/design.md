## Context

The current implementation improved backend routing but still misses the
operator workflow. `/generate/new` asks for one seed at a time and renders as a
large raw form. It does not help the user understand whether they are creating
seeds, shards, runs, or workers. It also routes away too early, before the
worker state and shard names settle, which makes the workflow feel broken even
when the backend accepts the request.

The original diagnosis in `docs/run-creation-and-worker-pool-proposal.md` was
correct about the contract mismatch, but the proposed fix was too narrow:
`POST /api/runs` plus a single-seed form still leaves batch generation, visual
quality, progress observation, and worker pool execution unresolved.

## Goals / Non-Goals

**Goals:**

- Make the browser path capable of running a complete generation workflow from
  an empty project using dry-run mode for testability and real Hermes when
  configured.
- Replace the current raw seed form with an operational, visually credible
  workflow screen.
- Support batches of seeds in one run; a run is not synonymous with one seed.
- Keep execution compatible with the existing single local worker while leaving
  room for a later worker-pool change.
- Keep every execution state observable from the UI: queued, claimed, running,
  completed, failed, stopped.
- Provide deterministic tests that prove frontend-submitted data reaches the
  queue and worker layer.

**Non-Goals:**

- No distributed worker fleet, remote hosts, Kubernetes, or external broker in
  this change.
- No authentication or multi-user scheduling.
- No replacement of `HermesRunner` or the prompt contract.
- No automatic generation of high-quality challenge ideas from only a category
  and count. Operators still provide seed intent, though templates may reduce
  typing.

## Current Failure Assessment

1. **Visual design is form-first, not workflow-first.** The page is technically
   valid but reads as scaffolding. It lacks hierarchy, compact scanning, clear
   primary action, and launch feedback.
2. **The page hides batch semantics.** The backend queue and runner are shard
   and batch oriented, but the UI primarily edits a single object.
3. **The request path is under-specified.** Creating a run must define exactly
   which seeds are included, whether saved seeds are reused, and what execution
   should start after enqueue.
4. **Worker behavior is not modeled.** "Start worker" is a side effect with no
   run association, dry-run option, or clear launch result in the run response.
5. **Navigation is premature.** Sending the user to a runs list immediately
   after launch loses the launch context and makes failures appear as silent
   navigation.
6. **There is no frontend-to-run smoke test.** Existing tests verify pieces,
   but not that the browser workflow produces pending/running shards through
   the same path an operator uses.

## Decisions

### D1. Generation cockpit, not a bigger form

`/generate/new` becomes a single operational cockpit with four full-width
sections:

1. **Seed Batch**: compact editable rows with category, id, title, difficulty,
   points, port/runtime/framework, technique, objective. Advanced JSON is
   available in a side sheet, not always visible.
2. **Shard Plan**: read-only preview of filenames and counts grouped by
   category, including collision warnings before launch.
3. **Execution**: segmented control for `enqueue only` and `single worker`;
   dry-run is an explicit toggle for smoke testing and defaults on.
4. **Launch Monitor**: post-submit state with created shards, worker starts,
   immediate errors, and links to queue/run details.

This removes the left/right card layout that made the page feel like a
settings form. The cockpit is dense and utilitarian, matching an operations
tool rather than a marketing page.

### D2. One run-creation contract owns launch semantics

The frontend calls one endpoint to launch a run. Lower-level seed and action
endpoints remain, but the browser workflow does not stitch them together.
The endpoint creates a persisted run manifest before launching workers, so the
UI has a stable object to observe even when a shard is immediately claimed and
renamed by a worker.

Request shape:

```json
{
  "seeds": [],
  "shard_size": 3,
  "save_seeds": true,
  "execution": {
    "mode": "single_worker",
    "dry_run": true,
    "timeout": 2500
  }
}
```

Response shape:

```json
{
  "ok": true,
  "run_id": "run-20260612-122601",
  "saved_seed_ids": ["web-0001"],
  "shards": [
    {"name": "web-0001-0003.json", "state": "pending", "challenge_count": 3}
  ],
  "execution": {
    "mode": "single_worker",
    "requested_workers": 1,
    "started_workers": ["dashboard-01"],
    "message": "started dashboard-01"
  },
  "links": {
    "runs": "/generate/runs",
    "queue": "/operate/queue"
  }
}
```

The endpoint MUST split only the submitted seeds, not all saved seeds. If
`save_seeds` is true, submitted seeds are also persisted for reuse.

The run manifest is a small JSON document under `work/runs/` (or an equivalent
project path owned by `ProjectPaths`) that records:

- `run_id`
- submitted seed IDs and seed payload hashes
- shard filenames created for the run
- execution request (`mode`, `dry_run`, `timeout`)
- creation timestamp and latest aggregate status

Shard JSON remains the queue source of truth. The manifest is an observability
and grouping document, not a lock or queue owner.

The browser workflow also uses a side-effect-free `POST /api/runs/preview`
endpoint with the same planning fields (`seeds`, `shard_size`,
`execution`). Preview returns validation errors, planned shard filenames,
category counts, and the normalized execution summary, but it MUST NOT write
seed files, shard files, run manifests, or start workers.

Browser-created shard filenames are run-scoped to make repeated test launches
possible. The planner starts from the traditional category/range stem and adds
the run identifier or a short run suffix, for example
`web-0001-0003.run-20260612-122601.json`. The shard payload still uses the
existing `{"challenges": [...]}` format, so workers can claim it through the
same queue mechanics. Low-level `POST /api/seeds/enqueue` keeps the legacy
category/range filename behavior.

### D3. Dry-run is a first-class execution option

The UI needs a safe, deterministic way to prove the workflow works. The cockpit
defaults to dry-run. A dry-run launch creates shards and starts worker subprocesses with
`challenge-factory run --dry-run`, exercising queue claim/completion without
real Hermes generation. The API requires the request to carry an explicit
`execution.dry_run` boolean; it does not silently infer real execution.

Real multi-worker Hermes launch is out of scope for this change. A later
worker-pool change can extend the execution block without changing the cockpit
workflow model.

### D4. Run orchestration stays separate from worker process management

Run orchestration owns seed validation, shard planning, manifest writes, and
the relationship between a run and its shards. The existing task manager owns
single local worker process lifecycle. This separation avoids making process
management responsible for business validation, and it keeps a future
worker-pool implementation from rewriting the run-creation contract.

### D6. Launch stays on page until there is meaningful state

After submit, the page does not immediately hide the result behind a route
change. It shows a Launch Monitor with the created shards and worker start
outcome. Navigation links are explicit. If execution mode starts workers, the
monitor polls for at least one state transition or worker snapshot before
suggesting the user open Runs/Queue.

### D7. Frontend visual rules for this page

- Avoid nested cards. Use full-width bands and compact rows.
- Use icons for actions: add row, duplicate, delete, import, validate, launch.
- Keep cards for repeated seed rows or modal/sheet content only.
- Use a quiet light operational palette already established by the Vue app,
  but add clearer contrast and hierarchy. No dark, purple, beige, or decorative
  gradient treatment.
- The primary action is always visible on desktop and becomes a sticky bottom
  action bar on mobile.

## Risks / Trade-offs

- **Risk: run creation endpoint becomes too large.** Mitigation: keep it as an
  orchestration boundary; validation, splitting, and process launch remain in
  domain/core/task manager modules.
- **Risk: a run manifest duplicates shard state.** Mitigation: the manifest
  stores grouping and requested execution only; current shard state is always
  derived from `work/shards/*` and progress events.
- **Risk: seed table feels complex.** Mitigation: provide templates and import
  from existing saved seeds; advanced JSON lives in a sheet.

## Migration Plan

1. Add backend run creation contract and run manifest support behind tests.
2. Keep execution routed through the existing single-worker task path.
3. Rebuild `/generate/new` as the cockpit using existing Vue/Tailwind
   primitives plus new focused subcomponents.
4. Add a dry-run browser smoke test that submits a seed batch and verifies
   created pending/running/done shards through the single-worker path.
5. Keep old lower-level endpoints and Operate / Workers page functional.

## Resolved Decisions

Resolved for this change:

- Worker pool and real multi-worker Hermes launch are deferred to a later
  OpenSpec change.
- v1 imports saved seeds from `work/challenge_seeds.json` and supports paste-in
  JSON/JSONL in the cockpit. File upload/import from arbitrary paths is
  deferred.
