## Source

This change is proposal **#3 of 6** in the Worker Pool split plan (see
[`worker-pool-split-plan.md`](../../../worker-pool-split-plan.md)) and its
implementation-level design [`option-a-execution-container-design.md`](../../../option-a-execution-container-design.md).
The sibling proposal `add-agent-worker-pool-management` is the historical
superset and is kept in `openspec/changes/` as "superset, deprecated by the
split" until all 6 children archive. The 6 children land in this order:

1. `add-execution-workspace-and-profile-per-category` (folded into baseline spec)
2. `add-staged-publication-allowlist` (proposal #2)
3. `add-execution-lease-and-fencing` (this change)
4. `add-project-agent-layer-over-hermes-profiles`
5. `add-local-supervisor-and-slots`
6. `add-execution-audit-snapshots`

## Why

Today a build run is modeled as a single `build_attempts` row, and **every
retry/clean-rebuild mints a brand-new `build_attempts` row** (`attempt_id =
uuid4()`, `attempt_no + 1`). Because `derive_workspace_id` names the execution
workspace after `build_attempt_id`, each retry produces a fresh bare-UUID
top-level directory under `work/executions/`. A batch of 10 challenges that
each fail twice leaves ~30 flat, indistinguishable directories, none of which
can be triaged by challenge, and the prior failure scene is not reachable by
the next retry — so each retry runs blind to why the last one failed.

There is also no lease or fencing: liveness is purely time-based
(`BuildReconciler` marks a stuck row `lost` after a 300s grace). With more than
one worker, or a worker that crashes and recovers, a stale process can still
publish dirty output because nothing fences it at the write boundary.

This change introduces a project-side **execution row with a lease, a fencing
token, and an iteration chain**, and reverses the retry model: `build_attempt`
becomes a per-challenge **container** and retry/revision/clean-rebuild append
**executions** under it instead of minting new build attempts. This collapses
the directory explosion to one stable folder per challenge, makes the prior
attempt's failure scene reachable for the next iteration (the physical
prerequisite for revision reuse), and lets an expired worker be fenced out of
publishing even while its Hermes process is still running.

## What Changes

- **BREAKING (internal)**: retry / clean-rebuild no longer create a new
  `build_attempts` row. They `INSERT` an `executions` row under the existing
  build attempt (`iteration_no = max + 1`). `attempt_no` is allocated only on a
  fresh submit and now means "which build session for this challenge".
- New `executions` table: `id, build_attempt_id, parent_execution_id,
  iteration_no, execution_kind (initial|retry|revision), worker_id, claim_token,
  lease_expires_at, heartbeat_at, status, exit_class, started_at, finished_at,
  created_at`, with a partial-unique "one active execution per attempt" index
  and a "revision requires parent" check.
- `build_attempts` gains `current_execution_id`, `latest_execution_id`,
  `successful_execution_id` (nullable); per-run fields (`worker`, `error`) are
  superseded by the execution row, while container-level result fields stay.
- Claim is a single transaction: lock attempt → allocate `iteration_no` → mint
  `claim_token` + `lease_expires_at` → insert execution → set container status.
- Fencing: `update_to_running`, `update_to_terminal`, the publisher (proposal
  #2), and a new heartbeat path all validate the current `claim_token`; a stale
  token is rejected and its output is left in quarantine.
- `BuildReconciler` is repurposed as a **lease reaper** over executions: expired
  leases are marked `lost` and re-mint the token on recovery; the existing
  `BUILD_LOST_GRACE` (300s) becomes the default `LEASE_TTL`.
- Revision claim materializes the parent execution's output manifest, base
  artifact, and human feedback snapshot into the new workspace `current/input/`,
  reading the base artifact **in place** from the same challenge directory's
  `attempts/iter-(N-1)/` (no cross-directory or DB path lookup).
- Human feedback intake: `POST /api/build-attempts/{id}/feedback`
  (`summary` / `requested_changes` / `preserve` / `forbid` / `reviewer`) —
  schema, persistence, and materialization only.
- `revalidate` does NOT create an execution row: it appends a
  `revalidation_events` record to the existing execution.

## Capabilities

### New Capabilities
<!-- none — this change extends existing capabilities only -->

### Modified Capabilities
- `worker-pool-execution`: add execution-row lease + fencing-token requirements,
  the container/iteration model, the heartbeat lease-renewal path, the
  reconciler-as-reaper behavior, and revision-materialization-from-prior-iter.
- `build-orchestration`: build attempt becomes a per-challenge container;
  retry/clean-rebuild append executions rather than minting build attempts; add
  `current/latest/successful_execution_id` references and the feedback intake
  endpoint.

## Impact

- **Schema / migration**: Alembic `0012` — create `executions` + indexes; alter
  `build_attempts` (3 nullable execution FKs); no execution backfill for
  pre-cutover rows; cutover guarded by a flag/timestamp so in-flight legacy
  attempts finish via the reconciler legacy path.
- **Code**: `src/services/build_orchestration_service.py` (`_prepare`/`_commit`
  retry split), `src/persistence/repositories/build_attempts.py` +
  new executions repository (claim/token/lease, terminal token gate),
  `src/services/build_reconciler.py` (lease reaper), `src/persistence/models/`
  (new `executions` model), `src/hermes/workspace.py` (archive prior `current/`
  into `attempts/iter-NNN/`, materialize base artifact), the staged publisher
  from proposal #2 (pre-publish token re-check), and `/api/build-attempts/*`.
- **Dependencies**: proposal #2 (`add-staged-publication-allowlist`) for the
  shared execution workspace + publisher flow.
- **UI**: build-attempt detail / "recent completed" feeds shift from per-attempt
  rows to a container → executions timeline (the feedback UI is a later change).
