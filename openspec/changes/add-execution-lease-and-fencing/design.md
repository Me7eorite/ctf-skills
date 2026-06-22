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

This change is proposal #3 of the Worker Pool split. The earlier implementation
study [`option-a-execution-container-design.md`](../../../option-a-execution-container-design.md)
remains background material, but this change package is authoritative where the
study differs—specifically for queued scheduling, immutable per-execution
tokens, recovery-as-a-new-iteration, canonical `iter-NNN` paths, and the
feedback/revalidation persistence tables.

## Terminology

- `build_attempts` is the **container** row for one operator-initiated build
  session.
- `executions` are the ordered runs inside a container.
- `executions` is the source of truth for run state; `build_attempts.status`
  is a derived aggregate that mirrors the latest execution and is updated in the
  same transaction as execution transitions.

## Goals / Non-Goals

**Goals:**

- Reverse the retry model: `build_attempt` becomes a per-build-session container;
  retry / clean-rebuild / revision append `executions` rows under it.
- Collapse the workspace directory explosion to one stable folder per
  build-session container,
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
row under the existing container (`iteration_no = max + 1`). Fresh submit still
creates a new container and allocates the next `attempt_no`. Chosen over
**Option B** (keep retry = new build_attempt, execution 1:1 only for lease)
because B leaves the directory explosion and the "next retry runs blind" problem
intact, and makes `iteration_no` / `parent_execution_id` redundant with the
existing `attempt_no` / `retry_sources`. Because `derive_workspace_id` already
keys on `build_attempt_id`, A makes the workspace folder stable across a retry
chain with **no key change** — only the retry path changes.

### D2: Fencing via a per-execution claim_token, validated at every write

Scheduling inserts a `queued` execution with null worker/claim/lease fields and
updates `latest_execution_id`. Worker claim later mints `claim_token` +
`lease_expires_at` while atomically changing that row to `claimed` and setting
`current_execution_id` (reusing the existing `with_for_update()` row lock).
After claim, `update_to_running`, `update_to_terminal`, the publisher, and the
heartbeat path all require the current token; a stale token is rejected and its
output stays in quarantine. Scheduling is guarded by the container row lock;
worker writes and publication are token-gated. The lease reaper is the sole
exception: it uses a conditional update fenced by current execution id, active
status, and expired lease, never by an untrusted caller token. The "only the
reconciler changes status" invariant is relaxed to "status may change from
claim / worker / reconciler; worker writes are token-gated and reaper writes
are current-id/expiry-gated" — safety comes from explicit fences, not from
exclusivity.

### D3: Reconciler terminates the expired run; recovery is a new iteration

Rather than introduce a parallel liveness mechanism, `BuildReconciler` is
repurposed to reap expired execution leases. It terminally marks the expired
execution `lost`, clears `current_execution_id`, and leaves that execution's
token immutable. A later retry request appends a new queued `retry` execution
with the lost execution as parent; automatic retry policy remains proposal #5's
responsibility. The new worker receives a new token only when it claims that new execution. The
existing `BUILD_LOST_GRACE` (300s) becomes the default `LEASE_TTL`.

### D4: One non-terminal execution per container via a partial unique index

A partial unique index on `executions(build_attempt_id) WHERE status IN
('queued','claimed','running')` enforces a single non-terminal iteration at the
database, mirroring the
existing `one_active_build_per_task` pattern. The container-level
`one_active_build_per_task` index is retained during cutover so legacy in-flight
rows still obey the old guarantee; after cutover the execution-level unique
constraint becomes the primary active-slot rule.

### D4b: Successful publication remembers the latest canonical execution

`successful_execution_id` is a last-successful-publish pointer. Each successful
canonical rename updates it to the execution that just published; a later repair
or retry may replace it only if its own publish succeeds.

### D5: Revision base artifact is read in place from the same directory

A `revision` claim resolves its base artifact from its explicit parent at
`work/executions/<build_attempt_id>/attempts/iter-<parent.iteration_no>/output`
— a local
filesystem lookup within the same build-session container directory, not a cross-UUID copy or
a DB-path join. This is only possible because D1 keeps the retry chain in one
folder, which is why the workspace-by-session consolidation is a hard
prerequisite for revision reuse.

### D6: Revalidate is an event on the latest execution, not a new run

`revalidate` appends an event to the container's latest execution and does not
create a new `executions` row. It may update container-level aggregate fields
derived from that execution, but it never changes the execution iteration chain.

### D7: Current means active; latest means newest

Scheduling updates only `latest_execution_id`. Claim sets
`current_execution_id` to that queued latest execution. A terminal transition
must validate that row as current, then clear `current_execution_id` while
retaining `latest_execution_id`. Therefore current and latest are identical only
while an execution is claimed/running; a queued or terminal latest execution has
no current pointer.

### D8: Shard basename is per-iteration to isolate progress and resume

Each iteration's shard is named `{build_attempt_id}.iter-NNN.json` rather than a
single reused `{build_attempt_id}.json`. This was changed from the first draft
(stable reused basename) because progress events and resume cursors are keyed on
the shard string: `progress_events.shard` (TEXT) and `ProgressCursor`'s
`(shard, challenge_id)` primary key. A reused basename would let iteration 1's
progress events bleed into iteration 2's `compute_resume_plan`, making the model
treat already-attempted challenges as done — re-introducing the cross-run
contamination this whole split exists to kill, just *inside* one container.

A per-iteration basename keeps each iteration's progress events, resume cursor,
and live-tailer stream naturally scoped. Queue scanning and attribution are
unaffected because attribution is by the shard payload's top-level
`build_attempt_id`, not by basename. The immutable per-iteration snapshot is the
workspace's `attempts/iter-NNN/input/shard.json`, which is also the audit
source. The container id (the workspace folder) stays stable across iterations;
only the shard file name carries the iteration discriminator.

## Risks / Trade-offs

- **Retry semantics change is internally breaking** → guard the cutover with a
  flag/timestamp (D-migration); in-flight legacy attempts finish via the
  reconciler legacy path and are not backfilled with executions. The old and new
  active-slot rules must not both be treated as authoritative after cutover.
- **Container aggregate status can drift from execution truth** → status is
  derived in the same transaction as execution terminal transitions; the
  reconciler reconciles any divergence on its tick.
- **Relaxing "only reconciler writes status"** → mitigated by mandatory token
  validation on every status write; a stale writer is always rejected and
  rejected publishes stay quarantined rather than mutating canonical output.
- **Clean-rebuild shares the container directory** (decided: keep in same
  folder) → the entire old `current/` directory is atomically renamed to
  `attempts/iter-NNN/` before a fresh `current/` inode is created. A stale
  process retains the renamed inode as its cwd and cannot write into the new
  iteration's directory; "clean" applies to the new inputs while history is
  preserved for triage.

## Migration Plan

1. Alembic `0012`: `CREATE TABLE executions`,
   `build_feedback_snapshots`, and `revalidation_events` + indexes
   (`uq_executions_attempt_iter`, `one_nonterminal_execution_per_attempt`,
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
