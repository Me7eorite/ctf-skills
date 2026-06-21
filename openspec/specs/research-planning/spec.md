# research-planning Specification

## Purpose
TBD - created by archiving change add-research-planning-core. Update Purpose after archive.
## Requirements
### Requirement: Challenge categories live in a lookup table seeded with the existing trio

The system SHALL maintain a `challenge_categories` table with columns `code` (text primary key), `display_name` (text not null), and `description` (text). The Alembic revision that introduces the table SHALL seed it with at least the three rows currently supported by the shard pipeline: `("web", "Web 安全", ...)`, `("pwn", "Pwn", ...)`, `("re", "Reverse", ...)`. Adding a new category at runtime SHALL be an `INSERT INTO challenge_categories` performed without a schema migration.

#### Scenario: Lookup table is seeded after upgrade

- **WHEN** `alembic upgrade head` runs against an empty database and reaches `0002_research_tables`
- **THEN** `challenge_categories` contains at least the rows with `code` in `{"web", "pwn", "re"}`
- **AND** each seeded row has a non-empty `display_name`

#### Scenario: New category is added with a single INSERT

- **GIVEN** the lookup table seeded with `{"web", "pwn", "re"}`
- **WHEN** an operator runs `INSERT INTO challenge_categories (code, display_name, description) VALUES ('crypto', '密码学', '...')`
- **AND** afterwards calls `ResearchJobService.submit_request(category="crypto", ...)`
- **THEN** the generation request is persisted successfully
- **AND** no schema migration is required to enable the new category

### Requirement: Generation requests are scoped to a single challenge category

Every `generation_requests` row SHALL carry a required `category` column with a foreign key to `challenge_categories.code`. A request whose `category` is missing, null, or absent from `challenge_categories` SHALL be rejected by the repository before any row is written.

#### Scenario: Valid category is persisted

- **WHEN** an operator submits `category="web"`, `topic="SQL injection"` against a lookup table containing `"web"`
- **THEN** the persisted `generation_requests` row has `category="web"`

#### Scenario: Category absent from the lookup table is rejected before persistence

- **WHEN** an operator submits `category="crypto"` against a lookup table that has not been extended to include `"crypto"`
- **THEN** the repository raises a validation error naming the unknown category and the set of currently allowed codes
- **AND** no row is written to `generation_requests`

#### Scenario: Missing category is rejected before persistence

- **WHEN** an operator submits a request payload that omits `category`
- **THEN** the repository raises a validation error naming the missing field
- **AND** no row is written to `generation_requests`

#### Scenario: Listing requests can filter by category

- **WHEN** `ResearchRepository.list_generation_requests(category="re")` is called against a store containing web, pwn, and re requests
- **THEN** only the re-category requests are returned

#### Scenario: Foreign key prevents deletion of an in-use category

- **GIVEN** `challenge_categories` contains `"re"` and at least one `generation_requests` row references it
- **WHEN** `DELETE FROM challenge_categories WHERE code = 're'` is attempted
- **THEN** the database raises a foreign-key violation
- **AND** the row in `challenge_categories` is not deleted

### Requirement: Generation requests capture operator intent with validated distribution

The system SHALL persist generation requests as rows in `generation_requests` with `category` (text, fk to `challenge_categories.code`), `topic` (text), `target_count` (positive integer), `difficulty_distribution` (jsonb mapping difficulty label → count), `runtime_constraints` (jsonb), `seed_urls` (jsonb array, default empty array), `max_attempts` (positive integer, default 3), `status` (enum `draft|researching|researched|failed`), `idempotency_key` (text, nullable), `request_fingerprint` (text, nullable), and `created_at` / `updated_at` timestamps. Difficulty labels SHALL be one of `easy|medium|hard|expert`. The sum of values in `difficulty_distribution` SHALL equal `target_count`. A request whose distribution does not sum to `target_count`, or whose distribution contains an unknown label, SHALL be rejected by the repository before any row is written.

`runtime_constraints` top-level keys SHALL be limited to the following ALLOWED set, with the value-type constraints listed:

| Key                | Value type                                         |
|--------------------|----------------------------------------------------|
| `runtime`          | string                                             |
| `framework`        | string                                             |
| `language`         | string                                             |
| `compiler`         | string                                             |
| `target_format`    | string ∈ {`elf`, `wasm`, `jar`, `container`}       |
| `architecture`     | string                                             |
| `port`             | positive int (1..65535)                            |
| `mitigations`      | object mapping flag name → bool                    |
| `target_platform`  | string ∈ {`linux/amd64`, `linux/arm64`, `linux/arm`} |
| `strip`            | bool                                               |
| `experimental.*`   | namespace; values restricted to string only        |

Any other top-level key SHALL cause the request to be rejected with HTTP `400` (web) or a non-zero exit (CLI), naming the offending key. The `experimental.*` namespace (any key beginning with `experimental.`) MAY hold operator-defined string values that are passed through to prompt rendering without further validation; this exists so operators can experiment with new constraint vocabulary without blocking on a schema change.

The HTTP `POST /api/research/requests` handler SHALL extract `runtime_constraints` from the JSON body, run it through the validator, and pass the validated mapping into `ResearchJobService.submit_request`. CLI `challenge-factory research submit` SHALL accept `--runtime-constraint key=value` (repeatable) and feed the same validator. The validator SHALL be a single function in `domain/research_validators.py` shared by both entry points.

#### Scenario: Submit persists request and queues first run

