## Purpose

Define a build-stage sequential-queue coordinator that can run multiple ordered queues in parallel, with a maximum of 12 build attempts per queue, while exposing each queue as an independent worker-oriented progress unit.

This change is limited to queue orchestration, queue isolation, and progress presentation. It does not alter per-attempt build semantics, Hermes prompt content, artifact publication, or build-failure taxonomy.

## Requirements

### Requirement: Build submissions are split into bounded sequential queues

The system SHALL split an ordered build-attempt submission into one or more sequential queues before execution.

Each sequential queue SHALL contain at most 12 build attempts.

The split algorithm SHALL preserve the original submission order.

The split algorithm SHALL be deterministic for the same ordered input.

Existing queue-start filters such as `category`, `generation_request_id`, and `limit` SHALL be applied before chunking.

#### Scenario: A small submission remains a single queue

- **GIVEN** a submission containing 3 build attempts in order `A, B, C`
- **WHEN** the coordinator prepares the run
- **THEN** it creates exactly one sequential queue
- **AND** that queue contains `A, B, C` in the same order

#### Scenario: A 12-attempt submission fits one queue exactly

- **GIVEN** a submission containing 12 build attempts
- **WHEN** the coordinator prepares the run
- **THEN** it creates exactly one sequential queue
- **AND** no queue exceeds the 12-attempt limit

#### Scenario: A larger submission is chunked into multiple queues

- **GIVEN** a submission containing 25 build attempts in order `A1..A25`
- **WHEN** the coordinator prepares the run
- **THEN** it creates 3 sequential queues
- **AND** queue 1 contains `A1..A12`
- **AND** queue 2 contains `A13..A24`
- **AND** queue 3 contains `A25`

---

### Requirement: Attempt eligibility and reservation prevent duplicate assignment

A build attempt SHALL appear in at most one active queue at a time.

For execution-backed build attempts, queue eligibility SHALL accept pending shard basenames of either `{build_attempt_id}.json` or `{build_attempt_id}.iter-NNN.json`, where `NNN` is a positive zero-padded iteration number.

The coordinator SHALL NOT mint a new `build_attempts` row when preparing queue membership.

The coordinator SHALL create attempt reservations atomically before spawning queue workers.

Attempt reservation SHALL be atomic across different queue ids, either by exclusive per-attempt reservation files or by a single locked reservation index.

A queue metadata record SHALL include the ordered attempt ids and their resolved shard basenames.

If queue preparation cannot reserve every attempt in a queue, it SHALL roll back any reservations created for that queue and SHALL NOT spawn a worker.

A stale queue reservation SHALL remain blocking until the system can prove that its owning process is no longer active or a recovery action marks the queue terminal.

#### Scenario: Duplicate attempt ids are rejected

- **GIVEN** a submission that contains the same build attempt id twice
- **WHEN** the coordinator validates the request
- **THEN** it rejects the submission
- **AND** no queue is started

#### Scenario: Execution iteration shard is eligible

- **GIVEN** build attempt `A` is queued
- **AND** its pending shard basename is `A.iter-002.json`
- **WHEN** the coordinator validates a queue containing `A`
- **THEN** the attempt is accepted as eligible
- **AND** the queue records `A.iter-002.json` as the attempt's shard basename

#### Scenario: Active execution-backed attempt is not double-assigned

- **GIVEN** build attempt `A` has `current_execution_id` set
- **WHEN** another queue-start request includes `A`
- **THEN** the request is rejected
- **AND** no second queue is started for `A`

#### Scenario: Queued latest execution remains eligible

- **GIVEN** build attempt `A` has a non-terminal latest execution in `queued`
- **AND** pending shard `A.iter-002.json` represents that queued execution
- **WHEN** a queue-start request includes `A`
- **THEN** the coordinator may reserve `A`
- **AND** it does not reject `A` merely because `latest_execution_id` is non-null

#### Scenario: Concurrent queue starts cannot reserve the same attempt

- **GIVEN** two queue-start requests both include build attempt `A`
- **WHEN** both requests prepare queue metadata concurrently
- **THEN** exactly one request creates an attempt reservation for `A`
- **AND** the other request is rejected before spawning a worker

#### Scenario: Partial reservation rolls back

