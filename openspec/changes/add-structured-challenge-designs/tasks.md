## 1. Schema

- [x] 1.1 Add Alembic revision `0004_design_attempts_and_designs`
  after `0003_design_tasks`.
- [x] 1.2 Add `design_attempts` with columns described in `design.md`.
- [x] 1.3 Add `challenge_designs` with columns described in
  `design.md`, including `payload jsonb`, `summary`, `flag_format`,
  `validation_notes`, `quality_gate_passed bool`, `status text`.
- [x] 1.4 Add constraints/indexes:
  - `unique(design_task_id, attempt)` on `design_attempts`
  - `attempt > 0` on `design_attempts`
  - status check `running|completed|failed` on
    `design_attempts`
  - index `(design_task_id, status)` on `design_attempts`
  - status check `draft|accepted|superseded` on `challenge_designs`
  - partial unique
    `unique(design_task_id) WHERE status='draft'` on
    `challenge_designs`
  - FK `design_attempt_id REFERENCES design_attempts(id)
     ON DELETE RESTRICT`
- [x] 1.5 Seed a new `agent_roles` row with code `design` and a
  `hermes_profile_bindings(role='design',
  profile_name='default', status='enabled')` row.
- [x] 1.6 Down-migration drops the two tables and the seed rows;
  do not drop `agent_roles.research` or the existing research
  binding.

## 2. Domain and Validation

- [x] 2.1 Add `DesignAttempt` DTO with `DesignAttemptStatus`
  constants.
- [x] 2.2 Add `ChallengeDesign` DTO with `ChallengeDesignStatus`
  constants.
- [x] 2.3 Add `validate_design_payload(payload, parent_task)` that
  enforces the JSON schema and parent-equality rules from `design.md`
  (id/title/category/difficulty/points equality with parent,
  hints length, docker/port rules for web/pwn, no URLs in artifacts
  or validation). It also generates `challenge_designs.summary` from
  the validated payload and truncates it to <= 280 chars when needed;
  summary is not read from model JSON.
