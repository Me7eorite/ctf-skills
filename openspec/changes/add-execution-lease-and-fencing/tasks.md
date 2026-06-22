## 1. Schema & migration

- [x] 1.1 Add `Execution` SQLAlchemy model in `src/persistence/models/executions.py` (fields per spec: build_attempt_id, parent_execution_id, iteration_no, execution_kind, execution_mode, feedback_snapshot_id, worker_id, claim_token, lease_expires_at, heartbeat_at, status, exit_class, error, timestamps)
- [x] 1.2 Add table args: `uq_executions_attempt_iter`, `one_nonterminal_execution_per_attempt` partial unique over `queued|claimed|running`, `ix_executions_lease`, `ix_executions_attempt_iter`, kind/status/mode/claim-field CHECKs, plus composite FKs enforcing same-container parent and feedback ownership
- [x] 1.3 Extend `BuildAttempt` model with nullable `current_execution_id` / `latest_execution_id` / `successful_execution_id` composite FKs that enforce same-container ownership
- [x] 1.4 Alembic `0012`: create `executions`, `build_feedback_snapshots`, and `revalidation_events` + indexes and alter `build_attempts`; no execution backfill for pre-cutover rows (verified up/down/up against test DB)
- [x] 1.5 Add a cutover flag/setting (env or config) gating execution-minting vs legacy build_attempt-minting (`core/execution_config.py`: `execution_minting_enabled()`, `lease_ttl_seconds()`)

## 2. Execution repository & claim/lease

