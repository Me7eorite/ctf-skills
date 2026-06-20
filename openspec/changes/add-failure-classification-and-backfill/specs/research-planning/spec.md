## MODIFIED Requirements

### Requirement: Research runs follow a strict state machine

The system SHALL model research run lifecycle with statuses `queued`, `running`, `completed`, `failed`. Normal transitions are `queued → running → (completed | failed)` and the running state SHALL NOT be skipped in the normal claim path. Failed runs SHALL carry non-empty `last_error`; completed runs SHALL have `finished_at` and null `last_error`. Queue API terminal writes SHALL require `status='running'` plus current `claimed_by` and `claim_token`, and zero matched rows SHALL raise `StaleClaimError`. The system SHALL additionally permit only `ResearchBackfillService.apply(run_id, expected_log_sha256)` (directly, from the dashboard, or from `challenge-factory research backfill --all-recoverable --apply`) to perform operator recovery `failed → completed`; the run MUST be failed, be the request's highest attempt, have no other queued/running/completed sibling, have zero sources/findings, use a log passing the backfill path/type/size/encoding and parse/quality rules, match the preview SHA-256, and be protected by transaction-scoped PostgreSQL advisory and row locks. Successful backfill SHALL set `finished_at`, clear `last_error`, and retain the confirmed `hermes_log_path`. Backfill MUST NOT perform `running → completed`; automated lease-rescue remains the only non-worker recovery of a running row inside the expired-lease row-lock path.

#### Scenario: Successful run reaches completed

- **WHEN** a worker drives a queued run to completion via claim → Hermes → persist
- **THEN** the row transitions queued → running → completed
- **AND** `finished_at` is set and `last_error` is null

#### Scenario: Hermes failure is recorded as failed

- **WHEN** the Hermes Research Agent exits non-zero or returns invalid JSON
- **THEN** the worker calls `mark_run_failed(run_id, agent_id, claim_token, last_error)`; the service decides retry vs terminal from `attempt` vs `max_attempts`
- **AND** the row status is `failed`
- **AND** `last_error` contains a non-empty diagnostic
- **AND** no `research_sources` or `research_findings` rows for this run exist

#### Scenario: Failure-and-persistence is atomic

- **WHEN** the persistence transaction raises midway through writing sources/findings
- **THEN** the transaction rolls back
- **AND** the row count in `research_sources` and `research_findings` for that run is zero
- **AND** the run remains `running` (a heartbeat will keep it alive; a subsequent worker action commits the terminal status)

#### Scenario: Stale worker cannot finalize after lease loss

- **GIVEN** worker `W-1` claimed a run with `claim_token = token-old`
- **AND** the run's lease expired and worker `W-2` recovered it as `failed`
- **AND** worker `W-2` claimed a new retry row with `claim_token = token-new`
- **WHEN** worker `W-1` later calls `mark_run_completed(run_id, 'W-1', token-old)`
- **THEN** the call raises `StaleClaimError`
- **AND** no terminal transition is written
- **AND** the expired row remains `failed`
- **AND** the retry row remains owned by `W-2`

#### Scenario: Executor swallows StaleClaimError without raising

- **GIVEN** a run whose lease expired during Hermes execution, was marked `failed` by another worker's `claim_next_run` recovery path, and now has a sibling retry row owned by that other worker
- **WHEN** the original executor finishes Hermes and calls `complete_run_with_results(...)` against the original (now `failed`) run id
- **THEN** the service raises `StaleClaimError`
- **AND** the executor catches it, logs a WARNING naming the run id and the lost claim_token, and returns without calling `mark_run_failed`
- **AND** the original executor process exits its current iteration without writing any further DB state for the lost run

#### Scenario: Operator backfills a failed run with a complete log

- **GIVEN** a run whose `status='failed'`, `hermes_log_path` points at a readable file, the file contains a complete `--- stdout ---` block, and the run has zero existing sources and findings
- **WHEN** the operator calls `ResearchBackfillService.apply(run_id, expected_log_sha256)` with the preview digest
- **THEN** sources and findings parsed from the log are persisted via the shared `_persist_rescue_payload`
- **AND** the run's `status` transitions `failed → completed`
- **AND** the run's `finished_at` is updated to the backfill commit timestamp
- **AND** the run's `last_error` is cleared
- **AND** the run's `hermes_log_path` continues to point at the parsed log
- **AND** the queue-API methods (`mark_run_completed`, `mark_run_failed`, `complete_run_with_results`, `heartbeat`) remain unable to mutate the row (it is now terminal `completed`)

#### Scenario: Backfill refuses to double-write when results already exist

- **GIVEN** a run whose `status='failed'` but which already has one or more `research_sources` rows attributed to it
- **WHEN** the operator calls `ResearchBackfillService.apply(run_id, expected_log_sha256)`
- **THEN** the call returns `already_has_results` and the database is unchanged
- **AND** the run's status remains `failed`

#### Scenario: Backfill respects the advisory lock under concurrent calls

- **GIVEN** a run that is eligible for backfill (status `failed`, log parseable, zero existing results)
- **WHEN** two concurrent apply calls with the same preview digest are issued
- **THEN** exactly one call commits sources/findings and transitions the run to `completed`
- **AND** the other call observes the new state (after acquiring the same advisory lock) and returns
  `already_completed` or `already_has_results`
- **AND** no duplicate rows exist in `research_sources` or `research_findings`

#### Scenario: Operator cannot take over a running claim

- **GIVEN** a running run with a complete log and a current worker claim
- **WHEN** an operator requests preview or apply
- **THEN** backfill returns `run_not_terminal`
- **AND** the claim and database state are unchanged

#### Scenario: Operator cannot restore a superseded attempt

- **GIVEN** a failed run with a higher-attempt, active, or completed sibling
- **WHEN** an operator requests apply
- **THEN** backfill returns `superseded_run` or `active_sibling_run`
- **AND** no result rows or files are written