- [x] 2.4 Add `parse_design_output(stdout)` that strips ` ```json `
  fences and extracts the first balanced JSON object; raise typed
  errors on missing/invalid blocks.
- [x] 2.5 Add `run_quality_gate(payload)` that applies explicit
  in-code predicates derived from
  `skills/design-challenges/references/quality-gate.md` and returns
  `(passed: bool, notes: list[str])`; do not parse Markdown as
  executable rules.
- [x] 2.6 Tests for validate / parse / quality-gate (good and bad
  inputs covering each rule).

## 3. Prompt Wrapper

- [ ] 3.1 Add `load_design_prompt_context(paths)` in
  `src/services/design_prompt.py` to read `SKILL.md` and required
  reference files.
- [ ] 3.2 Add `build_design_prompt(context, design_task,
  generation_request, findings, sources) -> str` in the same module.
- [ ] 3.3 Keep `build_design_prompt` pure: no IO, no DB, no env reads.
- [ ] 3.4 Cap the evidence block at 20 findings (in insertion order).
- [ ] 3.5 Route category to the correct reference file (`web-design`,
  `pwn-design`, `reverse-design`, `other-categories`).
- [ ] 3.6 Always append `spec-template.md`, `quality-gate.md`, and
  (for web/pwn) `delivery-format.md` reference links.
- [ ] 3.7 Tests:
  - byte-identical output for identical inputs
  - category routing
  - evidence cap
  - presence of the output-contract JSON shape

## 4. Persistence

- [ ] 4.1 Add SQLAlchemy models `DesignAttempt`, `ChallengeDesign`.
- [ ] 4.2 Add `ChallengeDesignRepository` with methods:
  - `list_attempts(design_task_id)`
  - `get_attempt(attempt_id)`
  - `latest_attempt(design_task_id)`
  - `create_attempt(design_task_id, attempt_no, caller, profile_name)`
  - `record_prompt_path(attempt_id, claim_token, prompt_path)`
    (token-fenced; `started_at` is already set by `create_attempt`,
    this only persists where the rendered prompt was written)
  - `complete_attempt(attempt_id, claim_token, log_path, payload,
    summary, flag_format, validation_notes, quality_gate_passed)`
    (single transaction: writes attempt completed, inserts
    `challenge_designs(draft)`, sets `design_tasks.status =
    'designed'`)
  - `fail_attempt(attempt_id, claim_token, log_path, last_error,
    max_attempts)` (single transaction: marks failed and either
    sets task status `queued` for a later real retry attempt, or sets
    task status `failed`; never inserts a queued attempt placeholder)
  - `latest_design(design_task_id, status='draft')`
- [ ] 4.3 Execution-owned status writes in `create_attempt`,
  `complete_attempt`, and `fail_attempt` must update
  `design_tasks.status` directly inside their transaction and must not
  call the planning-side `DesignTaskRepository.set_design_task_status()`
  or its transition validator.
- [ ] 4.4 All writes use the supplied session, never commit themselves.
- [ ] 4.5 Postgres tests for round-trip, partial unique constraint,
  status-sync rules (`queued -> designing -> designed`, retry resets
  task without inserting an attempt placeholder, exhausted retries).

## 5. Hermes Executor

- [ ] 5.1 Add `DesignChallengeExecutor` in
  `src/services/design_agent_executor.py` modeled on
  `ResearchAgentExecutor`.
- [ ] 5.2 The executor takes `(prompt_text, profile_name,
  timeout_seconds, log_path)` and returns `(stdout, exit_code,
  duration_s)`; no DB access.
- [ ] 5.3 Reuse the existing Hermes subprocess machinery
  (`src/hermes/runner.py`) and log to
  `work/design/logs/<attempt_id>.log`.
- [ ] 5.4 Tests:
  - subprocess args (skill + profile flags)
  - timeout path produces `last_error = 'timeout'`
  - subprocess non-zero exit produces `last_error` with code

## 6. Service

- [ ] 6.1 Add `ChallengeDesignService.design_for_task(
  design_task_id, caller)` in
  `src/services/challenge_design_service.py`.
- [ ] 6.2 Step 1 - open short transaction:
  - select and lock task; require `status == 'queued'` else raise typed conflict
  - read latest attempt; require `attempt < max_attempts` or none
  - insert `design_attempts(status='running', attempt=N,
    claim_token=uuid4(), claimed_by=caller, profile_name_used=<resolved>)`
  - set `design_tasks.status = 'designing'`
  - commit
- [ ] 6.3 Step 2 - render prompt to
  `work/design/prompts/<attempt_id>.md`, persist `prompt_path` via
  `record_prompt_path`.
- [ ] 6.4 Step 3 - invoke executor with timeout from config (default
  600s); capture stdout + log path.
- [ ] 6.5 Step 4 - parse + validate + quality-gate; on success, call
  `complete_attempt`; on any failure call `fail_attempt`.
- [ ] 6.6 Resolve Hermes profile via the new `design` role binding;
  fall back to `default` profile when binding missing.
- [ ] 6.7 Service tests with a fake executor:
  - happy path: `queued -> designing -> designed` + design row inserted
  - schema-invalid: `queued -> designing -> queued` and one failed
    attempt row when `max_attempts > 1`
  - exhausted retries: `queued -> designing -> failed` (no retry)
  - timeout path
  - concurrent call returns 409 on the second caller

## 7. HTTP API

- [ ] 7.1 Add `POST /api/design-tasks/{id}/design` returning
  `{design_task_id, attempt_id, design_task_status, attempt_status,
  challenge_design|null, error|null}`.
- [ ] 7.2 Translate the typed errors:
  - task not found -> 404
  - not queued / concurrent -> 409
  - validation/timeout/Hermes error -> 200 with
    `attempt_status='failed'`, current `design_task_status`,
    and `error=<reason>`
- [ ] 7.3 Extend `GET /api/research/requests/{id}` so each
  `design_tasks[]` entry has:
  - `attempts: AttemptSummaryDict[]` ordered oldest-first
    (id, attempt, status, started_at, finished_at, last_error,
    prompt_artifact_url, log_artifact_url; no raw filesystem paths)
    `prompt_artifact_url` is
    `/api/design-attempts/<attempt_id>/artifact?kind=prompt` when
    `prompt_path` is set, otherwise null; `log_artifact_url` uses the
    same shape with `kind=log` when `hermes_log_path` is set,
    otherwise null.
  - `latest_design: ChallengeDesignDict | null`
- [ ] 7.4 Add `GET /api/design-attempts/{id}/artifact?kind={prompt|log}`
  serving the stored prompt or Hermes log for one attempt. Stored
  paths are project-relative. Resolve against the project root,
  canonicalize the candidate and allowed root, require the candidate
  to stay under `work/design/prompts/` or `work/design/logs/`, and
  reject absolute paths, traversal, symlink escapes, or prefix-only
  checks with 403; unknown `kind` with 400; missing rows/paths with
  404.
- [ ] 7.5 API tests for:
  - success returns `design_task_status='designed'`,
    `attempt_status='completed'`, `challenge_design` populated, and
    no `retry_available` key
  - 404, 409, validation failure
  - retryable failure returns `design_task_status='queued'`,
    `attempt_status='failed'`, and no `retry_available` key
  - exhausted failure returns `design_task_status='failed'`,
    `attempt_status='failed'`, and no `retry_available` key
  - retry resets the task to `queued`, a second operator trigger
    produces the second attempt, exhausted attempts returns `failed`
  - artifact endpoint: serves prompt + log, rejects traversal /
    arbitrary paths / unknown `kind`

## 8. Dashboard

- [ ] 8.1 Add collapsible Designs sub-panel under each Design Task row
  in the request detail page.
- [ ] 8.2 Header: latest attempt status pill + "Design now" button
  enabled only when task status is `queued`.
- [ ] 8.3 Attempts list: numbered rows with start/end, status,
  link to the Hermes log via the bounded design-artifact endpoint.
- [ ] 8.4 Design payload: collapsible JSON tree of
  `latest_design.payload` plus quality-gate badge.
- [ ] 8.5 No prompt body shown inline - only a "View prompt" link
  served by the bounded design-artifact endpoint for the attempt.
- [ ] 8.6 Auto-refresh the panel while any attempt is `running`.

## 9. Validation

- [ ] 9.1 Run `uv run ruff check`.
- [ ] 9.2 Run the focused app tests:
  research API, design-task planning, design prompt, design executor,
  challenge-design service, challenge-design API.
- [ ] 9.3 Run `openspec validate add-structured-challenge-designs
  --strict`.
- [ ] 9.4 Manual smoke test:
  - submit a request
  - fake-complete its research run + findings (same flow used in
    add-design-task-planning's smoke)
  - generate design tasks, queue one, hit
    `POST /api/design-tasks/{id}/design`
  - confirm UI shows the structured design and attempt history
