## ADDED Requirements

### Requirement: Web prompt rules support standard service defaults

The shard prompt SHALL allow Web challenges to use upstream default container
ports when the matrix does not override them, including Apache/nginx on port 80,
Tomcat on 8080, and common Node services on 3000. The prompt MUST allow the
standard Apache/nginx root master plus non-root worker process model only for
binding low ports and managing workers. Business worker processes MUST NOT run
permanently as root, and generated services MUST NOT use `privileged: true`,
host networking, broad Linux capabilities, host devices, or unnecessary writable
system mounts. All previously absolute statements elsewhere in the shard prompt
that conflict with the relaxed rule (for example `Never leave the service
running as root` or `Do not use root execution`) MUST be updated or removed so
the prompt is internally consistent and does not contradict itself.

#### Scenario: Apache nginx root master exception

- **WHEN** a dry-run prompt is rendered for a Web shard
- **THEN** it permits Apache/nginx port 80 with a root master and non-root
  workers while still forbidding permanently root business processes and broad
  container privileges

### Requirement: Runner computes resume plans before current run events

`HermesRunner` SHALL compute a structured resume plan immediately after claiming
a shard and before writing the current run's shard-level queued/running event.
The plan MUST use `ShardQueue.original_name(running_path)` as the shard key and
MUST be injected into the rendered prompt. Hermes MUST follow the host-provided
plan and MUST NOT query SQLite or infer completed stages itself.

#### Scenario: Resume plan uses original shard key

- **WHEN** worker `worker-02` claims `web-0001-0005.json` as
  `web-0001-0005.worker-02.json`
- **THEN** resume queries and rendered `progress --shard` commands use
  `web-0001-0005.json` and not the worker-suffixed filename

#### Scenario: Current queued event is not part of plan calculation

- **WHEN** a non-dry-run shard is claimed for retry
- **THEN** the runner computes the resume plan from historical events before it
  resets snapshots or records the current queued/running event

### Requirement: Resume skips only continuously verified stage prefixes

The resume plan SHALL evaluate stages in conceptual order
`design`, `implement`, `build`, `validate`, `document`. A stage MAY be skipped
only when the latest event for that stage in the previous claim window has
status `passed` and the stage-specific evidence is complete. The first stage
without both a latest passed event and complete evidence MUST stop the skip
prefix; all following stages MUST run again even if they have historical passed
events.

#### Scenario: Missing implement evidence stops later skips

- **WHEN** historical design and build events are passed but implement evidence
  is missing a required file
- **THEN** only design may be skipped and the runner resumes at implement

#### Scenario: Latest event overrides earlier passed event

- **WHEN** a stage has an earlier passed event followed by running or failed in
  the same historical window
- **THEN** that stage is not included in the skip prefix

### Requirement: Resume evidence is deterministic

The resume checker SHALL validate stage evidence from files and local runtime
facts. Design requires parseable `metadata.json` whose `id` matches the
challenge id. Web/Pwn implement requires `deploy/src/`, `deploy/Dockerfile`,
`deploy/docker-compose.yml`, and at least one non-empty business source file.
Reverse implement requires a non-empty non-document source file under `src/`.
Build requires `metadata.build_status == "passed"` and a non-empty
`metadata.build_command`; Web/Pwn also require a non-empty
`metadata.docker_image` that exists locally; Reverse also requires a safe
`dist/` artifact path and matching SHA-256. Validate resume requires
`validate.sh`, `solve/solve.py`, `metadata.solve_status == "passed"`, and a
historical validate/passed event. Document requires both `writeup/wp.md` and
`README.md` to exceed 500 bytes and contain at least two Markdown `## `
headings each.

#### Scenario: Missing Docker image prevents build skip

- **WHEN** Web metadata has `build_status=passed` but `docker_image` is missing,
  empty, or not found by `core.docker.image_exists`
- **THEN** build evidence is incomplete and build is not skipped

#### Scenario: Unsafe Reverse artifact path is rejected

- **WHEN** Reverse metadata points to an absolute artifact path, a `..` escape,
  or a file outside `dist/`
- **THEN** build evidence is incomplete even if the historical build event
  passed

#### Scenario: Validate evidence is read only

- **WHEN** validate evidence is checked for resume
- **THEN** the resume checker does not start services, run exploits, or execute
  `validate.sh`

### Requirement: Dry-run remains state-isolated

