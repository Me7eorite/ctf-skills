# structured-challenge-designs Specification

## Purpose
TBD - created by archiving change add-structured-challenge-designs. Update Purpose after archive.
## Requirements
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
- For `category = re`, the delivered artifact SHALL NOT expose the
  plaintext flag via `strings` unless the design's
  `primary_technique` explicitly states that `strings on the binary` is
  the intended solve path
- `validate.sh` and `writenup/exp.py` SHALL NOT embed the literal
  `metadata.flag`

When `event.flag_format` is missing from the JSON, the validator SHALL
insert the default value `flag{...}` rather than reject.

The validator SHALL generate the `challenge_designs.summary` value
from the validated payload and SHALL keep it at or below 280
characters, truncating if necessary. This generated value is persisted
as `challenge_designs.summary`; it is not read from the model JSON.

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

### Requirement: Medium and harder designs declare asset-flow gate fields

The system SHALL require structured challenge designs with difficulty `medium`,
`hard`, or `expert` to declare a substantive `difficulty_reason`, non-empty
`asset_flow`, non-empty `shortcut_closure`, and a shape-level `fingerprint`.

The `fingerprint` object SHALL include non-empty `entrypoint_type`,
`asset_flow_shape`, `flag_access_model`, and `scenario_type`.

#### Scenario: Medium design without difficulty reason is rejected

- **GIVEN** a parent design task with `difficulty = "medium"`
- **WHEN** the design JSON omits `difficulty_reason`
- **THEN** the design attempt is rejected
- **AND** no `challenge_designs` row is inserted

#### Scenario: Medium design without shortcut closure is rejected

- **GIVEN** a parent design task with `difficulty = "medium"`
- **WHEN** the design JSON has an otherwise valid asset flow
- **AND** `shortcut_closure` is missing or empty
- **THEN** the design attempt is rejected

#### Scenario: Medium design without complete fingerprint is rejected

- **GIVEN** a parent design task with `difficulty = "medium"`
- **WHEN** the design JSON omits `fingerprint.asset_flow_shape`
- **THEN** the design attempt is rejected

### Requirement: Asset-flow transitions must be concrete

The system SHALL count an asset-flow transition only when the produced asset or
capability is concrete and the next-stage dependency is specific. Generic
produced assets such as `access`, `data`, `result`, or `permission` SHALL NOT
count as effective transitions by themselves.

#### Scenario: Generic asset does not satisfy medium transition

- **GIVEN** a parent design task with `difficulty = "medium"`
- **WHEN** the only asset-flow stage produces `access`
- **AND** `why_next_stage_requires_it` says only `needed for next step`
- **THEN** the validator does not count that stage as an effective transition
- **AND** the design attempt is rejected

### Requirement: Quality gate is checked and recorded but does not block persistence

The quality gate SHALL continue to be recorded without blocking persistence of
a structurally valid Design, preserving the operator's ability to inspect the
failed Design. However, `quality_gate_passed = false` SHALL make that Design
ineligible for governed trial or production Build submission. A later valid
Design revision is required before governed construction can start.

#### Scenario: Failing quality gate persists but cannot build

- **WHEN** a Design passes structural validation but fails the quality gate
- **THEN** ChallengeDesign and its evidence are persisted for inspection with
  `quality_gate_passed = false`
- **AND** a governed trial or production Build submission is rejected with
  `design_quality_gate_failed`

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
- **THEN** the design task transitions through `designing -> designed`
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
- on success return 200 with:
  - `design_task_id` equal to the parent task id
  - `attempt_id` equal to the completed attempt id
  - `design_task_status = 'designed'`
  - `attempt_status = 'completed'`
  - `challenge_design` containing the produced row
  - `error = null`
- on validation/timeout/Hermes failure return 200 with
  `attempt_status = 'failed'`, the current `design_task_status`
  (`queued` when retry remains, `failed` when exhausted), and the
  `error` field populated; the attempt row is still persisted

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

`prompt_artifact_url` SHALL be
`/api/design-attempts/<attempt_id>/artifact?kind=prompt` when
`prompt_path` is set, otherwise `null`. `log_artifact_url` SHALL use
the same shape with `kind=log` when `hermes_log_path` is set,
otherwise `null`.

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

### Requirement: Design consumes research plus a bounded corpus ledger

The Design prompt SHALL include the task reservation and an authoritative
bounded ledger snapshot containing aggregate occupancy over all sibling
reservations/committed designs, the configured number of nearest sibling and
historical designs, current quota usage, forbidden combined signatures, and a
`ledger_version`.

The Design prompt SHALL require the output to identify which supplied
research findings and compared challenge IDs support the proposed design. It
SHALL NOT invite Design to choose different governed profile values.

#### Scenario: Next Design sees earlier committed evidence

- **GIVEN** task A committed DesignEvidence before task B's prompt is rendered
- **WHEN** task B starts Design
- **THEN** B's ledger snapshot includes A's challenge ID and governed profile
- **AND** B must explain its solve/implementation difference from relevant
  compared entries