- **WHEN** `ResearchJobService.submit_request(category="web", topic="SQL injection", target_count=20, difficulty_distribution={"easy":5,"medium":10,"hard":5})` is called
- **THEN** a row appears in `generation_requests` with the validated fields
- **AND** the same transaction creates the first `research_runs` row with `status="queued"`
- **AND** the returned generation request has `status="researching"`
- **AND** the operation returns the new request id

#### Scenario: Distribution mismatch is rejected before persistence

- **WHEN** an operator submits `target_count=20` with `difficulty_distribution={"easy":5,"medium":10,"hard":3}`
- **THEN** the repository raises a validation error naming the mismatch
- **AND** no row is written to `generation_requests`

#### Scenario: Unknown difficulty label is rejected

- **WHEN** an operator submits `difficulty_distribution={"easy":5,"trivial":15}`
- **THEN** the repository raises a validation error naming the unknown label
- **AND** no row is written to `generation_requests`

#### Scenario: Seed URLs are persisted for later worker execution

- **WHEN** an operator submits seed URLs `["https://example.com/a", "https://example.com/b"]`
- **THEN** the `generation_requests.seed_urls` column stores exactly those URLs
- **AND** a later worker rendering the prompt does not need any in-memory CLI state from the submit process

#### Scenario: HTTP submit accepts whitelisted runtime_constraints

- **WHEN** the body carries `runtime_constraints = {"framework":"Flask","port":9001,"strip":true}`
- **THEN** the request is created with exactly those `runtime_constraints` persisted
- **AND** the rendered Hermes prompt for this request includes `framework=Flask` and `port=9001`

#### Scenario: HTTP submit rejects unknown runtime_constraints key

- **WHEN** the body carries `runtime_constraints = {"foo":"bar"}`
- **THEN** the response is `400` naming `foo` as the unknown key
- **AND** no row is written to `generation_requests`

#### Scenario: Experimental namespace passes through unchanged

- **WHEN** the body carries `runtime_constraints = {"experimental.solver":"angr"}`
- **THEN** the request is created with that key/value persisted verbatim

### Requirement: Research runs are claimed via atomic row-locking; lease enables fault tolerance

`research_runs` SHALL carry the columns `claimed_by text`, `claim_token uuid`, `claimed_at timestamptz`, `heartbeat_at timestamptz`, `lease_expires_at timestamptz`, `attempt int not null default 1`, `parent_run_id uuid` (nullable, FK back to `research_runs.id`), and `last_error text`. While `status='queued'`, `claimed_by`, `claim_token`, `claimed_at`, `heartbeat_at`, and `lease_expires_at` are NULL.

The system SHALL expose `ResearchJobService.claim_next_run(agent_id, lease_seconds) -> ResearchRun | None`. Before claiming, the service SHALL lazily recover expired `running` rows by marking each locked expired run `failed` with non-empty `last_error`; when attempts remain, it SHALL insert a new `queued` retry row with `parent_run_id` pointing at the expired run. The expired row's `claim_token`, `claimed_by`, `claimed_at`, `heartbeat_at`, and `lease_expires_at` columns SHALL be preserved (not cleared) — they remain as forensic evidence of which worker held the timed-out attempt. The claim implementation SHALL then use `SELECT ... FOR UPDATE SKIP LOCKED LIMIT 1` to atomically pick the oldest row whose `status='queued'`, and `UPDATE` it to `status='running'`, setting `claimed_by`, a fresh `claim_token`, `claimed_at`, `heartbeat_at`, and `lease_expires_at = now() + interval lease_seconds`. The statement SHALL NOT block on any locked row.

#### Scenario: Two workers claim distinct runs in the same instant

- **GIVEN** three rows with `status='queued'` and no other workers
- **WHEN** two workers call `claim_next_run` concurrently
- **THEN** each receives a different `research_runs` row
- **AND** no row is returned to both
- **AND** each returned row has a non-null `claim_token`
- **AND** the third row remains `queued`

#### Scenario: Expired lease creates a failed audit row and retry row

- **GIVEN** a `research_runs` row with `status='running'`, `claimed_by='W-1'`, `lease_expires_at` in the past
- **WHEN** worker `W-2` calls `claim_next_run`
- **THEN** the expired row is marked `failed` with a non-empty `last_error`
- **AND** if `attempt < generation_requests.max_attempts`, a new queued retry row is created with `parent_run_id` equal to the expired row id
- **AND** worker `W-2` may claim that retry row or another queued row
- **AND** no separate reaper process is required

#### Scenario: An empty queue returns None without blocking

- **GIVEN** no rows match the claim predicate
- **WHEN** a worker calls `claim_next_run`
- **THEN** it returns `None` immediately (no waiting)

### Requirement: Heartbeats extend the lease while a worker is alive

The system SHALL expose `ResearchJobService.heartbeat(run_id, agent_id, claim_token, lease_seconds) -> bool` that updates `heartbeat_at = now()` and `lease_expires_at = now() + interval lease_seconds` only when `status='running'`, `claimed_by = agent_id`, and `claim_token` matches. The method SHALL return `True` when a row was updated and `False` when no row matched (lost lease, stale token, or row already terminal). The worker SHALL invoke this method every 30 seconds while the Hermes subprocess is running. The system SHALL NOT block on any database lock for longer than the heartbeat update.

#### Scenario: Heartbeat updates extend the lease

- **GIVEN** a worker `W-1` holds the lease on a run with `lease_expires_at = T+15min`
- **WHEN** `W-1` heartbeats at `T+5min`
- **THEN** the row's `lease_expires_at` advances to `T+5min+15min = T+20min`

#### Scenario: Heartbeat from a worker that does not own the row is a no-op

- **GIVEN** a run currently `claimed_by = W-2`
- **WHEN** worker `W-1` (which used to own the row but lost it) calls `heartbeat(run_id, 'W-1', stale_claim_token, 900)`
- **THEN** no columns are updated on that row
- **AND** the method returns `False`

