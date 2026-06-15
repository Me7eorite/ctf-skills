## ADDED Requirements

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

The system SHALL persist generation requests as rows in `generation_requests` with `category` (text, fk to `challenge_categories.code`), `topic` (text), `target_count` (positive integer), `difficulty_distribution` (jsonb mapping difficulty label → count), `runtime_constraints` (jsonb), `seed_urls` (jsonb array, default empty array), `max_attempts` (positive integer, default 3), `status` (enum `draft|researching|researched|failed`), and `created_at` / `updated_at` timestamps. Difficulty labels SHALL be one of `easy|medium|hard|expert`. The sum of values in `difficulty_distribution` SHALL equal `target_count`. A request whose distribution does not sum to `target_count`, or whose distribution contains an unknown label, SHALL be rejected by the repository before any row is written.

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

The system SHALL model research run lifecycle with statuses `queued`, `running`, `completed`, `failed`. Transitions: `queued → running → (completed | failed)`. The `running` state SHALL NOT be skipped. A run that enters `failed` SHALL carry a non-empty `last_error`. A run that enters `completed` SHALL have `finished_at` set and `last_error` null. Once a run reaches `completed` or `failed`, no column SHALL change through the queue API — `heartbeat`, `mark_run_completed`, `mark_run_failed`, and `complete_run_with_results` all gate their UPDATEs on `status='running'`, so a stale write to a terminal row is structurally impossible.

Terminal transitions SHALL require `status='running'` plus the current `claimed_by` and `claim_token`. A worker whose original run was recovered after lease expiry — meaning `claim_next_run`'s recovery path has marked that row `failed`, possibly with a sibling retry row now owned by a different worker — SHALL NOT be able to mark the old run completed or failed, even if it later receives a valid Hermes result. The token-fenced service methods (`mark_run_completed`, `mark_run_failed`, `complete_run_with_results`) SHALL raise `StaleClaimError` when the WHERE clause matches zero rows, so the executor can branch on a typed exception rather than inspecting return values.

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

The system SHALL maintain `generation_requests.status` as a denormalized view of the request's latest run:

| Condition                                              | request.status |
|--------------------------------------------------------|----------------|
| No runs exist                                          | `draft`        |
| Any run in `queued` or `running`                       | `researching`  |
| Latest (by created_at) run is `completed`              | `researched`   |
| Latest run is `failed`, retry row just enqueued        | `researching`  |
| Latest run is `failed`, attempt = max_attempts         | `failed`       |

Synchronization SHALL happen inside `ResearchJobService` terminal transitions. No background reconciliation is permitted. Each terminal-transition service method that updates a `research_runs` row SHALL also update its parent `generation_requests.status` in the same transaction.

There are FOUR code paths that produce a terminal `research_runs.status` and therefore must update the parent: `mark_run_completed`, `complete_run_with_results`, `mark_run_failed`, and the lease-recovery branch inside `claim_next_run`. The last one is structurally similar to `mark_run_failed`: when `claim_next_run` marks an expired `running` row as `failed`, it computes `current.attempt < max_attempts` exactly like `mark_run_failed` does and either inserts a retry row (parent stays `researching`) or sets the parent to `failed`. The implementation SHALL reuse the same private helper between `claim_next_run` and `mark_run_failed` so the rule for "request `failed` when attempts are exhausted" is single-sourced.

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

#### Scenario: Rendered prompt includes the output contract

- **WHEN** the Research prompt is rendered for any generation request
- **THEN** the rendered text contains the JSON schema description for `sources` and `findings`
- **AND** the text contains a worked example whose `findings[0].source_indices` is non-empty
- **AND** the text contains the request's persisted seed URLs

#### Scenario: Hermes invocation does not touch the database

- **WHEN** the Hermes Research Agent subprocess runs
- **THEN** the subprocess environment does not include `DATABASE_URL`
- **AND** the `hermes` package source contains no import from `persistence`

### Requirement: Hermes profile binding maps agent role to profile name

The system SHALL maintain an `agent_roles` lookup table (`code` text primary key, `display_name`, `description`) and a `hermes_profile_bindings` table (`role` text fk to `agent_roles.code` and primary key, `profile_name` text not null, `description`, `status` text default `'enabled'` constrained to `{enabled, disabled}`, `last_used_at`, `last_used_run_id` nullable fk to `research_runs.id` with `on delete set null`, timestamps). The Alembic revision SHALL seed `agent_roles` with `('research', '研究 Agent', ...)` and `hermes_profile_bindings` with `(role='research', profile_name='default', status='enabled')`.

The system SHALL NOT mirror Hermes profile contents (`SOUL.md`, `config.yaml`, skills, sessions, memory, cron, state DB) into PostgreSQL. Profile contents remain in `~/.hermes/profiles/<name>/`, owned by Hermes.

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

#### Scenario: Disabled binding falls back to default with a warning

- **GIVEN** `(role='research', profile_name='ctf-research-bot', status='disabled')`
- **WHEN** `ResearchAgentExecutor.execute(...)` is invoked
- **THEN** the run is invoked with `-p default`
- **AND** a WARNING is logged naming the disabled binding

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

#### Scenario: Source raw text is on disk, not in the database

- **WHEN** a research run persists three sources, two of which captured raw text
- **THEN** two files exist under `work/research/sources/<run_id>/`
- **AND** the two `research_sources` rows reference those paths via `raw_text_path`
- **AND** no `research_sources` column holds the raw text

#### Scenario: Hermes log is on disk

- **WHEN** a research run completes
- **THEN** `work/research/logs/<run_id>.log` exists and is non-empty
- **AND** `research_runs.hermes_log_path` for that run equals that path
