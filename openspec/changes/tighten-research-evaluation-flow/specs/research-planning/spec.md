## ADDED Requirements

### Requirement: Request-scoped research worker preflight and startup handshake

The dashboard backend SHALL gate `POST /api/research/requests/{request_id}/worker/start` on three preflight checks executed before any subprocess is spawned, and SHALL replace the legacy `sleep(0.2) + poll()` health probe with a handshake mechanism that distinguishes "process spawned" from "worker is actually claiming work".

Preflight (executed in a single short DB transaction):

1. `generation_requests` row with `id = request_id` MUST exist. Otherwise: `404` with `detail = "request not found"`.
2. The row's `status` MUST be `draft`, `researching`, or `failed`. If `researched`: `409` with `code = "already_researched"`. If `failed` with no retry budget remaining: `409` with `code = "final_failure_no_retry_left"`.
3. The request MUST have at least one runnable `research_runs` row, where "runnable" means `status = 'queued'` OR `status = 'running'` with `lease_expires_at <= NOW()` (an expired lease the next claim will recover). Otherwise: `409` with `code = "no_runnable_run"`.

Handshake (after subprocess Popen succeeds):

- The worker subprocess SHALL `touch work/research/worker_handshake/<pid>.ready` immediately after it has finished imports, opened the DB session pool, and is about to call `claim_next_run`.
- The manager SHALL wait up to `5` seconds for the ready file to appear (poll cadence ≤ 100 ms).
- If the file appears within the window: return `202` with `message = "worker started"`.
- If the subprocess exits before the file appears OR the window elapses: kill the subprocess (`SIGTERM` then `SIGKILL` after 1 s grace), return `409` with `code = "worker_startup_failed"` and a `stderr_tail` of up to 1 KB.
- The ready file SHALL be unlinked by the worker on clean exit; the manager SHALL also sweep ready files for non-running PIDs on each start to avoid leaks.

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
- **THEN** the manager kills the subprocess (`SIGTERM`, then `SIGKILL` after 1 s)
- **AND** the response is `409` with `code = "worker_startup_failed"` and `stderr_tail` containing the captured tail of `stderr`

### Requirement: Submit endpoint is idempotent under operator-supplied Idempotency-Key

`POST /api/research/requests` SHALL accept an optional `Idempotency-Key` header carrying an opaque string ≤ 256 bytes. When the header is present, the service SHALL look up an existing `generation_requests` row matching all four of:

- `category = body.category`
- `topic = body.topic`
- `target_count = body.target_count`
- `idempotency_key = header value`

with `created_at >= NOW() - INTERVAL :ttl`, where `:ttl` defaults to `1800` seconds and MAY be overridden by environment variable `RESEARCH_SUBMIT_IDEMPOTENCY_TTL_SECONDS` (positive integer; invalid values fall back to default with a WARNING).

- Lookup hit: return the existing request representation with HTTP `200 OK`. No new row is created. No additional `research_runs` row is created.
- Lookup miss: the existing submit flow runs unchanged and returns `201 Created`.

When the header is absent, the existing "every call creates a new request" semantics SHALL be preserved.

The `generation_requests` table SHALL gain a `idempotency_key TEXT` column (nullable) and a composite BTree index `(category, topic, target_count, idempotency_key, created_at DESC)`. The index SHALL NOT be UNIQUE; expired (older than TTL) entries SHALL be eligible for fresh submission.

#### Scenario: Idempotency-Key hit within TTL returns existing request

- **GIVEN** an `Idempotency-Key: K` submit at time `T` created request `R`
- **WHEN** the same submit body with `Idempotency-Key: K` is re-sent at `T + 100 s`
- **THEN** the response is `200 OK` with the same request id `R`
- **AND** no new row appears in `generation_requests` or `research_runs`

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

The `research_sources` table SHALL carry a UNIQUE constraint on `(research_run_id, content_hash)`, replacing the prior non-unique `ix_research_sources_run_hash` index. The Alembic migration SHALL include a pre-step that audits existing rows, fails loudly if duplicates remain, and is preceded by a `tools/scripts/dedup_research_sources.py` operator script that resolves duplicates by keeping the earliest `id` per group and rewriting any `research_findings` source references away from the deleted rows.

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