#### Scenario: Matching agent id with stale claim token cannot extend a lease

- **GIVEN** a run currently `claimed_by = W-1` with `claim_token = token-new`
- **WHEN** a stale worker with the same agent id calls `heartbeat(run_id, 'W-1', token-old, 900)`
- **THEN** no columns are updated on that row
- **AND** the method returns `False`

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

### Requirement: Failed runs may be retried up to max_attempts via chained rows

The system SHALL create a new `research_runs` row when a previous run for the same generation_request fails AND `attempt < generation_request.max_attempts`. The new row SHALL have `parent_run_id = previous.id`, `attempt = previous.attempt + 1`, `status = 'queued'`. The database SHALL enforce `unique(generation_request_id, attempt)` so a request cannot have duplicate attempt numbers. The previous (failed) row SHALL remain immutable; it is the audit record of that attempt. When `attempt = max_attempts` and the run fails, NO new row is created.

#### Scenario: Failure creates a retry row

- **GIVEN** `generation_requests.max_attempts = 3`
- **AND** an existing failed `research_runs` row with `attempt = 1`
- **WHEN** the worker's failure transaction runs
- **THEN** a new `research_runs` row is inserted with `parent_run_id = previous.id`, `attempt = 2`, `status = 'queued'`
- **AND** the previous row's `status` remains `failed`, `last_error` unchanged

#### Scenario: Max attempts reached, no further row is created

- **GIVEN** `generation_requests.max_attempts = 3` and the latest run has `attempt = 3` and just failed
- **WHEN** the failure transaction runs
- **THEN** no new `research_runs` row is created
- **AND** the parent `generation_requests.status` becomes `failed`

### Requirement: Generation request status reflects the latest run

The system SHALL maintain `generation_requests.status` as a denormalized view of the request's latest run.

The status rules below assume this invariant: for each `generation_requests.id`, at most one `research_runs` row may be non-terminal (`queued` or `running`) at a time, and no runnable run may remain after a later `completed` or final `failed` run is committed. Terminal-transition code MUST preserve this invariant. If an implementation detects a historical or manually-mutated request that violates it, it SHALL treat the request as inconsistent and refuse operator-facing generation/worker-start actions with a machine-readable conflict rather than silently choosing one status rule.

| Condition                                              | request.status |
|--------------------------------------------------------|----------------|
| No runs exist                                          | `draft`        |
| Any run in `queued` or `running`                       | `researching`  |
| Latest (by created_at) run is `completed`              | `researched`   |
| Latest run is `failed`, retry row just enqueued        | `researching`  |
| Latest run is `failed`, attempt = max_attempts         | `failed`       |

Synchronization SHALL happen inside `ResearchJobService` terminal transitions. No background reconciliation is permitted. Each terminal-transition service method that updates a `research_runs` row SHALL also update its parent `generation_requests.status` in the same transaction.

There are FOUR code paths that produce a terminal `research_runs.status` and therefore must update the parent: `mark_run_completed`, `complete_run_with_results`, `mark_run_failed`, and the lease-recovery branch inside `claim_next_run`. The last one is structurally similar to `mark_run_failed`: when `claim_next_run` marks an expired `running` row as `failed`, it computes `current.attempt < max_attempts` exactly like `mark_run_failed` does and either inserts a retry row (parent stays `researching`) or sets the parent to `failed`. The implementation SHALL reuse the same private helper between `claim_next_run` and `mark_run_failed` so the rule for "request `failed` when attempts are exhausted" is single-sourced.

API responses SHALL expose TWO distinct status fields on every generation-request representation:

- `status`: the persisted `generation_requests.status` value, drawn from the four-value vocabulary above with no remapping.
- `display_status`: a derived operator-facing label drawn from the vocabulary `{draft, queued, researching, researched, failed}`. The mapping rule:
  - `status='researching'` AND latest run `status='queued'` → `display_status='queued'`
  - `status='researching'` AND latest run `status='running'` → `display_status='researching'`
  - all other cases → `display_status = status`

List filters SHALL strictly use the persisted field. `GET /api/research/requests?status=<v>` SHALL reject any value not in the persisted vocabulary with `400` and SHALL filter on `generation_requests.status` directly, NOT on the derived label. A separate parameter `?display_status=<v>` SHALL filter on the derived label using the vocabulary above.

The submit endpoint response SHALL be `{"request": <request-representation>, "latest_run": <run-representation>}`. The request representation uses the two-field `status`/`display_status` convention; the run representation uses the native `research_runs.status` vocabulary because run status is not remapped. The legacy hardcoded `"status": "queued"` top-level field is REMOVED; this is a breaking change for any caller that was reading it.

#### Scenario: Submit creates request and queued run atomically

- **WHEN** `research submit ...` is called
- **THEN** the request and first `research_runs` row are inserted in the same transaction
- **AND** `generation_requests.status` is `researching` after that transaction commits

#### Scenario: Completed run flips the parent request to researched

- **WHEN** a worker calls `mark_run_completed(run_id, agent_id, claim_token)`
- **THEN** in the same transaction, the parent `generation_requests.status` is set to `researched`

#### Scenario: Failed run with retry stays researching

- **WHEN** a worker calls `mark_run_failed(run_id, agent_id, claim_token, last_error)` for a run with `attempt < max_attempts`
- **THEN** in the same transaction: the failed row is marked, a new queued row is inserted, and the parent request stays `researching`

#### Scenario: Final failure flips the parent request to failed

