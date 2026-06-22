## 1. Schema & migration

- [ ] 1.1 Add `Execution` SQLAlchemy model in `src/persistence/models/executions.py` (fields per spec: build_attempt_id, parent_execution_id, iteration_no, execution_kind, worker_id, claim_token, lease_expires_at, heartbeat_at, status, exit_class, timestamps)
- [ ] 1.2 Add table args: `uq_executions_attempt_iter`, `one_active_execution_per_attempt` partial unique, `ix_executions_lease`, `ix_executions_attempt_iter`, `execution_kind`/`status` CHECKs, and the revision-requires-parent CHECK
- [ ] 1.3 Extend `BuildAttempt` model with nullable `current_execution_id` / `latest_execution_id` / `successful_execution_id` FKs
- [ ] 1.4 Alembic `0012`: create `executions` + indexes and alter `build_attempts`; no execution backfill for pre-cutover rows
- [ ] 1.5 Add a cutover flag/setting (env or config) gating execution-minting vs legacy build_attempt-minting

## 2. Execution repository & claim/lease

- [ ] 2.1 Create `ExecutionsRepository` (`src/persistence/repositories/executions.py`) with `create_execution`, `get`, `latest_for_attempt`, `list_for_attempt`
- [ ] 2.2 Implement single-transaction claim: lock attempt → allocate `iteration_no` → mint `claim_token` + `lease_expires_at = now + LEASE_TTL` → insert execution(`claimed`) → set container `current/latest_execution_id`
- [ ] 2.3 Wire `LEASE_TTL` default to the existing `BUILD_LOST_GRACE` (300s)
- [ ] 2.4 Add token-gated `update_to_running` and `update_to_terminal` (reject stale token; leave output in quarantine)
- [ ] 2.5 Add heartbeat renewal (`lease_expires_at`/`heartbeat_at`) gated on current token

## 3. Container status & build_attempts dedup

- [ ] 3.1 Derive and maintain `build_attempts.status` as the container aggregate from execution transitions (same transaction as terminal writes)
- [ ] 3.2 Move per-run `worker`/`error` writes to the execution row; keep container result fields (`resulting_challenge_dir`, `artifact_status`)
- [ ] 3.3 Keep `one_active_build_per_task` (container active = has a non-terminal execution); ensure it tolerates the cutover window

## 4. Orchestration retry rewrite (Option A)

- [ ] 4.1 Split `_prepare` in `build_orchestration_service.py`: fresh submit → build_attempt container + execution(initial, iter=1); retry/clean → resolve existing container, no `attempt_id = uuid4()`
- [ ] 4.2 Update `_commit` to call `create_execution(kind, parent)` on the retry path instead of `create_attempt`; do not advance `next_build_attempt_no`
- [ ] 4.3 Map `retry()` → execution(kind=`retry`), `clean_rebuild()` → execution(kind=`retry`, execution_mode=clean, same container), `revision` → execution(kind=`revision`, parent set)
- [ ] 4.4 Re-render the shard as `{build_attempt_id}.json` per iteration (overwrite); snapshot immutable copy into `attempts/iter-NNN/input/`
- [ ] 4.5 Update `_validate_task_for_submit` to anchor eligibility on the container's latest execution

## 5. Reconciler as lease reaper

- [ ] 5.1 Repurpose `BuildReconciler` to scan executions with expired `lease_expires_at` (no fresh heartbeat) → mark `lost`, re-mint token on recovery
- [ ] 5.2 Propagate execution terminal transitions to container aggregate status / `latest_execution_id`
- [ ] 5.3 Keep the legacy path for pre-cutover in-flight attempts (no execution rows backfilled)

## 6. Workspace materialization (ties to proposal #1 layout)

- [ ] 6.1 On claim, archive prior `current/{output,logs}` into `attempts/iter-<prev>-<exit_class>/` before recreating `current/` (no rmtree of `attempts/`)
- [ ] 6.2 Revision claim: materialize `base-artifact` (symlink/copy from `../../attempts/iter-(N-1)/output`), `previous-output-manifest.json`, `feedback.json`, `change-policy.json` into `current/input/`
- [ ] 6.3 Publisher (proposal #2) re-checks the current token before atomic rename; record `successful_execution_id` on success

## 7. Feedback & revalidate

- [ ] 7.1 Add `POST /api/build-attempts/{id}/feedback` (summary/requested_changes/preserve/forbid/reviewer); persist immutable snapshot
- [ ] 7.2 Add `revalidation_events` record append on `revalidate` (check/result/timestamp/actor); do NOT create an execution row

## 8. Regression tests

- [ ] 8.1 Retry inserts execution(iter=2) under the same build_attempt; top-level workspace dir unchanged; prior round archived to `attempts/iter-1/`
- [ ] 8.2 Batch of 10 challenges × 2 retries → `work/executions/` holds exactly 10 top-level dirs
- [ ] 8.3 Stale-token complete/publish rejected by `update_to_terminal`; output remains in quarantine
- [ ] 8.4 Expired lease reaped; token re-minted; old process's publish fenced
- [ ] 8.5 Revision materializes base-artifact from `attempts/iter-(N-1)/`; does not claim an unrelated shard
- [ ] 8.6 `one_active_execution_per_attempt`: concurrent second claim hits the unique violation
- [ ] 8.7 Fresh submit after an abandoned terminal session opens a new container and advances `attempt_no`
- [ ] 8.8 Post-migration in-flight legacy attempt finishes via the reconciler legacy path with no execution row
- [ ] 8.9 `revalidate` appends a `revalidation_events` row and creates no execution

## 9. Validation

- [ ] 9.1 `openspec validate add-execution-lease-and-fencing --strict` passes
- [ ] 9.2 Update `worker-pool-split-plan.md` proposal #3 status to reflect the landed change