- **GIVEN** a queue-start request tries to reserve attempts `A, B`
- **AND** reserving `A` succeeds
- **AND** reserving `B` fails because another active queue owns it
- **WHEN** the coordinator rejects the request
- **THEN** the reservation for `A` is removed
- **AND** no worker is spawned for the partial queue

#### Scenario: Stale reservation remains blocking until recovered

- **GIVEN** queue `Q` reserved build attempt `A`
- **AND** queue `Q` has no terminal metadata
- **WHEN** another queue-start request includes `A`
- **THEN** the request is rejected unless recovery has first marked `Q` terminal

---

### Requirement: Sequential queues run in parallel without sharing execution identity

The system SHALL execute different sequential queues as independent worker instances.

Each queue SHALL have a unique worker identity visible to the dashboard and progress store.

A queue worker identity SHALL NOT be reused by another active queue.

Queue execution SHALL remain sequential within the queue.

Queue execution SHALL be parallel across queues, subject to a configurable concurrency limit.

The system SHALL claim and heartbeat only the attempt currently being processed within a queue.

The system SHALL NOT eagerly mark every attempt in a sequential queue as running when the queue starts.

The CLI sequence runner SHALL support queue-scoped result output paths so parallel queues do not write to a shared result file.

#### Scenario: Two queues start with distinct worker identities

- **GIVEN** two sequential queues prepared from one submission
- **WHEN** the coordinator starts both queues
- **THEN** each queue receives a different worker identity
- **AND** progress events written by one worker are not attributed to the other

#### Scenario: Queue-local sequential ordering is preserved

- **GIVEN** a queue containing attempts `A, B, C`
- **WHEN** the worker runs the queue
- **THEN** it processes `A` before `B`
- **AND** `B` before `C`
- **AND** it does not claim `B` until `A` has finished its turn

#### Scenario: Queue-level concurrency is bounded

- **GIVEN** 5 sequential queues are ready to run
- **AND** the configured parallel queue limit is 2
- **WHEN** the coordinator starts execution
- **THEN** no more than 2 queues run at the same time
- **AND** the remaining queues wait for an execution slot

#### Scenario: Waiting attempts are not leased

- **GIVEN** a queue contains attempts `A, B`
- **WHEN** the worker starts processing `A`
- **THEN** only `A` is claimed and heartbeated
- **AND** `B` remains queued until `A` finishes its turn

#### Scenario: Parallel queues write result paths supplied by coordinator

- **GIVEN** queue A is launched with result path `A.result.json`
- **AND** queue B is launched with result path `B.result.json`
- **WHEN** both CLI sequence runners complete
- **THEN** queue A writes only `A.result.json`
- **AND** queue B writes only `B.result.json`

---

### Requirement: Queue outputs, metadata, and lifecycle are isolated

The system SHALL keep each sequential queue's runtime artifacts isolated from all other queues.

Each queue SHALL write to its own result record, log record, and metadata record.

A queue SHALL NOT overwrite another queue's runtime result, log, metadata, or progress snapshot.

The legacy single result file `dashboard-sequential-worker-result.json` SHALL NOT be the authoritative source for parallel sequential queues.

When a queue subprocess exits, queue metadata SHALL be updated to a terminal state even if the subprocess did not write a result JSON file.

#### Scenario: Result files do not collide

- **GIVEN** two sequential queues running at the same time
- **WHEN** both queues complete or fail
- **THEN** each queue writes its own result file
- **AND** one queue's final state does not overwrite the other queue's final state

#### Scenario: Log files do not collide

- **GIVEN** two sequential queues running at the same time
- **WHEN** both queues emit logs
- **THEN** the logs are written to separate queue-scoped paths
- **AND** the dashboard can fetch the correct log for each queue

#### Scenario: Process exits before writing result

- **GIVEN** a queue subprocess exits with a non-zero return code before creating its result JSON
- **WHEN** the task manager observes the exit
- **THEN** the queue metadata records a terminal failed state
- **AND** the metadata includes the return code and log path
- **AND** the queue's attempt reservations are eligible for recovery according to the stale-reservation policy

#### Scenario: A failed queue does not corrupt a healthy queue

- **GIVEN** queue 1 fails due to a local infrastructure error
- **AND** queue 2 is still running successfully
- **WHEN** queue 1 transitions to a terminal failed state
- **THEN** queue 2 continues running
- **AND** queue 2's result file remains unchanged
- **AND** queue 2's worker identity remains valid