- **WHEN** a worker calls `mark_run_failed(run_id, agent_id, claim_token, last_error)` for a run with `attempt = max_attempts`
- **THEN** in the same transaction the parent `generation_requests.status` is set to `failed`

#### Scenario: Submit response exposes persisted and display status separately

- **WHEN** `POST /api/research/requests` succeeds at `T`
- **THEN** the response is `{"request": {..., "status": "researching", "display_status": "queued"}, "latest_run": {..., "status": "queued"}}`
- **AND** the legacy top-level `"status"` field is absent

#### Scenario: List filter on status uses persisted field

- **GIVEN** request `R1` has `status='researching'` with latest run `queued` (display `queued`), and `R2` has `status='researching'` with latest run `running` (display `researching`)
- **WHEN** `GET /api/research/requests?status=researching` is invoked
- **THEN** both `R1` and `R2` appear in the response

#### Scenario: List filter on display_status uses derived field

- **WHEN** `GET /api/research/requests?display_status=queued` is invoked on the same fixture above
- **THEN** only `R1` appears in the response

#### Scenario: Unknown status filter value is rejected

- **WHEN** `GET /api/research/requests?status=queued` is invoked (note: `queued` is not in the persisted vocabulary)
- **THEN** the response is `400` naming `queued` as not a persisted status

### Requirement: Every research finding references at least one source

The system SHALL persist research findings as rows in `research_findings` with `kind` (enum `technique|variant|scenario|prerequisite`), `label` (text), `summary` (text), and a foreign key to `research_runs`. The system SHALL persist source references in `research_finding_sources` (join table on `finding_id` + `source_id`). The repository SHALL reject any finding submitted without at least one source reference, before any row is written. The combined insert (`research_findings` row + join rows) SHALL be atomic within a single transaction.

The repository SHALL also reject any `source_id` whose `research_run_id` differs from the finding's `run_id`. Findings may only cite sources captured for the same run.

#### Scenario: Finding with sources is persisted atomically

- **WHEN** `ResearchRepository.create_finding(run_id, kind, label, summary, source_ids=[s1, s2])` is called
- **THEN** one row in `research_findings` and two rows in `research_finding_sources` exist, all inside the same transaction

#### Scenario: Finding without sources is rejected

- **WHEN** `ResearchRepository.create_finding(run_id, kind, label, summary, source_ids=[])` is called
- **THEN** the repository raises a validation error
- **AND** no row is written to `research_findings`

#### Scenario: Finding cannot cite a source from another run

- **GIVEN** source `s1` belongs to run `A`
- **WHEN** `ResearchRepository.create_finding(run_id=B, ..., source_ids=[s1])` is called
- **THEN** the repository raises a validation error
- **AND** no row is written to `research_findings`

### Requirement: Research stage does not write to the shard queue

The system SHALL NOT, during the research stage, write any file to `work/shards/pending/`, modify `work/shards/`, or invoke `ShardQueue.split_*`. Promotion of approved candidate problems to the shard queue is the responsibility of a later approval stage.

#### Scenario: Research run does not touch the shard queue

- **WHEN** `ResearchAgentExecutor.execute(claimed_run, agent_id, lease_seconds, hermes_timeout_seconds)` runs to completion against an empty `work/shards/pending/`
- **THEN** `work/shards/pending/` remains empty
- **AND** `ShardQueue.list_pending()` returns the same set as before the run

### Requirement: Hermes Research Agent prompt contract

The system SHALL render Hermes Research Agent prompts from `prompts/research_prompt.md` containing: the challenge category code from `generation_requests.category`, the topic, the target count, the difficulty distribution, the persisted seed URLs (possibly empty), and an explicit output contract requiring a single JSON object on stdout with `sources[]` and `findings[]` arrays. The prompt SHALL instruct the Agent to keep all findings within the declared category and to refuse cross-category material rather than silently mixing it in. Every entry in `findings[]` SHALL declare `source_indices: int[]` of length ≥ 1, each value a valid 0-based index into `sources[]`. The prompt SHALL show at least one sample matching this contract. The prompt SHALL NOT hardcode the initial category trio; a category inserted into `challenge_categories` must render as the declared category value.

The Hermes Research Agent subprocess environment SHALL be assembled via an explicit ALLOWLIST. Only keys matching the allowlist below SHALL be passed to the subprocess; every other inherited variable is dropped:

- `PATH`, `HOME`, `LANG`, `LC_ALL`, `TERM`, `USER`, `SHELL`
- `HERMES_HOME`, `HERMES_TIMEOUT`, `HERMES_CMD`
- `ANTHROPIC_*` and `OPENAI_*` provider keys
- `CUSTOM_PROVIDER_*` and other Hermes legacy provider variables already handled by `hermes_process.apply_legacy_custom_provider`

Regardless of allowlist membership, any variable whose KEY (case-insensitive) contains any of `DATABASE`, `POSTGRES`, `PASSWORD`, `TOKEN`, `SECRET`, `PRIVATE_KEY` SHALL be DROPPED. This is a defense-in-depth deny rule: an operator who later widens the allowlist cannot accidentally leak persistence credentials.

#### Scenario: Rendered prompt includes the output contract

- **WHEN** the Research prompt is rendered for any generation request
- **THEN** the rendered text contains the JSON schema description for `sources` and `findings`
- **AND** the text contains a worked example whose `findings[0].source_indices` is non-empty
- **AND** the text contains the request's persisted seed URLs

#### Scenario: Hermes invocation does not touch the database

- **WHEN** the Hermes Research Agent subprocess runs
- **THEN** the subprocess environment does not include `DATABASE_URL`
- **AND** the `hermes` package source contains no import from `persistence`