## MODIFIED Requirements

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

### Requirement: Generation requests capture operator intent with validated distribution

The system SHALL persist generation requests as rows in `generation_requests` with `category` (text, fk to `challenge_categories.code`), `topic` (text), `target_count` (positive integer), `difficulty_distribution` (jsonb mapping difficulty label → count), `runtime_constraints` (jsonb), `seed_urls` (jsonb array, default empty array), `max_attempts` (positive integer, default 3), `status` (enum `draft|researching|researched|failed`), `idempotency_key` (text, nullable), and `created_at` / `updated_at` timestamps. Difficulty labels SHALL be one of `easy|medium|hard|expert`. The sum of values in `difficulty_distribution` SHALL equal `target_count`. A request whose distribution does not sum to `target_count`, or whose distribution contains an unknown label, SHALL be rejected by the repository before any row is written.

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

API responses SHALL expose TWO distinct status fields on every generation-request representation:

- `status`: the persisted `generation_requests.status` value, drawn from the four-value vocabulary above with no remapping.
- `display_status`: a derived operator-facing label drawn from the vocabulary `{draft, queued, researching, researched, failed}`. The mapping rule:
  - `status='researching'` AND latest run `status='queued'` → `display_status='queued'`
  - `status='researching'` AND latest run `status='running'` → `display_status='researching'`
  - all other cases → `display_status = status`

List filters SHALL strictly use the persisted field. `GET /api/research/requests?status=<v>` SHALL reject any value not in the persisted vocabulary with `400` and SHALL filter on `generation_requests.status` directly, NOT on the derived label. A separate parameter `?display_status=<v>` SHALL filter on the derived label using the vocabulary above.

The submit endpoint response SHALL be `{"request": <request-representation>, "latest_run": <run-representation>}` where both inner objects use the two-field `status`/`display_status` convention. The legacy hardcoded `"status": "queued"` top-level field is REMOVED; this is a breaking change for any caller that was reading it.

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

### Requirement: Research artifacts on disk are tracked by path

The system SHALL store the raw fetched page text under `work/research/sources/<run_id>/<index>.txt` when raw text is captured, and the Hermes stdout/stderr log under `work/research/logs/<run_id>.log`. The corresponding `research_sources.raw_text_path` and `research_runs.hermes_log_path` columns SHALL hold these paths. PostgreSQL SHALL NOT store the raw text or the full Hermes log in any column.

Raw text writes SHALL follow a stage-then-promote lifecycle so that a DB rollback or a parse-error exception cannot leave orphaned files:

1. During parsing, `ResearchAgentExecutor` writes each source's raw text to `work/research/sources_staging/<run_id>/<index>.txt`.
2. After `complete_run_with_results` commits successfully, the executor atomically renames the staging directory to `work/research/sources/<run_id>/`. Any pre-existing target directory aborts the operation and is logged as a reconciliation conflict.
3. Any failure path (parser error, validation error, DB rollback, stale-claim error, unexpected exception) SHALL invoke a cleanup helper that recursively deletes `work/research/sources_staging/<run_id>/`. The cleanup is idempotent and tolerant of partial writes.
4. Server startup (`web/server.py:serve`) SHALL invoke a sweep that deletes any `work/research/sources_staging/<run_id>/` directory whose `mtime` is older than `300` seconds. This recovers from a server crash during step 1 or 2.

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

#### Scenario: Successful commit promotes atomically

- **WHEN** `complete_run_with_results` commits successfully
- **THEN** `work/research/sources_staging/<run_id>/` no longer exists
- **AND** `work/research/sources/<run_id>/` exists and contains every captured source's raw text

#### Scenario: Server startup sweeps stale staging directories

- **GIVEN** a stale `work/research/sources_staging/<run_id>/` directory whose mtime is older than 300 seconds (e.g. a prior crash)
- **WHEN** `web/server.py:serve` runs its startup sweep
- **THEN** that directory is deleted before HTTP traffic is accepted

#### Scenario: Deleting a request removes its source directory

- **GIVEN** request `R` has a completed run `Rn` with source files under `work/research/sources/Rn/`
- **WHEN** `ResourceDeletionService` deletes `R`
- **THEN** `work/research/sources/Rn/` no longer exists after the deletion settles
