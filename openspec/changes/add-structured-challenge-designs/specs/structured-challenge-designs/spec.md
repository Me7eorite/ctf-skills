## ADDED Requirements

### Requirement: Design attempts and challenge designs are database-backed

The system SHALL persist every invocation of the design-challenges skill
as one row in `design_attempts`, and every validated structured output
as one row in `challenge_designs`. Each `design_attempts` row SHALL
reference exactly one parent `design_tasks` row via `design_task_id`.
Each `challenge_designs` row SHALL reference exactly one producing
`design_attempts` row via `design_attempt_id` and the same parent task
via `design_task_id`.

`design_attempts` SHALL store: `attempt` (1-based integer unique per
task), `status` in `{running, completed, failed}`, `claimed_by`,
`claim_token`, `started_at`, `finished_at`, `profile_name_used`,
`prompt_path`, `hermes_log_path`, `last_error`, and audit timestamps.
The retry chain is reconstructed from `(design_task_id, attempt)`
ordering; there is no `parent_attempt_id` column.

`challenge_designs` SHALL store: the full validated JSON object in
`payload jsonb`, a short `summary` text (<= 280 chars), `flag_format`,
`validation_notes`, `quality_gate_passed boolean`, `status` in
`{draft, accepted, superseded}` (this change writes only `draft`), and
audit timestamps.

The database SHALL enforce `unique(design_task_id) WHERE status =
'draft'` on `challenge_designs` so a design task can have at most one
live draft design.

#### Scenario: Successful attempt produces one attempt row and one design row

- **WHEN** the operator triggers a design attempt against a queued
  design task and the skill returns valid JSON
- **THEN** exactly one row is inserted into `design_attempts` with
  `status = 'completed'`
- **AND** exactly one row is inserted into `challenge_designs` with
  `status = 'draft'` and `design_attempt_id` referencing that attempt

#### Scenario: At most one draft challenge_design per task

- **GIVEN** a design task already has a `challenge_designs` row with
  `status = 'draft'`
- **WHEN** another row with `(design_task_id, status='draft')` is
  inserted
- **THEN** the database rejects the insert via the partial unique
  constraint

### Requirement: Prompt is rendered deterministically from skill + research evidence

The system SHALL render the Hermes prompt for each attempt as a single
Markdown file under `work/design/prompts/<attempt_id>.md`. The
rendered prompt SHALL include, in this order:

1. A header pinning the `design-challenges` skill.
2. An event-brief block synthesized from the parent
   `generation_requests` row (topic, category, runtime_constraints,
   max_attempts).
3. A single-challenge block synthesized from the parent
   `design_tasks` row (challenge_id, title, category, difficulty,
   points, port, primary_technique, learning_objective, scenario,
   constraints).
4. An evidence block listing each cited `research_findings` row's
   label, kind, summary, and source URLs, capped at the first 20
   findings.
5. A category-specific reference link, chosen by parent category:
   `web -> web-design.md`, `pwn -> pwn-design.md`,
   `re -> reverse-design.md`, otherwise `other-categories.md`.
6. The always-on references `spec-template.md` and `quality-gate.md`,
   plus `delivery-format.md` for `web`/`pwn` parents only.
7. An output-contract block instructing the model to emit exactly the
   JSON shape documented in `skills/design-challenges/SKILL.md`'s
   "machine-readable output" section, with exactly one entry in the
   `challenges[]` array.

The prompt context loader SHALL read the skill and reference files
from repository paths. The prompt renderer itself SHALL be a pure
function of the loaded context, design task, generation request,
findings, and sources, and SHALL NOT read from any database row, file,
or environment variable.

#### Scenario: Same inputs render byte-identical prompt files

- **GIVEN** the same design task, generation request, findings, and
  sources
- **WHEN** the prompt renderer runs twice
- **THEN** the two rendered Markdown strings are byte-identical

#### Scenario: Category routes to the right playbook

- **GIVEN** a design task with `category = 'pwn'`
- **WHEN** the prompt is rendered
- **THEN** the rendered Markdown links
  `@skills/design-challenges/references/pwn-design.md`
- **AND** does not link `web-design.md` or `reverse-design.md`

#### Scenario: Evidence cap is enforced

- **GIVEN** a research run with 25 findings cited by the task
- **WHEN** the prompt is rendered
- **THEN** the evidence block contains exactly 20 finding bullets in
  the order they were listed

### Requirement: JSON output is validated before persistence