#### Scenario: Subprocess env contains only allowlisted variables

- **GIVEN** the server process has `DATABASE_URL`, `POSTGRES_PASSWORD`, `FOO_TOKEN`, `PATH`, and `HERMES_HOME` set
- **WHEN** the Hermes Research Agent subprocess is invoked
- **THEN** its environment contains `PATH` and `HERMES_HOME`
- **AND** its environment does not contain `DATABASE_URL`, `POSTGRES_PASSWORD`, or `FOO_TOKEN`

#### Scenario: Deny rule survives allowlist additions

- **GIVEN** an operator adds `DATABASE_TIMEOUT` to the allowlist by mistake
- **WHEN** the Hermes Research Agent subprocess is invoked
- **THEN** the deny rule strips `DATABASE_TIMEOUT` because its key contains `DATABASE`

### Requirement: Hermes profile binding maps agent role to profile name

The system SHALL maintain an `agent_roles` lookup table (`code` text primary key, `display_name`, `description`) and a `hermes_profile_bindings` table (`role` text fk to `agent_roles.code` and primary key, `profile_name` text not null, `description`, `status` text default `'enabled'` constrained to `{enabled, disabled}`, `last_used_at`, `last_used_run_id` nullable fk to `research_runs.id` with `on delete set null`, timestamps). The Alembic revision SHALL seed `agent_roles` with `('research', '研究 Agent', ...)` and `hermes_profile_bindings` with `(role='research', profile_name='default', status='enabled')`.

The system SHALL NOT mirror Hermes profile contents (`SOUL.md`, `config.yaml`, skills, sessions, memory, cron, state DB) into PostgreSQL. Profile contents remain in `~/.hermes/profiles/<name>/`, owned by Hermes.

A research run whose binding is absent or `status = 'disabled'` SHALL be marked `failed` instead of being silently executed with `-p default`. The terminal `last_error` carries `profile_not_bound` when no binding row exists for `role = 'research'`, and `profile_disabled:<profile_name>` when the row exists but is disabled. This change removes the previous "fall back to default with WARNING" path, which silently shipped a default-profile run an operator did not request.

#### Scenario: Default binding allows research to run out of the box

- **GIVEN** the Alembic revision has been applied and no operator has modified bindings
- **WHEN** `ResearchAgentExecutor.execute(...)` is invoked
- **THEN** the spawned Hermes subprocess is invoked with `-p default`
- **AND** the run completes (assuming Hermes itself is healthy)

#### Scenario: Binding to a nonexistent profile is rejected

- **WHEN** an operator runs `challenge-factory profile bind research ctf-research-bot` and `hermes profile show ctf-research-bot` exits non-zero
- **THEN** the binding is NOT updated in the database
- **AND** the CLI prints an error naming the missing profile and suggesting `hermes profile create <name>`
- **AND** the CLI exits with a non-zero status

#### Scenario: Missing binding fails the run

- **GIVEN** no `hermes_profile_bindings` row exists for `role = 'research'`
- **WHEN** `ResearchAgentExecutor.execute(...)` is invoked
- **THEN** the run is marked `failed`
- **AND** `last_error` equals `profile_not_bound`
- **AND** no Hermes subprocess is spawned

#### Scenario: Disabled binding fails the run

- **GIVEN** `(role='research', profile_name='ctf-research-bot', status='disabled')`
- **WHEN** `ResearchAgentExecutor.execute(...)` is invoked
- **THEN** the run is marked `failed`
- **AND** `last_error` equals `profile_disabled:ctf-research-bot`
- **AND** no Hermes subprocess is spawned

#### Scenario: Profile contents are never persisted in PostgreSQL

- **WHEN** any code path in `services/` or `persistence/` runs
- **THEN** no SQL statement reads or writes `SOUL.md`, `config.yaml`, skills definitions, sessions, or memory of any Hermes profile
- **AND** no column in any project table holds raw profile content

### Requirement: Research runs record the profile used at execution time

Every `research_runs` row SHALL carry a `profile_name_used` text column. The column SHALL be written once when the runner resolves the binding for an execution attempt, and SHALL NOT be updated thereafter. The write happens before the Hermes subprocess starts and is preserved even when the attempt subsequently loses its lease — the value records "the profile this row's executor selected", not "the profile Hermes actually ran with". A subsequent change to `hermes_profile_bindings` SHALL NOT modify any historical `research_runs.profile_name_used`. Takeover (handled via expired-lease recovery + a new retry row) never overwrites this column because each retry row is a fresh `research_runs` insert.

#### Scenario: Profile used is captured per run

- **GIVEN** `(role='research', profile_name='ctf-research-bot', status='enabled')`
- **WHEN** `ResearchAgentExecutor.execute(...)` runs
- **THEN** the corresponding `research_runs.profile_name_used` equals `'ctf-research-bot'`

#### Scenario: Changing the binding does not rewrite history

- **GIVEN** a completed `research_runs` row with `profile_name_used='profile-a'`
- **WHEN** an operator rebinds `research` to `profile-b`
- **THEN** the historical row still has `profile_name_used='profile-a'`
- **AND** new runs use `'profile-b'`

### Requirement: Research artifacts on disk are tracked by path

The system SHALL store the raw fetched page text under `work/research/sources/<run_id>/<index>.txt` when raw text is captured, and the Hermes stdout/stderr log under `work/research/logs/<run_id>.log`. The corresponding `research_sources.raw_text_path` and `research_runs.hermes_log_path` columns SHALL hold these paths. PostgreSQL SHALL NOT store the raw text or the full Hermes log in any column.