`challenge-factory run --dry-run` SHALL claim a pending shard, compute the
historical resume plan, render the prompt, and restore the shard to pending.
Dry-run MUST NOT write progress events, reset snapshots, short-circuit
all-skipped shards, invoke Hermes, invoke `ChallengeValidator`, modify
metadata/report production state, or move the shard to done or failed.
`--dry-run` and `--loop` MUST be mutually exclusive.

#### Scenario: Dry-run renders all-skipped prompt without state writes

- **WHEN** every challenge in a claimed shard is already fully resumable
- **THEN** dry-run still renders a prompt with the resume plan and writes no
  events or final queue transition

#### Scenario: Dry-run restores shard after prompt failure

- **WHEN** prompt rendering or dry-run logging fails after claim
- **THEN** the running shard is requeued to pending and the claim sidecar is
  cleaned up

### Requirement: Non-dry-run carries forward skipped stages

For each skipped stage in a non-dry-run resume plan, the runner SHALL write a
current-window carry-forward `stage/passed` event after the current queued event
and before the first pending event. Carry-forward events MUST use the current
worker, original shard name, challenge id, and a message that identifies the
historical source event id. Carry-forward events represent verified inheritance,
not re-execution. The runner MUST write a single pending event for the first
un-skipped stage in conceptual order, including the case where that stage is
`validate`; in that case the runner writes `validate/pending` immediately and
later overwrites the dashboard snapshot with `validate/running` when invoking
`ChallengeValidator` after Hermes returns.

#### Scenario: Resume to build records carried prefix

- **WHEN** design and implement are skipped and build is the first stage to run
- **THEN** the current window contains design/passed and implement/passed
  carry-forward events followed by build/pending

#### Scenario: Resume to validate writes validate pending

- **WHEN** design, implement, and build are all skipped and validate is the
  first un-skipped stage
- **THEN** the runner writes `validate/pending` immediately after the
  carry-forward events and before invoking Hermes, and writes `validate/running`
  after Hermes returns

#### Scenario: All-skipped shard short-circuits

- **WHEN** every challenge in the shard skips all five stages
- **THEN** the runner writes carry-forward events, writes each challenge
  complete/passed, writes shard-level complete/passed, creates or updates a
  passed report, and moves the shard to the done queue without invoking Hermes

### Requirement: Runner owns validate execution and validate events

Hermes SHALL generate `validate.sh` and `solve/solve.py` but SHALL NOT execute
validation or write validate progress events. For non-dry-run execution,
`HermesRunner` SHALL verify design, implement, build, and document prerequisites
and validate files after Hermes returns. If prerequisites are incomplete, the
runner MUST write validate/failed without invoking `ChallengeValidator`. If
prerequisites are complete and validate is not skipped, the runner MUST write
validate/running, call `ChallengeValidator.validate_challenge(challenge_id)`,
and map only `status == "passed"` to validate/passed.

Carry-forward `validate/passed` events written by the runner under the resume
protocol MUST start their message with the literal token `carry-forward:` and
MUST include the source historical event id. Fresh `validate/passed` and
`validate/failed` events written by the runner after invoking
`ChallengeValidator` MUST start their message with the literal token
`validator:` and MUST include the validator's status and elapsed time. The two
prefixes MUST be machine-distinguishable so audit tooling can separate inherited
historical validations from freshly executed ones.

#### Scenario: Carry-forward and validator messages are distinguishable

- **WHEN** the same shard window contains one carry-forward `validate/passed`
  inherited from a prior run and one fresh `validate/passed` written after
  `ChallengeValidator` returned passed
- **THEN** the carry-forward event message starts with `carry-forward:` and
  cites the historical source event id, while the fresh event message starts
  with `validator:` and cites the validator status and elapsed time

#### Scenario: Validate gate blocks missing prerequisites

- **WHEN** design, implement, build, document, `validate.sh`, or `solve/solve.py`
  evidence is incomplete
- **THEN** the runner writes validate/failed, includes missing items in the
  message/report, and does not call `validate_challenge`

#### Scenario: Validate failure makes challenge fail

- **WHEN** Hermes wrote design, implement, build, and document passed but
  `validate_challenge` returns a non-passed status
- **THEN** the runner writes validate/failed and final challenge complete/failed
  while leaving prior document/passed events append-only

#### Scenario: Skipped validate is not re-executed

