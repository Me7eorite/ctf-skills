## Rebuilt Assessment

This file consolidates the issues found during repeated review and the constraints now folded back into `proposal.md`, `design.md`, `spec.md`, and `tasks.md`.

| # | Problem found | Current implementation evidence | Remediation folded into proposal |
| - | ------------- | ------------------------------- | -------------------------------- |
| 01 | Worker identity and result files collide under parallel queues. | `TaskManager.start_sequential_worker()` uses a fixed sequential worker name and the CLI writes a fixed sequential result path. | Introduce queue ids, queue-scoped worker names, queue-scoped logs, queue-scoped metadata, and queue-scoped result output paths. |
| 02 | `TaskManager` is single-process and would reject a second sequential queue. | The local task manager holds one `_process` and returns a busy-style conflict when another task is running. | Replace only sequential queue handling with a queue-id keyed process registry; keep non-sequential task guards intact. |
| 03 | Queue metadata atomic write is insufficient for duplicate assignment. | Two different queue ids could both create metadata while referencing the same attempt. | Make attempt reservation the atomic unit: exclusive per-attempt reservation files or one locked reservation index. |
| 04 | Partial reservation can leak ownership. | A queue with attempts `A, B` can reserve `A` and fail on `B`. | Require rollback of already-created reservations before returning an error. |
| 05 | Stale metadata can block forever or allow unsafe reuse if ignored. | A subprocess can die before writing result JSON. | Treat stale reservations as active until owner exit is proven or recovery marks the queue terminal; task manager writes terminal metadata on process exit. |
| 06 | The CLI fixed result path remains a race even with unique workers. | `_run_build_attempt_sequence()` result is currently written through a constant path. | Add queue-scoped result output path options and verify two parallel sequence runners write different files. |
| 07 | Eagerly leasing all attempts recreates the known waiting-attempt lease-expiry race. | The existing sequential loop intentionally claims only the current attempt. | Preserve lazy per-attempt claim/heartbeat; the coordinator must not mark or lease waiting attempts. |
| 08 | Execution-backed retry can be broken by treating retry as a new attempt. | Execution lease/fencing model reuses `build_attempt` as container and appends executions. | Queue orchestration must not mint build attempts; retry after queued failure appends an execution under the same container. |
| 09 | Rejecting every non-terminal `latest_execution_id` is too broad. | A normal queued execution is non-terminal before worker claim. | Allow non-terminal latest only when it is the queued execution represented by the matching pending shard; reject `current_execution_id` and active reservations. |
| 10 | Iteration shard basenames can be missed. | Execution-minted shards use `{build_attempt_id}.iter-NNN.json`. | Eligibility and progress lookup accept legacy and iteration basenames. |
| 11 | “Progress worker” is ambiguous and could become a second execution actor. | Progress events already carry worker; execution terminal writes are token-fenced by runner/repository. | Define progress worker/card as observational only: aggregate metadata/events, never claim shards or write terminal state. |
| 12 | Queue failure could accidentally abort other queues. | The existing single sequence fail-fast is local, but an outer coordinator is new. | Fail-fast/cancellation/abort reason remains queue-local; coordinator does not propagate aborts across queues. |
| 13 | Dashboard could remain tied to a single global result. | Server currently reads one latest sequential-worker result. | Dashboard reads queue metadata list and renders one worker card per queue; global result file is compatibility-only. |
| 14 | API shape can drift between one and many queues. | Existing response is a flat single queue response. | Always return top-level queue list, queue_count, and applied parallel limit for both single and multi-queue submissions. |

## Outcome

The change is implementable without a full worker-pool rewrite if implementation keeps the boundary clear:

- queue coordinator owns chunking, attempt reservation, subprocess launch, queue metadata, and UI aggregation;
- CLI sequence runner owns ordered execution inside one queue;
- execution repository/runner owns claim, heartbeat, fencing, and terminal execution state;
- dashboard progress worker/card observes only.

The highest-risk areas are attempt-level reservation, queue process lifecycle convergence, and preserving execution-backed retry semantics.
