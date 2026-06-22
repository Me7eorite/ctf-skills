## Purpose

Define a build-stage sequential-queue coordinator that can run multiple ordered queues in parallel, with a maximum of 12 build attempts per queue, while exposing each queue as an independent worker in the implementation-progress UI.

This change is intentionally limited to queue orchestration, queue isolation, and progress presentation. It does not alter per-attempt build semantics, Hermes prompt content, artifact publication, or the build-failure taxonomy.

## Requirements

### Requirement: Build submissions are split into bounded sequential queues

The system SHALL split an ordered build-attempt submission into one or more sequential queues before execution.

Each sequential queue SHALL contain at most 12 build attempts.

The split algorithm SHALL preserve the original submission order.

The split algorithm SHALL be deterministic for the same ordered input.

A build attempt SHALL appear in at most one active queue at a time.

A build attempt SHALL NOT be assigned to a second active queue until the first queue reaches a terminal state or the attempt is explicitly removed from the queue before execution begins.

#### Scenario: A small submission remains a single queue

- **GIVEN** a submission containing 3 build attempts in order `A, B, C`
- **WHEN** the coordinator prepares the run
- **THEN** it creates exactly one sequential queue
- **AND** that queue contains `A, B, C` in the same order
- **AND** the queue length is 3

#### Scenario: A 12-attempt submission fits one queue exactly

- **GIVEN** a submission containing 12 build attempts
- **WHEN** the coordinator prepares the run
- **THEN** it creates exactly one sequential queue
- **AND** the queue contains all 12 attempts
- **AND** no queue exceeds the 12-attempt limit

#### Scenario: A larger submission is chunked into multiple queues

- **GIVEN** a submission containing 25 build attempts in order `A1..A25`
- **WHEN** the coordinator prepares the run
- **THEN** it creates 3 sequential queues
- **AND** queue 1 contains `A1..A12`
- **AND** queue 2 contains `A13..A24`
- **AND** queue 3 contains `A25`
- **AND** the queue order matches the original submission order

#### Scenario: Duplicate attempt ids are rejected

- **GIVEN** a submission that contains the same build attempt id twice
- **WHEN** the coordinator validates the request
- **THEN** it rejects the submission
- **AND** no queue is started
- **AND** no worker is spawned

---

### Requirement: Sequential queues run in parallel without sharing execution identity

The system SHALL execute different sequential queues as independent worker instances.

Each queue SHALL have a unique worker identity that is visible to the dashboard and progress store.

A queue worker identity SHALL NOT be reused by another active queue.

Queue execution SHALL remain sequential within the queue.

Queue execution SHALL be parallel across queues, subject to a configurable concurrency limit.

#### Scenario: Two queues start with distinct worker identities

- **GIVEN** two sequential queues prepared from one submission
- **WHEN** the coordinator starts both queues
- **THEN** each queue receives a different worker identity
- **AND** the dashboard can distinguish the two workers
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

---

### Requirement: Queue outputs and results are isolated

The system SHALL keep each sequential queue's runtime artifacts isolated from all other queues.

Each queue SHALL write to its own result record and log record.

A queue SHALL NOT overwrite another queue's runtime result, log, or progress snapshot.

If a queue fails, its failure SHALL NOT mutate another queue's runtime artifacts.

#### Scenario: Result files do not collide

- **GIVEN** two sequential queues running at the same time
- **WHEN** both queues complete or fail
- **THEN** each queue writes its own result file
- **AND** the files contain different queue identifiers
- **AND** one queue's final state does not overwrite the other queue's final state

#### Scenario: Log files do not collide

- **GIVEN** two sequential queues running at the same time
- **WHEN** both queues emit logs
- **THEN** the logs are written to separate queue-scoped paths
- **AND** the dashboard can fetch the correct log for each queue

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
- **AND** the other queues do not inherit the abort reason

#### Scenario: Cancellation stops only one queue

- **GIVEN** one sequential queue receives a cancellation signal
- **WHEN** the worker terminates
- **THEN** the queue records an interrupt-style abort reason
- **AND** other queues remain active if they were not canceled

---

### Requirement: The progress UI presents one worker card per queue

The implementation-progress UI SHALL show each sequential queue as a distinct worker-oriented progress unit.

The UI SHALL surface queue identity, worker identity, status, queue length, processed count, and the current attempt when available.

The UI SHALL keep active queues visually separable so an operator can tell which queue is running, which queue has failed, and which queue has completed.

#### Scenario: A single queue appears as one worker card

- **GIVEN** one sequential queue is running
- **WHEN** the progress UI loads
- **THEN** the UI shows one queue card
- **AND** that card shows the worker identity
- **AND** that card shows the current attempt and queue progress

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

The response SHALL include queue count, queue lengths, queue identifiers, worker identities, and the ordered attempt ids for each queue.

The response schema SHALL be the same for one queue and many queues: a top-level queue list plus queue_count.

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
- **AND** the response still contains a single queue entry in the queue list
- **AND** callers that only expect one queue can read the first entry without a special-case response type

---

## Non-Goals

- Do not change the Hermes prompt or per-attempt execution contract.
- Do not introduce an external broker or distributed job queue.
- Do not change the build artifact publication boundary.
- Do not require a database schema migration unless queue history later needs durable persistence.
