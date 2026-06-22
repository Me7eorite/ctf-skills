## Context

The current implementation already has a stable sequential execution primitive: `src/cli.py::_run_build_attempt_sequence()` processes a list of build attempts in order, claiming each attempt only when it is the next one to run. That is the correct inner loop for sequential execution.

The missing piece is an outer scheduling layer that can create multiple independent sequential queues, attach unique worker identities to them, and surface each queue separately in the dashboard progress view.

## Goals

1. Allow one build submission to fan out into multiple sequential queues.
2. Cap each sequential queue at 12 build attempts.
3. Preserve strict in-queue ordering.
4. Keep queues isolated from each other at the worker, result, and UI levels.
5. Expose queue-local progress in the implementation-progress UI.

## Proposed Model

### Sequential Queue Instance

Introduce the concept of a queue instance that exists for the duration of one ordered batch.

Suggested fields:

- `queue_id`: globally unique identifier for the batch instance
- `worker_name`: unique worker label shown in the UI and progress store
- `attempt_ids`: ordered list of build attempts in that queue
- `status`: `queued | running | done | failed | aborted`
- `current_attempt_id`: the attempt currently being processed
- `processed_count`: number of attempts completed by the queue
- `failed_count`: number of failed attempts in the queue
- `abort_reason`: optional queue-local abort reason
- `result_path`: queue-local result JSON path
- `log_path`: queue-local log path

### Queue Splitting Rule

The split rule is deterministic:

- preserve the incoming order;
- chunk into groups of at most 12;
- each group becomes one sequential queue.

This keeps the UI predictable and makes retry behavior easier to reason about.

### Isolation Rules

To prevent interference between queues:

- each queue must have a unique worker name;
- each queue must write to a unique result file;
- each queue must write to a unique log file;
- queue-local fail-fast only applies within that queue;
- a build attempt may appear in at most one active queue.

### Execution Strategy

Keep the existing sequential runner as the queue body.
Add a coordinator above it that launches multiple queue workers concurrently, with a configurable parallelism limit.

Recommended default: 2–4 parallel queues.

## API and UI Shape

### API

The queue-start endpoints should return the queue breakdown rather than a single flat worker response.

Example response shape:

```json
{
  "ok": true,
  "queue_count": 3,
  "queues": [
    {
      "queue_id": "q1",
      "worker": "dashboard-seq-q1",
      "attempt_ids": ["..."],
      "queue_length": 12
    }
  ]
}
```

### Dashboard

The implementation-progress view should render one card per queue.

Each card should show:

- worker name
- queue id
- queue length
- completed / total
- current attempt
- queue status
- abort reason if present

This is intentionally a queue-level UI, not a task-level UI.

## Storage Boundary

The first implementation SHALL persist queue runtime metadata to disk alongside the queue result and log files. The disk record SHALL be sufficient for the dashboard to refresh active queues and to display recently finished queues within a bounded retention window. PostgreSQL persistence is NOT required for the first release.

## Recommendation

Use a minimal-first implementation:

1. derive queue instances in the API/service layer;
2. keep queue runtime state on disk alongside logs;
3. use globally unique worker names per queue;
4. make the dashboard read queue metadata from queue-scoped disk state and aggregate it into a queue list;
5. add PostgreSQL queue history only if a later requirement needs durable audit or cross-restart history beyond the disk retention window.
