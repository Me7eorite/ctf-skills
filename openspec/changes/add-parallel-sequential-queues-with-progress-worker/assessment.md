## 12-pass proposal assessment

Each pass follows: analyze proposal -> compare current implementation -> choose solution -> fold back into the proposal.

| Pass | Analysis | Current implementation check | Resolution folded into proposal |
| ---- | -------- | ---------------------------- | -------------------------------- |
| 01 | The proposal needs an explicit source of truth for queue identity; otherwise worker labels can collide across batches. | `TaskManager.start_sequential_worker()` currently hardcodes `dashboard-sequential-01` and the CLI runner has no queue-instance concept. | Introduce a queue instance / queue id and derive unique worker names from it. |
| 02 | A 12-attempt cap must be enforced at the API boundary, not only inside the scheduler, or clients can still submit oversized batches. | `src/web/build_attempts_endpoints.py` currently forwards the full ordered list directly to the sequential worker. | Add validation and chunking at the queue-start endpoint and keep a service-side guard. |
| 03 | If the same build attempt can be referenced by two queues, the system can create duplicate work and confusing progress states. | The current endpoint only checks duplicate ids inside a single request, not cross-queue ownership. | Require a queue-preparation step that rejects attempts already assigned to another active queue. |
| 04 | Parallel queues need bounded concurrency or the dashboard can turn into an unbounded process spawner. | `TaskManager` currently tracks a single `_process` and therefore implicitly serializes everything. | Add an outer coordinator with a configurable concurrency limit rather than launching all queues at once. |
| 05 | Queue-local worker identity must be reflected in progress events, or the UI cannot separate queues reliably. | `ProgressEventInput.worker` already exists, but the current sequential launcher writes a fixed worker name. | Reuse the existing progress schema and make the worker name queue-scoped. |
| 06 | A single shared result file would race under parallel execution and overwrite neighboring queue status. | `src/web/server.py` reads one latest sequential-worker result file only. | Move to queue-scoped result and log files, and make the UI aggregate them. |
| 07 | Queue-local fail-fast should not become a batch-level kill switch. | `_run_build_attempt_sequence()` already keeps fail-fast state local to one sequence. | Keep fail-fast inside the queue body and ensure the coordinator never propagates aborts across queues. |
| 08 | The progress UI may need a richer shape than a single worker blob, otherwise it will still flatten multiple queues into one surface. | The current dashboard UI state only exposes one `sequential_worker_result`. | Return and render a queue list so each queue gets its own card or row. |
| 09 | Automatic chunking can change UX expectations for existing callers that assume one request maps to one worker launch. | The existing API returns a single accepted response with one `queue_length`. | Preserve a stable response shape while expanding it to include queue metadata and count. |
| 10 | Retry and resume semantics could become ambiguous if a build attempt is already assigned to a queue. | Build-attempt retry logic currently reasons about the latest attempt, not queue membership. | Make queue assignment an execution-only concern and reject re-queueing of active attempts until the prior queue resolves. |
| 11 | The queue coordinator could accidentally treat one queue's abort as a global failure and stop scheduling the remaining chunks. | There is no outer coordinator today, so no explicit cross-queue abort policy exists. | Define queue-local terminal states and make the coordinator continue scheduling unaffected queues. |
| 12 | The proposal should avoid unnecessary schema churn unless the UI needs durable queue history. | Current progress and build attempt storage already support per-worker display without a queue table. | Keep persistence minimal first: use queue-scoped files and existing progress events, and add a queue table only if history becomes necessary. |

## Assessment Outcome

The current implementation can support this change without a full rewrite, but only if the proposal explicitly introduces a queue instance concept, queue-scoped worker identity, queue-scoped artifacts, and bounded parallel scheduling.

The highest-risk areas are:

1. preserving the existing sequential execution semantics inside each queue;
2. avoiding worker/result-file collisions;
3. preventing cross-queue abort propagation;
4. keeping the UI understandable when multiple queue cards are active at once.

If those constraints are kept, the change is a natural extension of the current build orchestration model rather than a new execution system.
