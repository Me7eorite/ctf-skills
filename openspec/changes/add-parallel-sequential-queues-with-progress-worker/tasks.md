## 1. Queue splitting and validation

- [ ] 1.1 Add deterministic chunking for ordered build attempts.
- [ ] 1.2 Enforce a maximum of 12 attempts per sequential queue.
- [ ] 1.3 Reject duplicate build attempt ids in the same submission.
- [ ] 1.4 Preserve current queue-start filters (`category`, `generation_request_id`, `limit`) before chunking.
- [ ] 1.5 Accept both legacy `{build_attempt_id}.json` and execution iteration `{build_attempt_id}.iter-NNN.json` pending shard basenames.
- [ ] 1.6 Reject non-null `current_execution_id`; allow non-terminal `latest_execution_id` only when it is the queued execution represented by the matching pending shard.

## 2. Attempt reservation

- [ ] 2.1 Atomically reserve attempts before spawning workers using exclusive per-attempt reservation files or a single locked reservation index.
- [ ] 2.2 Reject concurrent queue-start requests that overlap reserved attempts.
- [ ] 2.3 Roll back partial reservations when any attempt in a queue cannot be reserved.
- [ ] 2.4 Define stale-reservation recovery so abandoned queue metadata remains blocking until the owner is proven inactive or the queue is marked terminal.
- [ ] 2.5 Release or make reservations recoverable when queue metadata reaches a terminal state.

## 3. Queue identity and execution isolation

- [ ] 3.1 Generate a globally unique queue id for every queue instance.
- [ ] 3.2 Derive a unique worker name from the queue id.
- [ ] 3.3 Replace the single sequential-worker process slot with a queue-id keyed process registry while keeping existing non-sequential task guards intact.
- [ ] 3.4 Add a configurable sequential queue concurrency limit and enforce it before spawning queue workers.
- [ ] 3.5 Keep sequential execution unchanged inside each queue.
- [ ] 3.6 Keep fail-fast and cancellation local to the affected queue.
- [ ] 3.7 Do not eagerly mark running or lease waiting attempts.

## 4. CLI and artifacts

- [ ] 4.1 Add CLI sequence-runner options for queue-scoped result output paths.
- [ ] 4.2 Pass queue-scoped worker/result/log paths into the CLI sequence runner.
- [ ] 4.3 Write queue-scoped result JSON files.
- [ ] 4.4 Write queue-scoped log files.
- [ ] 4.5 Persist queue runtime metadata on disk.
- [ ] 4.6 Stop using `dashboard-sequential-worker-result.json` as the authoritative source for parallel queues; keep it only as optional compatibility alias if needed.
- [ ] 4.7 Update queue metadata to a terminal state when the subprocess exits, including when no result JSON was written.
- [ ] 4.8 Add bounded cleanup or retention for completed queue metadata/log/result records.

## 5. Dashboard and API

- [ ] 5.1 Return queue count, queue ids, worker names, queue lengths, ordered attempt ids, and applied parallel queue limit from queue-start endpoints.
- [ ] 5.2 Keep the response schema identical for single-queue and multi-queue submissions.
- [ ] 5.3 Render one queue card per sequential queue in the progress UI.
- [ ] 5.4 Show queue status, current attempt, processed count, abort reason, return code, and log path per card.
- [ ] 5.5 Aggregate card progress from queue metadata and progress events keyed by queue worker identity.
- [ ] 5.6 Keep the progress worker/card observational only: it must not claim shards, move queue files, or write execution terminal state.
- [ ] 5.7 Preserve existing single-attempt worker start behavior and existing retry/clean-rebuild API responses.

## 6. Verification

- [ ] 6.1 Add tests for 12-attempt chunking and ordering.
- [ ] 6.2 Add tests proving queue ids and worker names are unique.
- [ ] 6.3 Add tests proving execution iteration shard basenames are accepted.
- [ ] 6.4 Add tests proving waiting attempts are not marked running or leased before their turn.
- [ ] 6.5 Add tests proving active queue membership prevents duplicate assignment across concurrent start requests.
- [ ] 6.6 Add tests proving partial attempt reservations are rolled back on failure.
- [ ] 6.7 Add tests proving stale active queue metadata blocks duplicate assignment until explicit recovery.
- [ ] 6.8 Add tests proving two parallel CLI sequence runners write different result paths.
- [ ] 6.9 Add tests proving queue result and log files do not collide.
- [ ] 6.10 Add tests proving one queue failure does not stop other queues.
- [ ] 6.11 Add tests proving queue metadata reaches terminal failed state when the subprocess exits before writing a result.
- [ ] 6.12 Add tests proving retry after queued failure appends an execution under the same build_attempt container.
- [ ] 6.13 Add tests proving the progress worker/card does not mutate execution or shard state.
- [ ] 6.14 Add tests proving API response shape is stable for one queue and many queues.