Raw text writes SHALL follow a stage-then-promote lifecycle so that a DB rollback or a parse-error exception cannot leave orphaned files after normal error handling, and so crash windows are recoverable on server startup:

1. During parsing, `ResearchAgentExecutor` writes each source's raw text to `work/research/sources_staging/<run_id>/<index>.txt`.
2. `complete_run_with_results` SHALL insert and flush source/finding rows inside its terminal transaction, with `research_sources.raw_text_path` already pointing at the final path under `work/research/sources/<run_id>/`.
3. Before committing that transaction, the service SHALL atomically rename the staging directory to `work/research/sources/<run_id>/`. Any pre-existing target directory aborts the operation and rolls back the transaction.
4. The implementation MUST use explicit transaction control or equivalent transaction-helper hooks so the promotion happens before the final commit and commit failures can be caught by the service. If the DB commit fails after promotion, the service SHALL delete `work/research/sources/<run_id>/` before returning the error. If parsing, validation, stale-claim handling, or DB flush fails before promotion, the service SHALL delete `work/research/sources_staging/<run_id>/`. Cleanup is idempotent and tolerant of partial writes.
5. Server startup (`web/server.py:serve`) SHALL invoke a sweep that deletes any `work/research/sources_staging/<run_id>/` directory whose `mtime` is older than `300` seconds by default. Implementations MAY raise the effective threshold to `max(300, research_worker_manager.hermes_timeout_seconds + 60)` to avoid deleting a legitimate long-running staging directory. This threshold SHALL come from the research-worker manager's effective timeout, not the shard-oriented `HERMES_TIMEOUT` environment variable.
6. Server startup SHALL also reconcile final `work/research/sources/<run_id>/` directories: if `run_id` does not exist as a completed `research_runs` row with at least one `research_sources.raw_text_path` under that directory, the directory is treated as an orphan created by a crash window and is quarantined/deleted using the same operational-file deletion mechanism as resource deletion.

`ResourceDeletionService` SHALL include `work/research/sources/<run_id>/` in the operational-files set it quarantines/deletes when removing a generation request or its runs. The staging directory MUST also be cleaned if present.

The migration that introduces this lifecycle SHALL NOT relocate or modify any existing committed source files under `work/research/sources/<run_id>/`. The change applies only to write paths from the new version onward.

#### Scenario: Source raw text is on disk, not in the database

- **WHEN** a research run persists three sources, two of which captured raw text
- **THEN** two files exist under `work/research/sources/<run_id>/`
- **AND** the two `research_sources` rows reference those paths via `raw_text_path`
- **AND** no `research_sources` column holds the raw text

#### Scenario: Hermes log is on disk

- **WHEN** a research run completes
- **THEN** `work/research/logs/<run_id>.log` exists and is non-empty
- **AND** `research_runs.hermes_log_path` for that run equals that path

#### Scenario: DB rollback after staging leaves no orphan files

- **GIVEN** `ResearchAgentExecutor` has written source raw text to `work/research/sources_staging/<run_id>/`
- **WHEN** `complete_run_with_results` raises `ResearchValidationError`
- **THEN** after the executor returns, `work/research/sources_staging/<run_id>/` does not exist
- **AND** `work/research/sources/<run_id>/` does not exist

#### Scenario: Successful completion promotes before commit

- **WHEN** `complete_run_with_results` commits successfully
- **THEN** `work/research/sources_staging/<run_id>/` no longer exists
- **AND** `work/research/sources/<run_id>/` exists and contains every captured source's raw text
- **AND** every persisted `research_sources.raw_text_path` for that run points into the final directory

#### Scenario: Server startup sweeps stale staging directories

- **GIVEN** a stale `work/research/sources_staging/<run_id>/` directory whose mtime is older than 300 seconds (e.g. a prior crash)
- **WHEN** `web/server.py:serve` runs its startup sweep
- **THEN** that directory is deleted before HTTP traffic is accepted

#### Scenario: Server startup reconciles orphan final source directories

- **GIVEN** `work/research/sources/<run_id>/` exists from a crash after promotion
- **AND** there is no completed `research_runs` row with persisted source rows referencing that directory
- **WHEN** `web/server.py:serve` runs startup reconciliation
- **THEN** that directory is quarantined or deleted before HTTP traffic is accepted

#### Scenario: Deleting a request removes its source directory

- **GIVEN** request `R` has a completed run `Rn` with source files under `work/research/sources/Rn/`
- **WHEN** `ResourceDeletionService` deletes `R`
- **THEN** `work/research/sources/Rn/` no longer exists after the deletion settles

### Requirement: Generation requests expose governed deletion

The research request resource SHALL expose deletion through
`DELETE /api/research/requests/{id}` and through Delete actions on its dashboard
list and detail surfaces. Cascade, active-work, artifact retention, response,
and confirmation behavior SHALL conform to the `resource-deletion` capability.
Research submission, claim, retry, and read contracts SHALL remain unchanged.

#### Scenario: Request detail offers deletion

- **WHEN** the dashboard renders an existing generation request detail
- **THEN** it exposes a Delete action governed by the shared confirmation dialog
- **AND** the artifact checkbox is unchecked initially

#### Scenario: Request list offers deletion without navigation

- **WHEN** an operator activates Delete for a request row
- **THEN** the same governed confirmation is shown
- **AND** confirming deletes that row without first opening its detail view

### Requirement: Request-scoped research worker preflight and startup handshake

The dashboard backend SHALL gate `POST /api/research/requests/{request_id}/worker/start` on three preflight checks executed before any subprocess is spawned, and SHALL replace the legacy `sleep(0.2) + poll()` health probe with a handshake mechanism that distinguishes "process spawned" from "worker is actually claiming work".

