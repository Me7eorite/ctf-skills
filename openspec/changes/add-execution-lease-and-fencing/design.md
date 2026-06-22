## Context

Today a build run is one `build_attempts` row, and retry / clean-rebuild mint a
**new** row (`attempt_id = uuid4()`, `attempt_no + 1`). `derive_workspace_id`
names the execution workspace after `build_attempt_id`, so every retry produces
a fresh bare-UUID top-level directory under `work/executions/`. There is no
lease and no fencing token: liveness is purely time-based — `BuildReconciler`
marks a stuck row `lost` after a 300s grace, and the code asserts "only the
reconciler changes status". With more than one worker or a crash-recovery, a
stale process can still write into `work/challenges` because nothing fences the
write boundary.

This change is proposal #3 of the Worker Pool split. Implementation-level detail
(DDL, exact rewrite points, field-dedup table, test list) lives in
[`option-a-execution-container-design.md`](../../../option-a-execution-container-design.md);
this document records the architectural decisions and migration strategy.

## Goals / Non-Goals

**Goals:**

- Reverse the retry model: `build_attempt` becomes a per-challenge container;
  retry / clean-rebuild / revision append `executions` rows under it.
- Collapse the workspace directory explosion to one stable folder per challenge,
  with prior failure scenes archived (not erased) under `attempts/iter-NNN/`.
- Add a fencing token + lease so an expired worker cannot publish dirty output.
- Make the prior iteration's failure scene reachable in place for revision.

**Non-Goals:**

- Agent registry / capability hard-constraints (proposal #4).
- Supervisor, slots, global concurrency (proposal #5).
- Immutable audit snapshot fields and legacy isolation (proposal #6).
- Feedback management UI (later `add-build-attempt-feedback-ui`).
- Cross-host scheduling.

## Decisions

### D1: Container = build_attempt; run = execution (Option A)

Retry no longer mints a new `build_attempts` row; it inserts an `executions`
row under the existing container (`iteration_no = max + 1`). Chosen over
**Option B** (keep retry = new build_attempt, execution 1:1 only for lease)
because B leaves the directory explosion and the "next retry runs blind" problem
intact, and makes `iteration_no` / `parent_execution_id` redundant with the
existing `attempt_no` / `retry_sources`. Because `derive_workspace_id` already
keys on `build_attempt_id`, A makes the workspace folder stable across a retry
chain with **no key change** — only the retry path changes.

### D2: Fencing via a per-execution claim_token, validated at every write

Claim mints `claim_token` + `lease_expires_at` in the same transaction that
inserts the execution (reusing the existing `with_for_update()` row lock).
`update_to_running`, `update_to_terminal`, the publisher, and the heartbeat path
all require the current token; a stale token is rejected and its output stays in
quarantine. The "only the reconciler changes status" invariant is relaxed to
"status may change from claim / worker / reconciler, but every write is
token-gated" — safety comes from the fence, not from exclusivity.

### D3: Reconciler becomes a lease reaper; TTL reuses the 300s grace

Rather than introduce a parallel liveness mechanism, `BuildReconciler` is
repurposed to reap expired execution leases and re-mint tokens on recovery. The
existing `BUILD_LOST_GRACE` (300s) becomes the default `LEASE_TTL`, keeping the
liveness window unchanged and the logic isomorphic.

### D4: One active execution per container via a partial unique index

A partial unique index on `executions(build_attempt_id) WHERE status IN
('claimed','running')` enforces single-active at the database, mirroring the
existing `one_active_build_per_task` pattern. The container's
`one_active_build_per_task` index is retained to keep at most one active build
session per design task.

### D5: Revision base artifact is read in place from the same directory

A `revision` claim resolves its base artifact from
`work/executions/<build_attempt_id>/attempts/iter-(N-1)/output` — a local
filesystem lookup within the same challenge directory, not a cross-UUID copy or
a DB-path join. This is only possible because D1 keeps the retry chain in one
folder, which is why the workspace-by-challenge consolidation is a hard
prerequisite for revision reuse.

### D6: Shard file stays `{build_attempt_id}.json`, re-rendered per iteration

The shard basename is kept stable and overwritten each iteration (carrying the
new resume / base-artifact / feedback context) so `work/shards` scanning and
queue claiming are unchanged. The immutable per-iteration snapshot is the
workspace's `attempts/iter-NNN/input/shard.json`, which is also the audit
source.

## Risks / Trade-offs

- **Retry semantics change is internally breaking** → guard the cutover with a
  flag/timestamp (D-migration); in-flight legacy attempts finish via the
  reconciler legacy path and are not backfilled with executions.
- **Container aggregate status can drift from execution truth** → status is
  derived in the same transaction as execution terminal transitions; the
  reconciler reconciles any divergence on its tick.
- **Relaxing "only reconciler writes status"** → mitigated by mandatory token
  validation on every status write; a stale writer is always rejected.
- **Clean-rebuild shares the container directory** (decided: keep in same
  folder) → old `current/` is archived to `attempts/iter-NNN/` before the clean
  run, so "clean" applies to the new run's inputs while history is preserved for
  triage.

## Migration Plan

1. Alembic `0012`: `CREATE TABLE executions` + indexes
   (`uq_executions_attempt_iter`, `one_active_execution_per_attempt`,
   `ix_executions_lease`, `ix_executions_attempt_iter`); `ALTER build_attempts
   ADD current/latest/successful_execution_id` (nullable FKs).
2. **No execution backfill** for pre-cutover `build_attempts`. Rows in flight at
   the cutover finish via the reconciler legacy path; only claims after the
   cutover use the container + execution model.
3. Cutover is guarded by a flag/timestamp so the retry rewrite can be rolled
   back to legacy `build_attempt`-minting if needed; the migration records the
   cutover instant, and `one_active_build_per_task` tolerates both judgments
   during the transition window.
4. Rollback: disable the flag (retry reverts to minting build attempts); the
   `executions` table and new FKs are additive and can remain unused.

## Open Questions

- Should `successful_execution_id` be populated by this change's publisher hook
  or deferred entirely to proposal #6's audit wiring? (Leaning: populate the
  reference here, defer the immutable audit fields to #6.)
- Heartbeat transport: a dedicated `/api/executions/{id}/heartbeat` endpoint vs.
  piggy-backing on the existing progress-event spool. (Leaning: dedicated path
  for an explicit token check; revisit if it duplicates the spool.)