- **WHEN** validate belongs to a verified resume skip prefix
- **THEN** the runner does not call `ChallengeValidator.validate_challenge` for
  that challenge in the current run

### Requirement: ChallengeValidator supports single-challenge validation

`ChallengeValidator` SHALL keep its batch validation interface and SHALL add
`validate_challenge(challenge_id) -> dict`. The single-challenge interface MUST
match exactly one `work/challenges/<challenge_id>-<slug>` directory. Zero
matches MUST return a failed `missing_challenge` status, and multiple matches
MUST return a failed `ambiguous_challenge` status without selecting or executing
any directory.

#### Scenario: Ambiguous challenge id is failed safely

- **WHEN** two challenge directories match the same challenge id prefix
- **THEN** `validate_challenge` returns `ambiguous_challenge` and the runner
  records validate/failed

### Requirement: Run command validation is mandatory

`challenge-factory run` SHALL always perform runner-owned per-challenge
validation in non-dry-run mode. The run subcommand MUST NOT expose a `--validate`
flag. The standalone `challenge-factory validate` command SHALL remain
available.

#### Scenario: Run help reflects mandatory validation

- **WHEN** an operator runs `challenge-factory run --help`
- **THEN** the help includes no `--validate` option, while
  `challenge-factory validate` remains a valid command

### Requirement: Timeout selection is explicit and reported

The run subcommand SHALL accept `--timeout INT` in seconds. The effective Hermes
timeout MUST be chosen by precedence: CLI flag, then `HERMES_TIMEOUT`, then
default `1500`. CLI and environment values MUST be positive integers. Invalid
CLI values MUST fail through argparse. Invalid or non-positive environment
values MUST fail before shard claim with exit code 2. The first stdout line of
run, including dry-run, MUST be `effective_timeout=<N> source=<cli|env|default>`.
This timeout SHALL apply only to the Hermes subprocess, not validator execution.

#### Scenario: Default timeout is reported

- **WHEN** run dry-run starts without `--timeout` or `HERMES_TIMEOUT`
- **THEN** stdout begins with `effective_timeout=1500 source=default`

#### Scenario: CLI timeout overrides environment timeout

- **WHEN** `HERMES_TIMEOUT=1700` and `--timeout 1800` are both supplied
- **THEN** stdout begins with `effective_timeout=1800 source=cli`

#### Scenario: Invalid environment timeout fails before claim

- **WHEN** `HERMES_TIMEOUT` is `abc` or `0`
- **THEN** run exits with code 2 before moving a shard or writing events

### Requirement: validate.sh prompt contract reuses existing images

The prompt SHALL instruct Web/Pwn validate scripts to inspect the expected image
before building it. Generated `validate.sh` scripts MUST use the pattern
`docker image inspect "$IMAGE" >/dev/null 2>&1 || docker build -t "$IMAGE" .`
before `docker compose up`. Force rebuild is performed manually by deleting the
image outside the script.

#### Scenario: Prompt includes image inspect fallback

- **WHEN** a dry-run prompt is rendered for Web/Pwn validation
- **THEN** it contains the literal `docker image inspect "$IMAGE" >/dev/null
  2>&1 || docker build` pattern

### Requirement: StateStore exposes resume-safe event queries

`StateStore` SHALL expose public read APIs for complete event streams:
`events_for_shard(shard, before_id=None)`, `events_for_challenge(shard,
challenge_id, after_id=None, before_id=None)`, and `latest_claim_event(shard,
before_id=None)`. Events MUST be returned by ascending event id, `before_id`
MUST be exclusive, and `after_id` for challenge events MUST be inclusive.
`events_for_challenge` MUST return only events whose `challenge_id` equals the
parameter value and MUST exclude shard-level events that have an empty
`challenge_id`; shard-level events are accessed exclusively via
`events_for_shard` or `latest_claim_event`.
`StateStore.record()` SHALL return the inserted event id. `reset_snapshots(shard)`
SHALL delete only snapshots for the named original shard and SHALL NOT delete
events.

#### Scenario: Event boundaries are respected

- **WHEN** query APIs are called with `after_id` and `before_id`
- **THEN** returned events include only records inside the documented id window
  and remain ordered by id

#### Scenario: Snapshot reset preserves history

- **WHEN** `reset_snapshots("web-0001-0005.json")` is called
- **THEN** snapshots for that shard are removed and all progress events remain
  queryable

### Requirement: Snapshot percent is monotonic within a run