- [x] 2.1 Create `ExecutionsRepository` (`src/persistence/repositories/executions.py`) with `schedule_execution`, `claim_queued`, `get`, `latest_for_attempt`, `list_for_attempt`
- [x] 2.2 Implement scheduling transaction: lock attempt → allocate `iteration_no` → insert execution(`queued`, null claim fields) → set container `latest_execution_id`
- [x] 2.3 Implement claim transaction: lock the exact queued latest execution → mint token/lease and set worker/status → set container `current_execution_id` and `running`; return token to worker
- [x] 2.4 Wire `LEASE_TTL` default to the existing `BUILD_LOST_GRACE` (300s) (`lease_ttl_seconds()`)
- [x] 2.5 Add token/current-gated `update_to_running` and `update_to_terminal`; terminal transition clears current and leaves latest unchanged
- [~] 2.6 Repository `heartbeat()` implements the three-gate (token + active status + is-current); dedicated `POST /api/executions/{id}/heartbeat` endpoint NOT yet wired
- [x] 2.7 Make `executions` the source of truth; derive container status and timestamps from execution transitions in the same transaction
- [~] 2.8 Repository `set_successful_execution()` exists; publisher call-site wiring deferred to §6 (depends on proposal #2 publisher)

## 3. Container status & build_attempts dedup

- [ ] 3.1 Derive and maintain `build_attempts.status` as the container aggregate from execution transitions (same transaction as terminal writes)
- [ ] 3.2 Move authoritative per-run `worker`/`error` writes to the execution row; keep container result fields and maintain `build_attempts.error` only as a latest-execution compatibility aggregate
- [ ] 3.3 Keep `one_active_build_per_task` (container active = aggregate status `queued|running`); ensure it tolerates the cutover window
- [ ] 3.4 Reject terminal writes from executions that are no longer the container's current execution

## 4. Orchestration retry rewrite (Option A)

- [ ] 4.1 Split `_prepare` in `build_orchestration_service.py`: fresh submit → build_attempt container + execution(initial, iter=1); retry/clean → resolve existing container, no `attempt_id = uuid4()`
- [ ] 4.2 Update `_commit` to call `create_execution(kind, parent)` on the retry path instead of `create_attempt`; do not advance `next_build_attempt_no`
- [ ] 4.3 Map `retry()` → execution(kind=`retry`), `clean_rebuild()` → execution(kind=`retry`, execution_mode=clean, same container), `revision` → execution(kind=`revision`, parent set)
- [ ] 4.4 Stage the per-iteration shard as `{build_attempt_id}.iter-NNN.json` (not a reused basename, so progress/resume cannot bleed across iterations) using the existing filesystem prepare/commit compensation flow; publish to pending only after DB scheduling commits, materialize the immutable input copy at `current/input/shard.json`, and let the whole-directory archive move it to `attempts/iter-NNN/input/shard.json` at the next iteration
- [ ] 4.5 Update `_validate_task_for_submit` to anchor eligibility on the container's latest execution
- [ ] 4.6 Reject `revalidate` unless `current_execution_id` is null and `latest_execution_id` names a terminal execution

## 5. Reconciler as lease reaper

- [ ] 5.1 Repurpose `BuildReconciler` to scan executions with expired leases → terminally mark old execution `lost`, record error, clear current, and never auto-schedule or rotate its token; operator retry (or future supervisor) schedules recovery as a new iteration
- [ ] 5.2 Propagate execution terminal transitions to container aggregate status / `latest_execution_id`
- [ ] 5.3 Keep the legacy path for pre-cutover in-flight attempts (no execution rows backfilled)

## 6. Workspace materialization (ties to proposal #1 layout)

- [ ] 6.1 Before claim, atomically rename the entire prior `current/` directory to canonical zero-padded `attempts/iter-NNN/`, then create a fresh `current/` inode; never move only its children, and retain exit_class in DB/manifest rather than the directory name
- [ ] 6.2 Revision claim: resolve the explicit parent execution and materialize `base-artifact` from `../../attempts/iter-<parent.iteration_no>/output`, plus manifest, selected immutable feedback snapshot, and change policy into `current/input/`
- [ ] 6.3 Publisher (proposal #2) re-checks the current token before atomic rename; record `successful_execution_id` on success

## 7. Feedback & revalidate

- [ ] 7.1 Add `build_feedback_snapshots` repository and `POST /api/build-attempts/{id}/feedback`; persist append-only snapshots and require revision to select one by id
- [ ] 7.2 Add `revalidation_events` repository and append on `revalidate` (check/result/timestamp/actor); require no current execution and a terminal latest execution; do NOT create an execution row

## 8. Regression tests

- [ ] 8.1 Retry schedules queued execution(iter=2) under the same build_attempt; claim mints its token; top-level workspace dir unchanged; prior round archived to `attempts/iter-001/`
- [ ] 8.2 Batch of 10 challenges × 2 retries → `work/executions/` holds exactly 10 top-level dirs
- [ ] 8.3 Stale-token complete/publish rejected by `update_to_terminal`; output remains noncanonical in its archived workspace or quarantine
- [ ] 8.4 Expired execution becomes permanently lost; recovery creates a new queued iteration/token; old process's publish is fenced
- [ ] 8.5 Revision materializes base-artifact from the explicit parent's canonical `attempts/iter-NNN/`; non-immediate parent works and unrelated shard is not claimed
- [ ] 8.6 `one_nonterminal_execution_per_attempt`: concurrent second scheduling hits the unique violation
- [ ] 8.7 Fresh submit after an abandoned terminal session opens a new container and advances `attempt_no`
- [ ] 8.8 Post-migration in-flight legacy attempt finishes via the reconciler legacy path with no execution row
- [ ] 8.9 `revalidate` appends a `revalidation_events` row only for a terminal latest execution with no current execution and creates no execution
- [ ] 8.10 Multiple feedback snapshots remain immutable and revision materializes the explicitly selected snapshot
- [ ] 8.11 A stale process whose cwd was the old `current/` can write only into the atomically renamed `attempts/iter-NNN/`, never the new iteration's `current/`
- [ ] 8.12 DB scheduling/shard publication failure compensation leaves neither an orphan queued execution nor an unmatched pending shard
- [ ] 8.13 Iteration 2 (`B.iter-002.json`) `compute_resume_plan` sees no iteration-1 progress events and treats every challenge as fresh

## 9. Validation

- [ ] 9.1 `openspec validate add-execution-lease-and-fencing --strict` passes
- [ ] 9.2 Update `worker-pool-split-plan.md` proposal #3 status to reflect the landed change