Preflight (executed in a single short DB transaction):

1. `generation_requests` row with `id = request_id` MUST exist. Otherwise: `404` with `detail = "request not found"`.
2. The row's `status` MUST be `draft` or `researching`. If `researched`: `409` with `code = "already_researched"`. If `failed`: `409` with `code = "final_failure_no_retry_left"`. A `failed` parent request is already terminal by the R6 status contract and MUST NOT be treated as runnable preflight input.
3. The request MUST have at least one runnable `research_runs` row, where "runnable" means `status = 'queued'` OR `status = 'running'` with `lease_expires_at <= NOW()` (an expired lease the next claim will recover). Otherwise: `409` with `code = "no_runnable_run"`.

Handshake (after subprocess Popen succeeds):

- The worker subprocess SHALL `touch work/research/worker_handshake/<pid>.ready` immediately after it has finished imports, opened the DB session pool, and is about to call `claim_next_run`.
- The manager SHALL wait up to `5` seconds for the ready file to appear (poll cadence ≤ 100 ms).
- If the file appears within the window: return `202` with `message = "worker started"`.
- If the subprocess exits before the file appears OR the window elapses: terminate the subprocess using the platform's graceful termination API, then force-kill it after 1 s grace if it is still running. POSIX implementations MAY map this to `SIGTERM` followed by `SIGKILL`; Windows implementations MUST use the equivalent `Popen.terminate()` / `Popen.kill()` or Win32 process APIs rather than assuming POSIX signal semantics. Return `409` with `code = "worker_startup_failed"` and a `stderr_tail` of up to 1 KB.
- The ready file SHALL be unlinked by the worker on clean exit; the manager SHALL also sweep ready files for non-running PIDs on each start to avoid leaks. The PID liveness check SHALL be platform-aware: POSIX may use `kill(pid, 0)`, while Windows must use an equivalent process-existence check rather than assuming POSIX signal semantics.

The unscoped `POST /api/research/worker/start` SHALL apply the same handshake but skip the request-existence preflight (since it operates on the global queue).

#### Scenario: Missing request returns 404 with no subprocess spawned

- **GIVEN** request id `R` is not in `generation_requests`
- **WHEN** `POST /api/research/requests/R/worker/start` is invoked
- **THEN** the response is `404` with `detail = "request not found"`
- **AND** no subprocess is spawned

#### Scenario: Already researched request returns 409

- **GIVEN** request `R` has `status = "researched"`
- **WHEN** `POST /api/research/requests/R/worker/start` is invoked
- **THEN** the response is `409` with `code = "already_researched"`

#### Scenario: Final failure returns 409

- **GIVEN** request `R` has `status = "failed"` AND its latest run has `attempt = max_attempts`
- **WHEN** `POST /api/research/requests/R/worker/start` is invoked
- **THEN** the response is `409` with `code = "final_failure_no_retry_left"`

#### Scenario: Handshake success reports started

- **GIVEN** preflight passes and the worker writes its ready file within 2 s
- **WHEN** the handler observes the file
- **THEN** the response is `202` with `message = "worker started"`

#### Scenario: Handshake timeout reports failure with stderr tail

- **GIVEN** the worker imports raise an exception before writing the ready file
- **WHEN** 5 s elapse without the ready file appearing
- **THEN** the manager gracefully terminates the subprocess and force-kills it after 1 s if it is still running, using platform-appropriate process APIs
- **AND** the response is `409` with `code = "worker_startup_failed"` and `stderr_tail` containing the captured tail of `stderr`

### Requirement: Submit endpoint is idempotent under operator-supplied Idempotency-Key

`POST /api/research/requests` SHALL accept an optional `Idempotency-Key` header carrying an opaque UTF-8 string whose encoded length is ≤ 256 bytes. Oversized keys SHALL be rejected with HTTP `400`. When the header is present, the service SHALL compute a `request_fingerprint` from the normalized operator intent and then look up the most recent existing `generation_requests` row with:

- `idempotency_key = header value`
- `created_at >= NOW() - INTERVAL :ttl`

where `:ttl` defaults to `1800` seconds and MAY be overridden by environment variable `RESEARCH_SUBMIT_IDEMPOTENCY_TTL_SECONDS` (positive integer; invalid values fall back to default with a WARNING).

The `request_fingerprint` SHALL be the lower-case SHA-256 hex digest of canonical JSON over exactly these normalized body fields: `category`, `topic`, `target_count`, `difficulty_distribution`, `runtime_constraints`, `seed_urls`, and `max_attempts`. The canonical JSON SHALL sort object keys and use normalized defaults so omitted optional fields hash the same as explicit default values.

The idempotency lookup and possible request/run creation SHALL be concurrency-safe for the same non-empty `Idempotency-Key`. Before the lookup, the submit transaction SHALL acquire a transaction-scoped per-key serialization guard. The preferred implementation is a PostgreSQL advisory transaction lock derived from a stable hash of the UTF-8 key, for example a two-int `pg_advisory_xact_lock(...)` hash split; an equivalent single-row idempotency ledger table with a UNIQUE key is also acceptable. The existing non-unique `generation_requests` index is only a lookup accelerator and is NOT sufficient by itself for concurrent idempotency.

- Lookup hit with the same `request_fingerprint`: return the same submit response shape as a fresh submit, `{"request": <request-representation>, "latest_run": <run-representation>}`, with HTTP `200 OK`. No new row is created. No additional `research_runs` row is created.
- Lookup hit with a different `request_fingerprint`: reject with HTTP `409` and `code = "idempotency_key_conflict"`. No new row is created. No additional `research_runs` row is created.
- Lookup miss: the existing submit flow runs unchanged and returns the same submit response shape with HTTP `201 Created`.