After snapshots are reset for a new non-dry-run claim, snapshot updates SHALL
keep the maximum of the existing snapshot percent and the new event percent
while updating stage, status, message, and timestamp to the latest event.

#### Scenario: Validate running does not reduce displayed percent

- **WHEN** document/passed is followed by validate/running in the same run
- **THEN** the snapshot stage/status becomes validate/running and its percent
  is not lower than the document/passed percent

### Requirement: Reports preserve per-challenge validation results

After each single-challenge validation result, the runner SHALL merge
`challenge_id`, `solve_status`, `validation_status`, `validation_elapsed`, and
`validation_error` when present into the shard report's matching challenge
entry. If the report is missing or malformed, the runner MUST create or repair a
minimal report structure instead of discarding validation results. The shard
report top-level `runner_status` MUST be failed when any challenge validation
fails and passed when all challenges validate or are legally skipped by resume.

#### Scenario: Malformed report is repaired

- **WHEN** Hermes writes a missing, non-object, or non-list `challenges` report
- **THEN** the runner persists each validation result in a valid minimal report

#### Scenario: Mixed result shard keeps per-challenge statuses

- **WHEN** challenge A passes all stages and challenge B fails validate
- **THEN** A is complete/passed, B is complete/failed, the shard is
  complete/failed, the queue file moves to failed, and a later retry can
  carry-forward A without revalidating it

### Requirement: Timeout recovery cannot bypass validation

When Hermes times out, the runner SHALL re-evaluate current-window events and
deterministic evidence for design, implement, build, and document. The runner
MUST NOT treat `metadata.build_status == "passed"` alone as complete and MUST
NOT synthesize missing stage passed events. If prerequisites are complete, the
runner SHALL continue into mandatory validation; final done/failed status SHALL
depend on the same five-stage success rules as non-timeout execution.

#### Scenario: Timeout with missing document fails

- **WHEN** Hermes times out after build passed but document event or evidence is
  missing
- **THEN** the runner records final failure and does not write a synthetic
  document/passed event

#### Scenario: Timeout with complete prerequisites still validates

- **WHEN** Hermes times out after design, implement, build, and document are
  fully verified
- **THEN** the runner performs mandatory validate execution unless validate was
  already skipped by resume

### Requirement: Docker image inspection is isolated in core

The system SHALL provide `core.docker.image_exists(image: str) -> bool` using
`docker image inspect` with an argv list, `shell=False`, and a finite timeout.
The helper MUST return `False` rather than raising for empty image names,
missing Docker, command timeout, or non-zero inspect results. Domain resume code
MUST call this helper and MUST NOT import `subprocess`.

#### Scenario: Docker unavailable returns false

- **WHEN** Docker is not available on PATH during a build evidence check
- **THEN** `image_exists` returns `False` and resume does not skip build

### Requirement: Duration metrics cover the latest claim window

The system SHALL provide `duration_breakdown(challenge_id, shard)` returning
JSON-serializable durations for design, implement, build, validate, and document
from the latest shard-level queued/running claim window for the original shard
name. A stage duration SHALL be present only when the latest event for that
stage in the latest window is passed and the window contains a running event for
that stage. The duration value SHALL be `last_passed.created_at -
first_running.created_at`. Missing stages, non-passed latest events, or
carry-forward-only skipped stages SHALL return `null` for that stage.

#### Scenario: Latest window ignores older events

- **WHEN** a challenge has complete events in an older claim window and partial
  events in the latest claim window
- **THEN** duration metrics are computed only from the latest claim window

#### Scenario: Carry-forward stage has no duration

- **WHEN** a skipped stage has only a current-window carry-forward passed event
  and no running event
- **THEN** that stage's duration is `null`

### Requirement: Durations CLI validates shard input

`challenge-factory durations --challenge <id> --shard <name>` SHALL print the
duration breakdown as JSON. The `--shard` value MUST be an original shard
basename ending in `.json`; paths, worker-suffixed names, and names missing
`.json` MUST be rejected with exit code 2.

#### Scenario: Original shard basename is accepted

- **WHEN** `--shard web-0001-0005.json` is supplied
- **THEN** the CLI returns a JSON object containing the five stage keys

#### Scenario: Worker suffix shard name is rejected

- **WHEN** `--shard web-0001-0005.worker-02.json` or
  `running/web-0001-0005.worker-02.json` is supplied
- **THEN** the CLI exits with code 2
