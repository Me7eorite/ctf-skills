## 1. Generation run orchestration

- [ ] 1.1 Add a `work/runs/` path to `ProjectPaths` or choose an equivalent runtime-state location for generation run manifests
- [ ] 1.2 Define request/response schemas for `POST /api/runs` covering `seeds`, `shard_size`, `save_seeds`, and `execution.{mode,dry_run,timeout}` where mode is `enqueue_only` or `single_worker`
- [ ] 1.3 Add a run manifest model containing `run_id`, submitted seed IDs/hashes, planned shard names, execution request, creation timestamp, and latest aggregate status
- [ ] 1.4 Add a shard-plan helper that validates submitted seeds, generates run-scoped shard filenames, previews planned shard counts, and verifies planned filenames are unique across pending/running/done/failed before writing files
- [ ] 1.5 Add `POST /api/runs/preview` that returns validation results, planned run-scoped shard filenames, category counts, and execution summary without writing files or starting workers
- [ ] 1.6 Update `DashboardService.create_run` so it validates and splits only the submitted seed batch; saved historical seeds MUST NOT be included unless explicitly submitted
- [ ] 1.7 Generate run-scoped shard filenames for browser-created runs so the same seed batch can be launched repeatedly without colliding with prior queue states
- [ ] 1.8 Enforce planning atomicity: invalid seeds or shard collisions leave no shard files, no saved seed updates, no run manifest, and no worker processes
- [ ] 1.9 Add `GET /api/generation-runs/{run_id}` and any supporting list/read APIs so a run ID can be inspected after shards are claimed/renamed
- [ ] 1.10 Add backend tests for: preview side effects, manifest persistence, valid batch run, repeated same-batch run, mixed category batch, claimed-shard association, shard collision/validation failure, `save_seeds=false`, enqueue-only mode, single-worker dry-run mode, and unsupported execution mode rejection
- [ ] 1.11 Ensure demo mode read-only protection covers any new mutating run endpoints

## 2. Single-worker execution integration

- [ ] 2.1 Route `execution.mode=single_worker` through the existing dashboard single-worker task path with dry-run and timeout options where supported
- [ ] 2.2 Associate the started worker and log file with the generation run manifest or launch response
- [ ] 2.3 Keep `POST /api/actions/worker` as a compatibility endpoint for manually starting one worker outside the New Run cockpit
- [ ] 2.4 Add tests for empty pending queue behavior, immediate worker exit reporting, and run launch response when the single worker starts successfully or fails to start

## 3. New Run cockpit UI

- [ ] 3.1 Replace the current raw New Run form with a full-width generation cockpit organized into Seed Batch, Shard Plan, Execution, and Launch Monitor sections
- [ ] 3.2 Implement compact editable seed rows with add, duplicate, delete, category switch, inline validation, and advanced JSON in a sheet/dialog
- [ ] 3.3 Add templates for at least Web, Pwn, and Reverse seeds that produce backend-valid defaults without requiring every field to be typed from scratch
- [ ] 3.4 Add shard plan preview before launch, powered by `POST /api/runs/preview`, including category grouping, planned run-scoped filenames, shard size, challenge count, and validation/planning errors
- [ ] 3.5 Add execution controls: segmented mode selector (`enqueue only`, `single worker`), dry-run toggle defaulting on, timeout input, and clear copy about real Hermes cost/time
- [ ] 3.5a Do not expose worker-count or local-pool controls in this change; reserve that for a later worker-pool OpenSpec
- [ ] 3.6 Keep launch results on the page after submit; show created shards, started workers, errors, and explicit links to Runs/Queue/Logs
- [ ] 3.7 Improve visual quality: no nested cards, no decorative gradients, compact operational hierarchy, stable dimensions, icon buttons with tooltips, and polished empty/error/loading states
- [ ] 3.8 Update `src/web/static/dist/` with a fresh production build after frontend changes

## 4. Frontend integration and tests

- [ ] 4.1 Add typed API client methods for run preview, run creation, and generation-run detail; frontend components MUST NOT manually stitch `/api/seeds`, `/api/seeds/enqueue`, and `/api/actions/worker`
- [ ] 4.2 Add unit/component tests for seed row validation, category `re` mapping, preview API integration, execution mode toggles, unsupported pool controls being absent, and launch-result rendering
- [ ] 4.3 Add a browser smoke test or documented Playwright verification path: submit a dry-run seed batch from `/generate/new`, verify shard creation, verify at least one worker snapshot, and verify navigation links resolve
- [ ] 4.4 Verify responsive layout at desktop and mobile widths with screenshots; text must not overflow buttons, rows, or badges

## 5. Documentation and cleanup

- [ ] 5.1 Replace or mark `docs/run-creation-and-worker-pool-proposal.md` as superseded by this OpenSpec change
- [ ] 5.2 Update README dashboard workflow documentation to describe the cockpit, dry-run launch, and single-worker execution mode
- [ ] 5.3 Update architecture docs with the generation run manifest responsibility and its boundary with `core.queue`
- [ ] 5.4 Run `openspec validate generation-workflow-and-worker-pool --strict`
- [ ] 5.5 Run backend tests, frontend tests, typecheck, lint, build, and browser smoke verification