---

### Requirement: Queue-local failure handling does not cross queue boundaries

The system SHALL apply fail-fast and cancellation behavior only within the queue that experienced the event.

An abort reason from one queue SHALL NOT abort other active queues.

A cancellation signal SHALL terminate only the affected queue worker and its remaining local attempts.

#### Scenario: Infra fail-fast aborts only the current queue

- **GIVEN** a sequential queue reaches its configured infra fail-fast threshold
- **WHEN** the current queue aborts with `consecutive_infra`
- **THEN** only the remaining attempts in that queue are marked aborted
- **AND** other queues continue running

#### Scenario: Cancellation stops only one queue

- **GIVEN** one sequential queue receives a cancellation signal
- **WHEN** the worker terminates
- **THEN** the queue records an interrupt-style abort reason
- **AND** other queues remain active if they were not canceled

---

### Requirement: The progress UI presents one worker card per queue

The implementation-progress UI SHALL show each sequential queue as a distinct worker-oriented progress unit.

The UI SHALL surface queue identity, worker identity, status, queue length, processed count, current attempt, abort reason, return code, and log path when available.

The UI SHALL aggregate queue cards from queue metadata plus progress events keyed by queue worker identity.

The progress worker/card SHALL be an observer and SHALL NOT claim shards, update execution terminal state, or move queue files.

#### Scenario: Multiple queues appear as multiple cards

- **GIVEN** three sequential queues are active or recently finished
- **WHEN** the progress UI loads
- **THEN** the UI shows three queue cards
- **AND** each card is labeled with its own worker identity
- **AND** each card shows its own status and progress summary

#### Scenario: Queue progress updates stay attached to the correct queue

- **GIVEN** queue A and queue B are both active
- **WHEN** queue A advances to the next attempt
- **THEN** only queue A's card updates its current-attempt display
- **AND** queue B's card remains unchanged unless queue B itself advances

---

### Requirement: The public queue-start API reports queue breakdowns

The queue-start API SHALL return the queue breakdown that will be executed.

The response SHALL include queue count, queue lengths, queue identifiers, worker identities, ordered attempt ids, and the applied parallel queue limit.

The response schema SHALL be the same for one queue and many queues.

The response SHALL preserve queue ordering.

#### Scenario: The API returns queue metadata for a chunked submission

- **GIVEN** a submission that becomes 2 sequential queues
- **WHEN** the queue-start endpoint accepts the request
- **THEN** the response includes `queue_count = 2`
- **AND** the response includes both queue identifiers
- **AND** the response includes each queue's worker identity
- **AND** the response includes the ordered attempt ids for each queue

#### Scenario: The API preserves single-queue compatibility

- **GIVEN** a submission that fits within one queue
- **WHEN** the queue-start endpoint accepts the request
- **THEN** the response still uses the same queue metadata shape
- **AND** the queue count is 1

---

### Requirement: Queue orchestration remains compatible with execution-backed retry

Queue orchestration SHALL treat `build_attempts` as build-session containers and SHALL treat `executions` as per-run state when execution minting is enabled.

Retrying or clean-rebuilding a build attempt that previously ran through a sequential queue SHALL append a new execution under the same build attempt container.

The top-level execution workspace id SHALL remain the build attempt container id across queue retry iterations.

#### Scenario: Retry after queued execution does not create a new container

- **GIVEN** build attempt `A` was processed by a sequential queue and failed
- **WHEN** the operator retries `A`
- **THEN** the system schedules a new execution under build attempt `A`
- **AND** no new `build_attempts` row is created
- **AND** the new pending shard uses an iteration basename such as `A.iter-002.json`

#### Scenario: Queue execution does not revive a terminal fenced execution

- **GIVEN** build attempt `A` has a terminal latest execution
- **WHEN** a retry schedules `A.iter-002.json`
- **THEN** the queue claims the new queued execution for iteration 2
- **AND** it does not mutate the terminal row for iteration 1

---

## Non-Goals

- Do not change the Hermes prompt or per-attempt execution contract.
- Do not introduce an external broker or distributed job queue.
- Do not change the build artifact publication boundary.
- Do not require a database schema migration unless queue history later needs durable persistence.