### Requirement: Successful Design commits evidence and a build contract

A successful Design SHALL create one live `design_evidence` row linked to the
ChallengeDesign and parent task. The evidence SHALL:

- cite only findings belonging to the task's ResearchRun;
- cite at least one designable finding;
- list concrete research claims used;
- compare against actual IDs present in the supplied ledger;
- provide a non-empty distinctness claim covering solve and implementation;
- reproduce the reserved profile exactly;
- provide a valid structured `build_contract`.

The build contract SHALL contain `required_profile`,
`required_player_actions`, `required_components`, `required_asset_flow`,
`forbidden_shortcuts`, `acceptance_tests`, and
`allowed_implementation_freedom`. `required_player_actions` SHALL contain at
least one non-empty action for every difficulty and SHALL agree with
`required_profile.solve.required_action`.

Negative and acceptance tests SHALL use a closed host-owned harness vocabulary.
Design may reference only declared artifacts/fixtures and closed assertions; it
SHALL NOT provide an executable name, arbitrary argv, shell string, or path
outside the challenge contract.

The harness vocabulary SHALL be defined in host code and rendered into prompts
from the same source. Initial harness kinds SHALL include only
`artifact_direct_run`, `fixture_assertion`, `solver_with_fixture`,
`solver_without_fixture`, and category-permitted `random_flag_rebuild`.
Each harness kind SHALL define its accepted fields and assertions. Artifact and
fixture references SHALL be symbolic IDs declared in the build contract, not
paths. Unknown harness kinds, assertions, undeclared references, path traversal,
argv, or shell strings SHALL fail contract validation.

Every entry in `required_asset_flow` SHALL contain a stable `stage_id`, a
verification harness proving the stage's declared output/capability exists, and
a dependency harness proving the downstream solve fails when that
output/capability is withheld or invalidated.

ChallengeDesign insertion, DesignEvidence insertion, and reservation
`reserved -> committed` SHALL happen in one transaction. A conflicting ledger
advance SHALL fail with `stale_design_ledger`.
Evidence SHALL be versioned with `unique(design_task_id, evidence_version)` and
a partial unique constraint allowing at most one row with
`superseded_at IS NULL` per task. Supersession SHALL store
`superseded_at`, `superseded_by_evidence_id`, and `supersession_reason`.

#### Scenario: Invented evidence is rejected

- **GIVEN** Design output cites a finding or compared challenge ID absent from
  its authoritative inputs
- **WHEN** output validation runs
- **THEN** the attempt fails
- **AND** no ChallengeDesign or DesignEvidence is committed
- **AND** the reservation remains reserved for retry

#### Scenario: Design cannot drift from reserved implementation

- **GIVEN** the reservation requires WASM/Rust and runtime-derived-key
  concealment
- **WHEN** Design returns ELF/C with single-byte XOR concealment
- **THEN** validation rejects it as a profile mismatch

### Requirement: Persisted Designs can be revised without in-place contract mutation

The system SHALL expose a service-backed Design revision operation for a task
in `designed`, `build_failed`, or `built` when it has no queued/running
BuildAttempt. A built task is eligible only when its current version has not
been included in a released production corpus batch. The operation SHALL run
under the task/request locks and SHALL:

- mark the live ChallengeDesign and DesignEvidence superseded;
- release the old reservation;
- allocate and attach a fresh reservation, allowing the same governed profile
  only as a revision of the same task;
- clear stale plan review metadata;
- transition the task to `draft`.

The next Design attempt creates a new ChallengeDesign/DesignEvidence version.
The operation SHALL never edit a committed build contract in place. Tasks with
an active BuildAttempt are rejected. A production-released built version is
also rejected and requires a new DesignTask/version. Prior BuildAttempts and
observations remain immutable history. The revised draft SHALL pass the
existing plan-review checkpoint before it can transition `draft -> queued`.

#### Scenario: Failed quality Design is revised

- **GIVEN** a task in `designed` whose latest Design has
  `quality_gate_passed = false`
- **WHEN** the operator requests Design revision
- **THEN** the old design/evidence are superseded
- **AND** a fresh reservation is attached
- **AND** the task returns to `draft`
- **AND** it must be approved before queueing another Design attempt

#### Scenario: Active Build prevents revision

- **GIVEN** a task with a queued or running BuildAttempt
- **WHEN** Design revision is requested
- **THEN** the request is rejected
- **AND** no design, evidence, reservation, or task status changes

#### Scenario: Corpus-blocked unpublished build can be redesigned

- **GIVEN** a built task was blocked by corpus review
- **AND** it has not been included in a released production batch
- **WHEN** Design revision is requested
- **THEN** the prior build remains historical
- **AND** the task returns to `draft` with a fresh reservation

#### Scenario: Released production version is immutable

- **GIVEN** a built task belongs to a released production corpus batch
- **WHEN** in-place Design revision is requested
- **THEN** the request is rejected
- **AND** remediation requires a new DesignTask/version
