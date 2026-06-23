## Context

The current implementation already has a stable sequential execution primitive: `src/cli.py::_run_build_attempt_sequence()` processes build attempts in the supplied order and claims each attempt only when it is the next one to run. That is the correct inner loop and must remain unchanged in spirit.

The missing layer is a coordinator that can prepare independent queue instances, reserve their attempts, launch queue-scoped subprocesses, and surface each queue separately in the dashboard.

## Queue Instance

A sequential queue instance exists for one ordered batch chunk.

Suggested fields:

- `queue_id`: globally unique queue identifier
- `worker_name`: queue-scoped worker label visible in progress events and UI
- `attempt_ids`: ordered build attempt ids in this queue
- `attempt_shard_basenames`: resolved shard basenames, including `{build_attempt_id}.iter-NNN.json`
- `status`: `queued | running | done | failed | aborted`
- `current_attempt_id`
- `processed_count`
- `failed_count`
- `abort_reason`
- `result_path`
- `log_path`
- `started_at` / `finished_at`
- `process_id` and `returncode` when available

The queue instance is not a replacement for `build_attempts` or `executions`; it is an execution-time envelope around existing build-attempt containers.

## Queue Splitting

The split rule is deterministic:

- preserve the incoming order;
- chunk into groups of at most 12;
- each group becomes one sequential queue.

The existing `/api/build-attempts/queue/start` filters (`category`, `generation_request_id`, `limit`) still apply before chunking.

## Attempt Reservation

Queue preparation must reserve attempts before spawning subprocesses. Disk metadata is acceptable for the first implementation, but the atomic unit must be the attempt ownership check, not the queue metadata file.

Use one of these approaches:

- exclusive per-attempt reservation files, for example `work/logs/sequential-queues/reservations/<attempt_id>.json`;
- or a single reservation index updated under an exclusive lock.

Rules:

- create all attempt reservations before writing terminal queue-start success;
- write queue metadata only after every attempt reservation succeeds;
- roll back already-created reservations if any later reservation in the same queue fails;
- reject preparation if any active reservation already references one of the attempts;
- treat stale metadata as active until the owner process is proven exited or recovery marks the queue terminal.

Reservation is separate from execution claim. It prevents two queue-start requests from assigning the same queued attempt before the CLI sequence loop reaches that attempt.

## Execution Strategy

Keep the existing sequential runner as the queue body. Add a coordinator above it that launches multiple queue workers concurrently with a configurable parallelism limit.

Recommended default: 2 to 4 parallel queues.

The coordinator passes queue identity into the CLI runner as:

- queue-scoped worker name;
- queue-scoped result path;
- queue-scoped log path;
- optional queue metadata/progress path if needed.

The CLI currently writes sequential results to one constant path. This change must add queue-scoped CLI options for result output path; otherwise parallel queues still collide even with unique worker names.

The CLI sequence loop remains responsible for per-attempt claim, heartbeat, fail-fast classification, and terminal execution outcome recording. The coordinator must not eagerly mark or lease waiting attempts.

## Task Manager Shape

The current local `TaskManager` tracks one process in one `_process` field. Parallel sequential queues require a queue-process registry:

- non-sequential actions (`worker`, `validate`, single constrained worker) keep the existing single-task guard;
- sequential queue actions use `queue_id -> process/log/result/metadata` entries;
- starting a queue is rejected when that queue or one of its attempts is already active, not merely because another sequential queue is running;
- a global sequential concurrency limit controls how many queue processes are launched at once;
- process exit updates queue metadata to terminal state even if the CLI never writes result JSON.

## Storage Boundary

Suggested first-release layout:

```text
work/logs/sequential-queues/
  queues/<queue_id>.metadata.json
  results/<queue_id>.result.json
  logs/<queue_id>.log
  reservations/<attempt_id>.json
```

The existing `work/logs/dashboard-sequential-worker-result.json` may be retained as a legacy compatibility view for the most recent single queue, but it must not be authoritative once parallel queues are enabled.

Queue metadata must converge to terminal state on subprocess exit. A queue that fails before writing a result record still needs terminal metadata carrying return code and log path so active reservations can be released or recovered deterministically.

## Progress Worker / Card

The progress worker/card is observational only. It aggregates queue metadata plus per-attempt progress events keyed by queue worker identity.

It must not:

- claim shards;
- move queue files;
- update `executions` terminal state;
- mark `build_attempts` terminal.

## Compatibility With Execution Lease And Fencing

This change builds on the execution-backed model:

- retry and clean rebuild append an `executions` row under the same `build_attempt` container;
- staged shards may be named `{build_attempt_id}.iter-NNN.json`;
- only the running attempt in a queue is claimed and heartbeated;
- filesystem status mirroring is not authoritative for execution-backed containers.

Queue eligibility must check both the DB row and matching pending shard basename. The coordinator must not schedule an attempt whose `current_execution_id` is non-null or whose latest execution is non-terminal unless that latest execution is exactly the queued execution represented by the pending shard.