When the header is absent, the existing "every call creates a new request" semantics SHALL be preserved.

The `generation_requests` table SHALL gain `idempotency_key TEXT` and `request_fingerprint TEXT` columns (both nullable) and a BTree index `(idempotency_key, created_at DESC)`. The index SHALL NOT be UNIQUE; expired (older than TTL) entries SHALL be eligible for fresh submission.

#### Scenario: Idempotency-Key hit within TTL returns existing request

- **GIVEN** an `Idempotency-Key: K` submit at time `T` created request `R`
- **WHEN** the same submit body with `Idempotency-Key: K` is re-sent at `T + 100 s`
- **THEN** the response is `200 OK` with the same request id `R`
- **AND** the response body is `{"request": {...}, "latest_run": {...}}`
- **AND** no new row appears in `generation_requests` or `research_runs`

#### Scenario: Idempotency-Key reused with different body is rejected

- **GIVEN** an `Idempotency-Key: K` submit at time `T` created request `R`
- **WHEN** a different submit body with `Idempotency-Key: K` is sent at `T + 100 s`
- **THEN** the response is `409` with `code = "idempotency_key_conflict"`
- **AND** no new row appears in `generation_requests` or `research_runs`

#### Scenario: Concurrent same-key submits serialize to one request

- **GIVEN** two identical `POST /api/research/requests` calls with `Idempotency-Key: K` arrive concurrently
- **WHEN** both transactions execute the idempotency path
- **THEN** exactly one `generation_requests` row and one initial `research_runs` row are created
- **AND** one response is `201 Created`
- **AND** the other response is `200 OK` with the same request id

#### Scenario: Idempotency-Key miss outside TTL creates new request

- **GIVEN** an `Idempotency-Key: K` submit at time `T`
- **WHEN** the same submit body with `Idempotency-Key: K` is re-sent at `T + 7200 s` (greater than default TTL)
- **THEN** the response is `201 Created` with a fresh request id

#### Scenario: Absent header preserves prior semantics

- **WHEN** two identical submit bodies are sent without any `Idempotency-Key` header
- **THEN** two distinct rows are created in `generation_requests`

### Requirement: Research output minimum quality gate

`ResearchAgentExecutor` SHALL apply a minimum-quality gate before persisting any `research_runs.status = 'completed'`. A run that fails any gate check SHALL be persisted as `failed` with a structured `last_error` value of the form `<code>:<detail>`. The codes and rules:

- `url_shape_invalid:<url>` — any `sources[].url` that does not match `^https?://[^\s]+$` or has an empty hostname.
- `content_hash_shape_invalid:<value>` — any `sources[].content_hash` not matching `^[0-9a-f]{64}$` (lower-case sha256 hex).
- `content_hash_dup:<hash>` — two or more `sources[]` entries within the same run share a `content_hash`.
- `insufficient_findings:got=<N>,need=<M>` — `findings.length < ceil(target_count * 0.5)`, where `M = ceil(target_count * 0.5)`.
- `unparseable_output:<reason>` — stdout cannot be reduced to a single JSON object (see below).

The `research_sources` table SHALL carry a UNIQUE constraint on `(research_run_id, content_hash)`, replacing the prior non-unique `ix_research_sources_run_hash` index. The Alembic migration SHALL include a pre-step that audits existing rows, fails loudly if duplicates remain, and is preceded by a `tools/scripts/dedup_research_sources.py` operator script that resolves duplicates by keeping the earliest `research_sources.id` per `(research_run_id, content_hash)` group and rewriting `research_finding_sources.source_id` from every deleted source row to the kept source row before deletion.

`ResearchAgentExecutor` stdout parsing SHALL accept arbitrary leading lines (markdown, log, banners) and SHALL extract the LAST top-level JSON object in stdout by scanning from the end: locate the final `}`, walk back matching braces (respecting string literals and escapes), and attempt `json.loads(...)` on the resulting substring. The first object that parses cleanly is the output. If no such substring parses, the run is marked `failed: unparseable_output:no_terminal_json_object`.

#### Scenario: Invalid URL fails the run with diagnostic

- **GIVEN** Hermes emits a `sources[]` entry with `url = "httpbla://broken"`
- **WHEN** the executor persists the result
- **THEN** the run is marked `failed`
- **AND** `last_error` matches `url_shape_invalid:httpbla://broken`

#### Scenario: Non-sha256 content_hash fails the run

- **GIVEN** Hermes emits a `sources[]` entry with `content_hash = "ABC"`
- **WHEN** the executor persists the result
- **THEN** the run is marked `failed`
- **AND** `last_error` starts with `content_hash_shape_invalid:`

#### Scenario: Duplicate content_hash within a run is rejected

- **GIVEN** Hermes emits two `sources[]` entries sharing the same `content_hash`
- **WHEN** the executor persists the result
- **THEN** the run is marked `failed` with `last_error` starting `content_hash_dup:`
- **AND** the DB UNIQUE constraint is the final defensive barrier if the application check is bypassed

#### Scenario: Insufficient findings fails the run

- **GIVEN** a request with `target_count = 4` and Hermes returns only 1 finding
- **WHEN** the executor persists the result
- **THEN** the run is marked `failed`
- **AND** `last_error` equals `insufficient_findings:got=1,need=2`

#### Scenario: Stdout with leading log lines parses successfully

- **GIVEN** Hermes stdout is `INFO starting agent\n... agent thought ...\n{"sources":[...],"findings":[...]}\n`
- **WHEN** the executor parses stdout
- **THEN** the terminal JSON object is selected and persistence proceeds normally

