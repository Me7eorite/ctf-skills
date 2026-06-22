## 1. Queue splitting and validation

- [ ] 1.1 Add deterministic chunking for ordered build attempts.
- [ ] 1.2 Enforce a maximum of 12 attempts per sequential queue.
- [ ] 1.3 Reject duplicate build attempt ids in the same submission.
- [ ] 1.4 Reject assignment of an attempt to more than one active queue.

## 2. Queue identity and execution isolation

- [ ] 2.1 Generate a globally unique queue id for every queue instance.
- [ ] 2.2 Derive a unique worker name from the queue id.
- [ ] 2.3 Keep sequential execution unchanged inside each queue.
- [ ] 2.4 Keep fail-fast and cancellation local to the affected queue.

## 3. Queue artifacts and metadata

- [ ] 3.1 Write queue-scoped result JSON files.
- [ ] 3.2 Write queue-scoped log files.
- [ ] 3.3 Persist queue runtime metadata on disk alongside the result and log files.
- [ ] 3.4 Ensure queue metadata is sufficient for dashboard refresh and recent-history display.

## 4. Dashboard and API

- [ ] 4.1 Return queue count, queue ids, worker names, and ordered attempt ids from queue-start endpoints.
- [ ] 4.2 Keep the response schema identical for single-queue and multi-queue submissions.
- [ ] 4.3 Render one queue card per sequential queue in the progress UI.
- [ ] 4.4 Show queue status, current attempt, and processed count per card.

## 5. Verification

- [ ] 5.1 Add tests for 12-attempt chunking and ordering.
- [ ] 5.2 Add tests proving queue ids and worker names are unique.
- [ ] 5.3 Add tests proving queue result and log files do not collide.
- [ ] 5.4 Add tests proving one queue failure does not stop other queues.
- [ ] 5.5 Add tests proving the API response shape is stable for one queue and many queues.