The system SHALL parse the Hermes stdout for the first balanced JSON
object (after stripping any \`\`\`json fences) and SHALL reject the
attempt if any of the following hold:

- the top level lacks `event` (object) or `challenges` (array of
  length 1)
- the single challenge object is missing any required field:
  `id`, `title`, `category`, `difficulty`, `points`, `deployment`,
  `primary_technique`, `learning_objective`, `prompt`, `artifacts`,
  `flag_location`, `validation`, `hints`
- `id` does not equal the parent `design_tasks.challenge_id`
- `category` does not equal the parent `design_tasks.category`
- `difficulty` does not equal the parent `design_tasks.difficulty`
- `points` is not a positive integer equal to the parent
  `design_tasks.points`
- `artifacts` is not a non-empty array of relative-path strings
- `hints` is not an array of exactly three non-empty strings
- for `category in {web, pwn}`: `deployment` does not contain
  `docker` (case-insensitive), or `port` is missing or differs from
  the parent `design_tasks.port`
- `artifacts` or `validation` contains any `http://` or `https://`
  URL string
- the generated `summary` is longer than 280 characters

When `event.flag_format` is missing from the JSON, the validator SHALL
insert the default value `flag{...}` rather than reject.

#### Scenario: Missing required hint count rejects the design

- **WHEN** a Hermes response returns `hints = ["only one"]`
- **THEN** the validator rejects the response
- **AND** the attempt is recorded as `failed` with
  `last_error` describing the hint-count violation
- **AND** no `challenge_designs` row is inserted

#### Scenario: Web challenge without docker deployment is rejected

- **GIVEN** a parent design task with `category = 'web'`
- **WHEN** the JSON's `deployment` field equals `"static"`
- **THEN** the validator rejects the response

#### Scenario: Default flag_format is filled in

- **WHEN** the JSON omits `event.flag_format`
- **THEN** the persisted `challenge_designs.flag_format` equals
  `flag{...}`

### Requirement: Quality gate is checked and recorded but does not block persistence

The system SHALL run explicit in-code quality-gate predicates derived
from the bundled `quality-gate.md` checklist against the validated
JSON and record the boolean result as
`challenge_designs.quality_gate_passed`. The Markdown checklist remains
human-readable guidance and SHALL NOT be parsed as an executable rule
source. A failed quality gate SHALL NOT cause the attempt to be
rejected; the persistence of the design proceeds and the operator
decides whether to act on it downstream.

#### Scenario: Failing quality gate persists with flag set to false

- **WHEN** the JSON passes schema validation but the quality gate
  flags a missing validation step
- **THEN** the `challenge_designs` row is inserted with
  `quality_gate_passed = false`
- **AND** the attempt is recorded as `completed`
- **AND** the `design_tasks.status` is set to `designed`

### Requirement: Status transitions are owned by this layer

The system SHALL transition `design_tasks.status` from
`queued -> designing` when a new attempt is inserted with
`status = 'running'`, and SHALL transition the same row to:

- `designed` on the same transaction that inserts the
  `challenge_designs` row,
- `failed` when the failed attempt has `attempt == max_attempts`
  (no further retries),
- `queued` when a failed attempt has `attempt < max_attempts`, so the
  operator can trigger a later real attempt. The failure transaction
  SHALL NOT insert a queued placeholder attempt row.

Each terminal write SHALL be gated on the `(design_attempt.id,
claim_token)` tuple and the parent `design_tasks.status = 'designing'`
condition so a stale caller cannot overwrite the row.

`archived` and `draft` design-task states remain owned by the
planning layer and SHALL NOT be written by this layer.

#### Scenario: Queued task moves to designing then designed on success

- **GIVEN** a design task with `status = 'queued'`
- **WHEN** an attempt is started, runs Hermes, and the response
  validates
- **THEN** the design task transitions through `designing → designed`
  in two separate transactions
- **AND** the `design_attempts` row is `completed`

#### Scenario: Failed attempt below max_attempts reopens the task

- **GIVEN** a design task whose parent request has `max_attempts = 3`
  and one prior failed attempt (`attempt = 1`)
- **WHEN** a second attempt fails validation
- **THEN** no third attempt row is inserted in that transaction
- **AND** the parent `design_tasks.status` is set back to `queued`
- **AND** the next operator trigger creates `attempt = 3`

#### Scenario: Failed attempt at max_attempts terminates as failed

- **GIVEN** a design task whose parent request has `max_attempts = 1`
- **WHEN** the first attempt fails
- **THEN** no retry row is inserted
- **AND** `design_tasks.status` is set to `failed`

#### Scenario: Wrong claim_token cannot complete an attempt

- **GIVEN** an existing `design_attempts` row with a known
  `claim_token`
- **WHEN** a caller posts a completion against
  `(attempt_id, wrong_token)`
- **THEN** the terminal `UPDATE` affects zero rows
- **AND** a typed `StaleClaimError` is surfaced
- **AND** the row's status is unchanged

### Requirement: Operator can trigger one synchronous design attempt

The system SHALL expose `POST /api/design-tasks/{id}/design` that
runs the full attempt lifecycle in the request thread for one task.
The endpoint SHALL:

- return 404 if the task does not exist
- return 409 if the task is not in `status = 'queued'`
- on success return 200 with the produced `challenge_designs` row
- on validation/timeout/Hermes failure return 200 with
  `attempt_status = 'failed'`, the current `design_task_status`
  (`queued` when retry remains, `failed` when exhausted), a
  `retry_available` boolean, and the `error` field populated; the
  attempt row is still persisted

The endpoint SHALL enforce a per-attempt wall-clock timeout (default
600 seconds) and SHALL record a `failed` attempt with
`last_error = 'timeout'` if Hermes does not return in time.

The system SHALL expose prompt/log content only through
`GET /api/design-attempts/{id}/artifact?kind={prompt|log}`. That
endpoint SHALL look up the stored `prompt_path` (for `kind=prompt`)
or `hermes_log_path` (for `kind=log`) of the matching attempt. Stored
paths SHALL be project-relative paths under `work/design/prompts/` or
`work/design/logs/`. Before reading, the endpoint SHALL resolve the
stored path against the project root, canonicalize both the candidate
path and the allowed root directory, and require the candidate to be
relative to the allowed root. String-prefix checks alone are not
sufficient. It SHALL respond with:
- 404 when the attempt does not exist or has no path of the requested
  kind,
- 400 when `kind` is not one of `prompt` or `log`,
- 403 when the stored path is absolute, contains traversal, or its
  canonical resolved path is outside the allowed directory,
- 200 with the file body on success.

#### Scenario: Artifact endpoint serves the stored prompt for an attempt

- **GIVEN** an attempt with a written `prompt_path` under
  `work/design/prompts/<id>.md`
- **WHEN** `GET /api/design-attempts/<id>/artifact?kind=prompt` is
  called
- **THEN** the response is 200 with the file body

#### Scenario: Artifact endpoint rejects path traversal

- **GIVEN** an attempt whose stored `prompt_path` resolves outside
  `work/design/prompts/` (e.g. `../../../etc/passwd`)
- **WHEN** the artifact endpoint is called for that attempt
- **THEN** the response is 403 and the file is not read

#### Scenario: Triggering design on a non-queued task is rejected

- **GIVEN** a design task with `status = 'designed'`
- **WHEN** the operator posts to `/api/design-tasks/{id}/design`
- **THEN** the response is HTTP 409
- **AND** no new `design_attempts` row is inserted

#### Scenario: Two concurrent triggers on the same task

- **WHEN** two operators post to the same task's design endpoint
  simultaneously
- **THEN** exactly one of them receives a 2xx with an attempt id
- **AND** the other receives HTTP 409

### Requirement: Request detail exposes designs and attempt history

The request detail API `GET /api/research/requests/{id}` SHALL include,
for each `design_tasks[]` entry:

- `latest_design`: the (at most one, by partial unique constraint)
  `challenge_designs` row for that task with `status = 'draft'`,
  serialized with payload, summary, flag_format, validation_notes,
  quality_gate_passed, created_at; or `null` if none.
- `attempts`: ordered list of `design_attempts` rows for that task,
  oldest first, each with id, attempt, status, started_at,
  finished_at, last_error, and artifact URLs for `prompt` and `log`
  when the corresponding stored path exists. Raw filesystem paths
  SHALL NOT be exposed in this response.

The dashboard SHALL render those fields inline under each Design Task
row as a collapsible panel showing the attempt list, a JSON viewer for
`latest_design.payload`, the quality-gate badge, and a "Design now"
button that is enabled only when the parent task status is `queued`.

#### Scenario: Detail returns latest design and attempts

- **GIVEN** a task with two attempts (one failed, one completed) and
  one draft design
- **WHEN** the request detail endpoint is called
- **THEN** the response includes `latest_design` non-null and
  `attempts` with two entries ordered oldest-first

#### Scenario: Design now button disabled outside queued

- **GIVEN** a task whose status is `designing`
- **WHEN** the dashboard renders the task row
- **THEN** the "Design now" button is rendered disabled
