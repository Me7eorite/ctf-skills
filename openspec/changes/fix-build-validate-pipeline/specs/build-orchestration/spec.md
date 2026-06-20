## ADDED Requirements

### Requirement: Existing per-attempt revalidation is race-safe and recoverable

The dashboard backend SHALL retain `POST /api/build-attempts/{id}/revalidate`
for the latest failed attempt and SHALL harden its existing validation, queue,
and status updates while retaining progress events keyed by the row's
`shard_basename`.

The endpoint MUST:

- Reject any non-failed or stale sibling and preserve the current failed-shard
  identity checks.
- Prefer a valid recorded `resulting_challenge_dir`; otherwise resolve exactly
  one current directory whose metadata id matches the attributed challenge.
- Serialize the attempt with a PostgreSQL advisory lock across the validator
  subprocess; a duplicate request returns `409` before writing progress.
- Preserve the existing `dashboard-revalidate` worker and
  `validate/* → complete/*` event semantics.
- On `passed`: set `row.status = "succeeded"`, `row.error = NULL`,
  `row.artifact_status = "present"`, refresh `row.finished_at = NOW()`, and
  set parent `design_task.status = "built"`.
- On any non-passed status (`flag_mismatch`, `nonzero_exit`, `timeout`,
  `missing_validation`, `contract_failed`, etc.): set `row.status = "failed"`,
  `row.error = <validator status>`, refresh `row.finished_at = NOW()`, and set
  parent `design_task.status = "build_failed"`.
- Run the validator subprocess outside any open DB transaction while holding
  only the session-level advisory lock.
- Convert an unexpected validator exception to `validator_error`, write a
  `validate/failed` plus `complete/failed` event, and release the lock.
- Write `complete/passed` only after the shard move and database state commit.
  If the database commit fails after the shard move, restore the shard and its
  claim file to `failed/` before returning an error.

The endpoint SHALL retain its `200 OK` attempt representation on success,
return `404` when no row matches the id, and return `409` for ineligible,
concurrent, missing-shard, or validation-failure cases.

The existing `POST /api/actions/validate` endpoint and its underlying
`cli.py validate` subprocess SHALL remain available and unchanged.

#### Scenario: Revalidate flips a failed row to succeeded

- **GIVEN** build_attempt B is `failed` with a present `resulting_challenge_dir`
  and the on-disk `validate.sh` now exits `0` and prints the expected flag
- **WHEN** `POST /api/build-attempts/{B}/revalidate` is invoked
- **THEN** the response is `200` with `status="succeeded"`
- **AND** the row's `status` is `succeeded`, `error` is null, and
  `finished_at` is refreshed
- **AND** the parent design task's `status` is `built`
- **AND** exactly one `validate/running` and one `validate/passed` progress
  event are appended for the row's `shard_basename`

#### Scenario: Revalidate of a still-active attempt is rejected

- **GIVEN** build_attempt B is `running`
- **WHEN** `POST /api/build-attempts/{B}/revalidate` is invoked
- **THEN** the response is `409` and no progress event is written

#### Scenario: Revalidate of a missing failed shard is rejected

- **GIVEN** build_attempt B is `failed` but its attributed failed shard is absent
- **WHEN** `POST /api/build-attempts/{B}/revalidate` is invoked
- **THEN** the response is `409` with a message naming the missing failed shard

#### Scenario: Revalidate failure writes validate and complete terminals

- **GIVEN** the same revalidation produces a `flag_mismatch` result
- **WHEN** the endpoint completes
- **THEN** it appends `validate/running`, `validate/failed`, and
  `complete/failed` using the existing revalidate event semantics

#### Scenario: Stale sibling and concurrent duplicate are rejected

- **GIVEN** B is not the latest attempt for its design task, or another request
  is already revalidating B
- **WHEN** `POST /api/build-attempts/{B}/revalidate` is invoked
- **THEN** the response is `409` and that request starts no validator process

#### Scenario: Final database write failure restores queue placement

- **GIVEN** validation passes and the failed shard is moved to done
- **WHEN** the attempt status transaction fails to commit
- **THEN** the shard and claim file are restored under failed
- **AND** no `complete/passed` event is written

### Requirement: list_attempts progress subquery is bounded by the folded batch

The `list_attempts` repository query SHALL restrict its `progress_snapshots`
aggregation to the `shard_basename` set of the folded latest-per-task rows
selected by the outer query, rather than aggregating across every snapshot in
the table.

The query SHALL use the existing BTree primary-key index on
`progress_snapshots(shard, challenge_id)` for the restricted shard scan; it
SHALL NOT require a redundant single-column index.

This requirement is a performance contract: the `list_attempts` query's row
count read from `progress_snapshots` MUST be proportional to the number of
returned build_attempts, not to the global snapshot population.

#### Scenario: Snapshot scan size scales with returned rows

- **GIVEN** the `progress_snapshots` table holds 10000 rows across 500 shards
- **AND** the query is filtered such that only 5 build_attempts are returned
- **WHEN** the dashboard requests `GET /api/build-attempts?limit=5`
- **THEN** the executed query's `progress_snapshots` aggregation only scans
  rows belonging to those 5 shards (verifiable via `EXPLAIN ANALYZE` showing
  an index scan on `progress_snapshots(shard)`)

#### Scenario: Existing primary key index supports the scan

- **WHEN** the bounded query is explained on PostgreSQL
- **THEN** the plan may use the primary-key index whose leading column is `shard`
