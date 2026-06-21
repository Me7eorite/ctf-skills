# hermes-execution-protocol Specification

## Purpose
TBD - created by archiving change runner-resume-and-metrics. Update Purpose after archive.
## Requirements
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

`HermesRunner` SHALL compute a structured resume plan immediately after
claiming a shard and before writing the current run's shard-level
queued/running event. For an ordinary shard, the resume-read key and current
progress-write key SHALL both be `ShardQueue.original_name(running_path)`.
The plan MUST be injected into the rendered prompt. Hermes MUST follow the
host-provided plan and MUST NOT query the progress event store or infer
completed stages itself. Resume plan calculation SHALL go through the injected
`ProgressStore` protocol; the runner SHALL NOT import a concrete progress store
implementation.

When a generated shard contains a non-empty top-level
`resume_from_shard_basename`, the runner SHALL use that basename only as the
historical resume-read key passed to `latest_claim_event` and
`events_for_challenge`. It SHALL continue to use the current original basename
for snapshot reset, the current claim event, carry-forward events, rendered
`progress --shard` commands, validation events, and completion events.

The named resume source SHALL be a safe basename ending in `.json`; absolute
paths, path separators, `..`, and a value equal to the current basename SHALL
be rejected as malformed generated-shard input. Hand-written shards that omit
the field SHALL retain existing behavior.

#### Scenario: Retry reads previous attempt but writes current attempt

- **GIVEN** current shard `web-0001-attempt-2.json` contains
  `resume_from_shard_basename = "web-0001-attempt-1.json"`
- **WHEN** the runner computes and executes its resume plan
- **THEN** historical claim and challenge-event queries use
  `web-0001-attempt-1.json`
- **AND** all newly written progress and carry-forward events use
  `web-0001-attempt-2.json`

#### Scenario: Current queued event is not part of plan calculation

- **WHEN** a non-dry-run shard is claimed for retry
- **THEN** the runner computes the resume plan from the selected historical
  source before it resets current snapshots or records the current
  queued/running event

#### Scenario: Resume queries go through the protocol

- **WHEN** the runner builds a resume plan
- **THEN** all event reads go through `progress.events_for_shard`,
  `progress.events_for_challenge`, and `progress.latest_claim_event`
- **AND** no read goes through a SQLite cursor, legacy state-store attribute,
  or `work/state.sqlite3`

#### Scenario: Initial and hand-written shards are unchanged

- **WHEN** a shard omits `resume_from_shard_basename`
- **THEN** resume reads and current progress writes both use its current
  original basename

#### Scenario: Unsafe resume source is rejected

- **WHEN** `resume_from_shard_basename` is `../old.json`, `/tmp/old.json`, or
  contains a path separator
- **THEN** the runner rejects the shard before reading progress history

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
`validate.sh`, `writenup/exp.py`, `metadata.solve_status == "passed"`, and a
historical validate/passed event. Document requires both `writenup/wp.md` and
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

Hermes SHALL generate `validate.sh` and `writenup/exp.py` but SHALL NOT execute
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

The same host-owned validation behavior MAY be invoked by a build-attempt
revalidation service for an existing failed attempt. That service path SHALL NOT
invoke Hermes and SHALL NOT recompute the original resume/carry-forward
decision; it SHALL use current disk lookup and evidence for the validation
decision.

#### Scenario: Carry-forward and validator messages are distinguishable

- **WHEN** the same shard window contains one carry-forward `validate/passed`
  inherited from a prior run and one fresh `validate/passed` written after
  `ChallengeValidator` returned passed
- **THEN** the carry-forward event message starts with `carry-forward:` and
  cites the historical source event id, while the fresh event message starts
  with `validator:` and cites the validator status and elapsed time

#### Scenario: Validate gate blocks missing prerequisites

- **WHEN** design, implement, build, document, `validate.sh`, or `writenup/exp.py`
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

#### Scenario: Build-attempt revalidation uses current disk evidence only

- **GIVEN** a failed build attempt references a shard whose prior
  `validate/failed` event came from stale host lookup state
- **AND** the challenge directory now exists on disk
- **WHEN** the build-attempt revalidation service invokes host validation
- **THEN** validation resolves the current challenge directory before the gate
- **AND** it does not invoke Hermes
- **AND** it does not create carry-forward events from a newly computed resume
  plan

### Requirement: ChallengeValidator supports single-challenge validation

`ChallengeValidator` SHALL keep its batch validation interface and SHALL add
`validate_challenge(challenge_id) -> dict`. The single-challenge interface MUST
match exactly one `work/challenges/<challenge_id>-<slug>` directory. Zero
matches MUST return a failed `missing_challenge` status, and multiple matches
MUST return a failed `ambiguous_challenge` status without selecting or executing
any directory.

When `validate.sh` exits `0`, the validator SHALL extract the recovered flag by
scanning the captured stdout for independent flag tokens matching
`(?<![A-Za-z0-9_])flag\{[^\r\n{}]+\}(?![A-Za-z0-9_])` and using the LAST match
as `printed_flag`. If no match
exists, the result is `flag_mismatch` with `printed_flag = ""`. This replaces
the previous "last non-empty stdout line" rule, which mis-classified successful
runs as `flag_mismatch` whenever the shell's EXIT trap printed cleanup messages
after the flag was echoed.

The selected `printed_flag` is compared to `metadata.flag` with exact string
equality. On mismatch the result status is `flag_mismatch`. On equality the
result status is `passed`.

#### Scenario: Ambiguous challenge id is failed safely

- **WHEN** two challenge directories match the same challenge id prefix
- **THEN** `validate_challenge` returns `ambiguous_challenge` and the runner
  records validate/failed

#### Scenario: Cleanup trap output does not mask a passing flag

- **GIVEN** `validate.sh` exits `0` and stdout ends with
  ```
  [+] Validation PASSED
  flag{whitespace_bypass_master}
  [*] Cleaning up...
  ```
- **AND** `metadata.flag = "flag{whitespace_bypass_master}"`
- **WHEN** `validate_challenge` runs
- **THEN** the result status is `passed`
- **AND** `printed_flag == "flag{whitespace_bypass_master}"`

#### Scenario: No flag pattern in stdout yields flag_mismatch

- **GIVEN** `validate.sh` exits `0` but stdout contains no `flag{...}`
  substring
- **WHEN** `validate_challenge` runs
- **THEN** the result status is `flag_mismatch` with `printed_flag = ""`

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

### Requirement: Snapshot percent is monotonic within a run

After snapshots are reset for a new non-dry-run claim, snapshot updates SHALL
preserve the maximum of the existing derived percent and the new event derived
percent, where the percent is computed by `_percent(stage, status)` in
`core/state.py`. The implementation SHALL NOT persist `percent` as a column.
When a newer event has a lower derived percent than the current snapshot, the
upsert SHALL keep the snapshot's `stage` and `status` and update only
`updated_at`, `worker`, and `message`. This is a deliberate behavior change
from the pre-existing SQLite upsert, which always overwrote `(stage, status)`
to the newest event and only constrained `percent`. The dashboard now displays
the `(stage, status)` of the highest-progress event seen in the window rather
than the last-arriving event.

#### Scenario: Lower-progress event does not reduce displayed percent

- **WHEN** document/passed is followed by validate/running in the same run
- **THEN** the validate/running event is appended to `progress_events`
- **AND** the snapshot keeps `stage=document` and `status=passed`
- **AND** the dashboard-visible derived percent does not fall below the
  document/passed percent

#### Scenario: Out-of-order regression is suppressed

- **WHEN** a snapshot is at `(stage=validate, status=running)` and a late
  build/passed event arrives for the same `(shard, challenge_id)` pair
- **THEN** the snapshot keeps `(validate, running)` but the new event is still
  appended to `progress_events`

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

### Requirement: ProgressStore exposes resume-safe event queries

The `ProgressStore` protocol SHALL expose public read APIs for complete
event streams: `events_for_shard(shard, before_id=None)`,
`events_for_challenge(shard, challenge_id, after_id=None,
before_id=None)`, and `latest_claim_event(shard, before_id=None)`.
Events MUST be returned by ascending event id, `before_id` MUST be
exclusive, and `after_id` for challenge events MUST be inclusive.
`events_for_challenge` MUST return only events whose `challenge_id`
equals the parameter value and MUST exclude shard-level events that
have an empty `challenge_id`; shard-level events are accessed
exclusively via `events_for_shard` or `latest_claim_event`.
`ProgressStore.record()` SHALL return the inserted event id.
`reset_snapshots(shard)` SHALL delete only snapshots for the named
original shard and SHALL NOT delete events.

#### Scenario: Event boundaries are respected

- **WHEN** query APIs are called with `after_id` and `before_id`
- **THEN** returned events include only records inside the documented id window
  and remain ordered by id

#### Scenario: Snapshot reset preserves history

- **WHEN** `reset_snapshots("web-0001-0005.json")` is called
- **THEN** snapshots for that shard are removed and all progress events remain
  queryable

### Requirement: Hermes shard runner claims one worker-owned shard before prompting

The shard runner SHALL support constrained claiming in addition to the legacy
whole-queue claim. The public runner and CLI contracts SHALL accept optional
`category`, `build_attempt_id`, and build-attempt-attribution filters. When a
filter is present, the runner SHALL claim only a shard whose JSON payload
matches every requested filter and SHALL render the prompt from that claimed
shard.

The existing unconstrained runner behavior remains valid for whole-queue
legacy operation.

`--build-attempt` SHALL be mutually exclusive with `--loop`; it names one
specific shard execution target. `--category` MAY be combined with `--loop` or
`--build-attempt`; combined filters SHALL all match. `--build-attempts-only`
SHALL require `--category` and SHALL be mutually exclusive with
`--build-attempt`.

Before scanning the queue, constrained claims SHALL validate category and UUID
filter arguments. Candidate attribution SHALL contain valid top-level
`build_attempt_id` and `design_task_id` UUIDs when build-attempt attribution is
required. An exact build-attempt claim SHALL also require the canonical
`<build_attempt_id>.json` basename. Malformed JSON, invalid attribution,
non-regular files, and symbolic links SHALL be skipped without mutation. Legacy
unconstrained claims retain their existing compatibility behavior.

#### Scenario: CLI category filter reaches the queue

- **WHEN** `challenge-factory run --worker W --category web` is invoked
- **THEN** the runner passes `category = web` to the shard queue claim
- **AND** no Pwn or Re shard is claimed by that worker invocation

#### Scenario: CLI attributed category filter skips legacy shards

- **GIVEN** a legacy Web shard and an attributed Web build-attempt shard are
  both pending
- **WHEN** `challenge-factory run --worker W --category web
  --build-attempts-only` is invoked
- **THEN** the runner passes `category = web` and build-attempt attribution
  required to the shard queue claim
- **AND** the legacy Web shard is not claimed

#### Scenario: CLI build-attempt filter reaches the queue

- **WHEN** `challenge-factory run --worker W --build-attempt A` is invoked
- **THEN** the runner passes `build_attempt_id = A` to the shard queue claim
- **AND** no shard lacking `build_attempt_id = A` is claimed

#### Scenario: CLI combines exact attempt and expected category

- **WHEN** `challenge-factory run --worker W --build-attempt A --category web`
  is invoked
- **THEN** the runner passes both filters to the shard queue claim
- **AND** a shard with attempt `A` but a non-Web challenge is not claimed

#### Scenario: Exact attempt ignores a duplicate noncanonical basename

- **GIVEN** canonical shard `A.json` and another pending file both contain
  `build_attempt_id = A`
- **WHEN** an exact-attempt worker claims `A`
- **THEN** only `A.json` is eligible
- **AND** the duplicate file remains pending

#### Scenario: Build-attempt run rejects loop mode

- **WHEN** `challenge-factory run --worker W --build-attempt A --loop` is
  invoked
- **THEN** the CLI exits with code 2 before claiming any shard

#### Scenario: Invalid constrained arguments do not scan the queue

- **WHEN** the CLI receives an invalid category, invalid build-attempt UUID, or
  incompatible constrained options
- **THEN** it exits with code 2 before constructing a claim
- **AND** no pending shard is mutated

#### Scenario: Malformed constrained candidate remains pending

- **GIVEN** a pending candidate has malformed JSON or invalid UUID attribution
- **WHEN** a constrained runner scans the queue
- **THEN** that candidate remains pending
- **AND** Hermes is not invoked for that candidate

#### Scenario: No matching shard is not a failed generation

- **GIVEN** pending shards exist but none match the requested category or build
  attempt
- **WHEN** the constrained runner executes
- **THEN** it exits without invoking Hermes
- **AND** no shard is moved to `failed/`

### Requirement: validate.sh prompt contract forbids in-script image builds

The Docker image SHALL be a Stage 3 (`build`) deliverable. By the time the
runner records `build/passed` for a Web/Pwn challenge, the image named in
`metadata.docker_image` MUST already be present in the local Docker daemon.

Generated `validate.sh` scripts MUST satisfy the following hygiene rules:

1. The script MUST gate on image presence with a **fail-fast** check and MUST
   NOT contain any `docker build`, `docker compose build`, `pip install`,
   `apt-get`, or other network-fetching commands. The gate pattern is:
   ```bash
   docker image inspect "$IMAGE" >/dev/null 2>&1 || {
     echo "validate.sh: required image '$IMAGE' is missing; rebuild via the build stage" >&2
     exit 1
   }
   ```
   This makes validation offline-capable and prevents transient network
   failures (e.g. a registry / mirror outage during base-image pull, or a
   `pip install` package fetch failure) from being misreported as
   `nonzero_exit` validation failures.
2. The `cleanup` function (and any other shell function fired from
   `trap ... EXIT` / `trap ... ERR`) MUST redirect ALL of its output to stderr
   (`>&2`). This includes `echo` lines, `docker stop`, `docker rm`, and any
   diagnostic messages. The recovered flag MUST be the last text written to
   stdout in the success path.
3. The script MUST perform a pre-run cleanup of any stale container name
   before `docker run --name "$CONTAINER_NAME"`, e.g.
   `docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true`. This prevents
   `nonzero_exit` failures caused by leftover containers from a previous
   killed run.

#### Scenario: Prompt forbids in-script image build

- **WHEN** a dry-run prompt is rendered for Web/Pwn validation
- **THEN** the prompt instructs `validate.sh` to `exit 1` when
  `docker image inspect "$IMAGE"` fails, and explicitly forbids `docker build`,
  `docker compose build`, `pip install`, and `apt-get` inside `validate.sh`

#### Scenario: Prompt mandates stderr-only cleanup output

- **WHEN** a dry-run prompt is rendered for Web/Pwn validation
- **THEN** the prompt instructs cleanup-function output to be redirected to
  stderr (`>&2`) and instructs a pre-run `docker rm -f "$CONTAINER_NAME"`
  before `docker run --name`

### Requirement: Build Hermes invocations use a local execution workspace

For non-dry-run build shard execution, the runner SHALL create a local
workspace under `work/executions/<workspace_id>/` before rendering the build
prompt or invoking Hermes. `workspace_id` SHALL be the shard payload's
top-level `build_attempt_id` when present and valid. For legacy/manual shards
without build-attempt attribution, the runner SHALL use `manual-<uuid>`.

The workspace SHALL contain `input/`, `references/`, `output/`, `logs/`, and
any `bin/` helper shim directory needed by the rendered build prompt.
The runner SHALL copy the claimed running shard to `input/shard.json` and
SHALL write `input/manifest.json` with the workspace id, original shard
basename, running shard basename, worker, category, build attempt id when
present, design task id when present, creation timestamp, and input hashes.

Per-invocation Hermes log output SHALL be written under
`work/executions/<workspace_id>/logs/` (replacing the prior
`work/logs/<shard_name>.log` location for build shards). Research and design
log paths SHALL remain unchanged.

The build prompt SHALL render the structured report path as
`./logs/report.json`. Before existing report consumers run, the runner SHALL
import or sync that workspace report to the legacy
`work/reports/<running-shard-stem>.report.json` path. Existing report summary
behavior that scans `work/reports/*.report.json` SHALL continue to work.

**Materialization strategy** SHALL copy per-claim files so claim-time snapshots
cannot be modified retroactively: `input/shard.json`, `input/manifest.json`,
and the generation-profile snapshot. It SHALL also copy only the selected
category's required Markdown guidance into `references/`. This change SHALL
NOT create repository-external reference symlinks because Docker profiles are
only required to mount `work/executions/`; such symlinks would be broken in
that backend. `input/manifest.json` SHALL record
`allowed_static_reference_roots: []`, and preflight SHALL reject any injected
reference symlink. A future read-only-mount implementation may define a
non-empty allowlist with an equivalent visibility contract.

The workspace id is not a database id. This change SHALL NOT add an
`executions` table or require persistent execution rows.

If a workspace already exists for the derived workspace id, the runner SHALL
either recreate only that owned workspace subtree from an empty fixed layout or
fail preflight before invoking Hermes. It SHALL NOT merge a new invocation with
stale workspace input, output, logs, or references.

#### Scenario: Build-attempt shard gets stable workspace id

- **GIVEN** a claimed shard payload contains `build_attempt_id = A`
- **WHEN** the build runner prepares the workspace
- **THEN** it creates `work/executions/A/`
- **AND** writes the claimed shard to `work/executions/A/input/shard.json`
- **AND** records `A` in `input/manifest.json`

#### Scenario: Legacy shard gets manual workspace id

- **GIVEN** a claimed legacy shard has no `build_attempt_id`
- **WHEN** the build runner prepares the workspace
- **THEN** it creates `work/executions/manual-<uuid>/`
- **AND** no database execution row is required

#### Scenario: Stale manual workspaces are reclaimed on new workspace creation

- **GIVEN** `work/executions/manual-old/` has mtime older than 7 days
- **AND** `work/executions/manual-fresh/` has mtime within 7 days
- **AND** `work/executions/<build_attempt_id-uuid>/` exists as attributed
- **WHEN** the runner prepares a new workspace
- **THEN** `manual-old` is deleted
- **AND** `manual-fresh` is kept
- **AND** the attributed UUID workspace is not touched by GC
- **AND** GC errors (permission/busy) do not block new workspace creation

#### Scenario: Build Hermes log lands inside the workspace

- **WHEN** the build runner invokes Hermes for workspace `W`
- **THEN** Hermes log output is written under `work/executions/W/logs/`
- **AND** no new log file appears under the legacy `work/logs/` for that
  build shard
- **AND** research and design log paths under `work/research/logs/` and
  `work/design/logs/` are unchanged

#### Scenario: Workspace report is visible to legacy report merge

- **GIVEN** Hermes writes `./logs/report.json` in workspace `W`
- **WHEN** the runner finishes the Hermes invocation
- **THEN** the report is imported to
  `work/reports/<running-shard-stem>.report.json`
- **AND** `merge-reports` can include it without scanning `work/executions`

### Requirement: Build prompts use workspace-relative paths

The build prompt SHALL expose only workspace-relative runtime paths for the
claimed shard, reference context, output directory, workspace logs, and helper
shims controlled by this change. It SHALL refer to the shard as
`./input/shard.json` and the candidate output root as `./output/`.

The build prompt SHALL NOT embed host absolute paths for the running shard,
report path, challenge output root, generation profile, design skill, or design
references. Non-build research/design prompt behavior is unchanged by this
requirement.

#### Scenario: Dry-run prompt omits host shard path

- **WHEN** a build dry-run prompt is rendered for a claimed shard
- **THEN** it contains `./input/shard.json`
- **AND** it contains `./output/`
- **AND** it does not contain the absolute path to `work/shards/running`

#### Scenario: Workspace report path is relative

- **WHEN** a build prompt references execution logs or reports
- **THEN** those references are under `./logs/`
- **AND** no host absolute report path is rendered

#### Scenario: Dry-run preserves preview semantics

- **WHEN** a build dry-run prompt is rendered from a workspace context
- **THEN** Hermes is not invoked
- **AND** no workspace output is promoted
- **AND** the claimed shard is returned to pending using the existing dry-run
  requeue behavior

### Requirement: Build Hermes calls use category profiles and workspace cwd

The build runner SHALL derive the category from the claimed shard payload and
SHALL invoke Hermes with profile `cf-<category>` for categories supported by
the build shard queue. The profile argument SHALL be inserted into argv
immediately before the `chat` subcommand using a single shared helper
extracted from the existing research/design `_build_arguments` implementations,
with the same fallback behavior when `chat` is not present.

The Hermes subprocess `cwd` SHALL be the execution workspace. The build runner
SHALL NOT require Git worktree mode (`-w`) for this contract. Existing
research/design profile binding behavior SHALL remain unchanged in observable
output.

#### Scenario: Web shard uses Web profile

- **GIVEN** a claimed shard contains only Web challenges
- **WHEN** the build runner invokes Hermes
- **THEN** the argv includes `-p cf-web`
- **AND** the subprocess `cwd` argument equals `work/executions/<workspace_id>`
- **AND** the subprocess `cwd` argument is NOT the project root

#### Scenario: Git worktree is not required

- **WHEN** the build runner invokes Hermes
- **THEN** the argv does not need to include `-w`
- **AND** runtime isolation relies on the project workspace contract, not Git
  worktree behavior

#### Scenario: Shared helper covers research, design, and build

- **GIVEN** research, design, and build runners all need to insert `-p <name>`
- **WHEN** any of them builds the Hermes argv
- **THEN** they invoke the same shared helper in `hermes/process.py`
- **AND** the `chat`-index insertion semantics are preserved for all three

### Requirement: Build Hermes hard timeout follows the claimed shard

When no explicit operational override is supplied, the runner SHALL derive
the Hermes hard timeout after claiming and parsing the shard: Re SHALL use
1800 seconds, Web SHALL use 2700 seconds, Pwn SHALL use 3600 seconds, and a
Pwn shard containing any challenge with `difficulty=expert` SHALL use 5400
seconds. A Pwn challenge with missing or unknown difficulty SHALL use 3600
seconds. Mixed-category shards remain invalid and SHALL fail preflight.

Timeout precedence SHALL be explicit CLI `--timeout`, then the existing
`HERMES_TIMEOUT` environment variable, then claimed-shard policy. A direct
runner caller supplying a positive timeout is equivalent to an explicit CLI
override. Research and design timeout behavior SHALL remain unchanged.

Web UI constrained worker dispatch SHALL use claimed-shard policy by default
without requiring an editable timeout field. The effective timeout and its
source (`cli`, `env`, or `shard_policy`) SHALL be visible in the workspace
manifest, Hermes log, worker-start API result, and build-attempt execution
view/status output.

#### Scenario: Web UI starts a Web build with policy timeout

- **GIVEN** a queued Web build attempt
- **AND** neither CLI nor environment supplies a timeout override
- **WHEN** the operator starts its worker from the Web UI
- **THEN** Hermes receives a hard timeout of 2700 seconds
- **AND** the UI/API reports 2700 seconds with source `shard_policy`

#### Scenario: Re uses the shorter non-Docker build budget

- **GIVEN** a claimed Re shard
- **AND** no explicit timeout override
- **WHEN** Hermes is invoked
- **THEN** its hard timeout is 1800 seconds

#### Scenario: Expert Pwn raises the whole shard budget

- **GIVEN** a claimed Pwn shard containing at least one expert challenge
- **AND** no explicit timeout override
- **WHEN** Hermes is invoked
- **THEN** its hard timeout is 5400 seconds

#### Scenario: Explicit override wins over shard policy

- **GIVEN** a claimed Web shard
- **AND** the CLI explicitly supplies `--timeout 4200`
- **WHEN** Hermes is invoked
- **THEN** its hard timeout is 4200 seconds
- **AND** the recorded timeout source is `cli`

### Requirement: Build preflight fails closed before model invocation

Before invoking Hermes, the build runner SHALL preflight the workspace. The
preflight SHALL verify in order:

1. The selected `cf-<category>` Hermes profile exists on the host (checked via
   the same `profile_exists()` helper already used by research execution).
2. `input/shard.json` exists, is a regular file, and parses as JSON.
3. Every challenge in the shard has one supported category.
4. The category matches the selected `cf-<category>` profile.
5. `output/` exists and is writable.
6. The workspace contains `./bin/progress` as a regular file with the
   executable bit set; the shim MUST be materialized before preflight so its
   absence is caught here, not later during prompt rendering.
7. The workspace contains no unrelated challenge artifact names. A name is
   "unrelated" when it matches the challenge-namespace pattern
   `^(web|pwn|re)-[a-zA-Z0-9][a-zA-Z0-9_-]*$` but does NOT match any id from
   `input/shard.json::challenges[*].id` by exact name or `<id>-<slug>` prefix.
   Symlinks are resolved before matching. The matcher MUST NOT depend on
   stricter id-shape assumptions (such as `^(web|pwn|re)-\d+`), because real
   design-task ids use a project-defined format like
   `<category>-<hex8>-<NNNN>(-<slug>)?`.
8. Reference symlinks SHALL resolve only to roots listed in the manifest. The
   copy-only strategy records an empty list, so every reference symlink fails.

When preflight fails, the runner SHALL return `status=failed` with
`failure_type=infrastructure` and SHALL NOT invoke Hermes. The failure message
for a missing profile SHALL include the literal recovery command
`hermes profile create cf-<category>`. It MAY move the already claimed shard
through the existing failed-shard path so build-attempt reconciliation can
observe the failure, but it SHALL NOT move unrelated shards or publish
workspace output.

#### Scenario: Category/profile mismatch blocks invocation

- **GIVEN** the claimed shard contains a Pwn challenge
- **AND** the runner selected profile `cf-web`
- **WHEN** preflight runs
- **THEN** it fails before invoking Hermes

#### Scenario: Unrelated artifact blocks invocation

- **GIVEN** a Web workspace contains a `pwn-9999` directory entry
- **WHEN** preflight runs
- **THEN** it fails before invoking Hermes

#### Scenario: Unsafe reference symlink blocks invocation

- **GIVEN** `references/` contains a symlink resolving outside allowed static
  reference roots
- **WHEN** preflight runs
- **THEN** it fails before invoking Hermes

#### Scenario: Preflight failure only affects the claimed shard

- **WHEN** preflight fails after a shard is claimed
- **THEN** Hermes is not invoked
- **AND** no unrelated pending shard is moved
- **AND** no candidate artifact is published

#### Scenario: Missing cf-<category> profile fails closed

- **GIVEN** the host has no Hermes profile named `cf-web`
- **WHEN** the build runner preflights a Web shard
- **THEN** preflight returns infrastructure-failed before invoking Hermes
- **AND** the failure message contains the literal string
  `hermes profile create cf-web`
- **AND** no shard or workspace output is published

#### Scenario: Missing or non-executable progress shim fails closed

- **GIVEN** a workspace where `./bin/progress` was not materialized (or its
  executable bit is missing)
- **WHEN** preflight runs
- **THEN** preflight returns infrastructure-failed before invoking Hermes
- **AND** the failure message identifies the missing/invalid shim
- **AND** the runner does NOT discover the problem only at prompt-rendering
  time

#### Scenario: Real design-task challenge ids are recognized

- **GIVEN** a claimed shard with `challenges[0].id = "web-abcdef12-0001"`
- **AND** Hermes writes `./output/challenges/web/web-abcdef12-0001-demo/`
- **WHEN** preflight scans for unrelated artifacts AND promotion runs
- **THEN** the directory is treated as the claimed challenge (not "unrelated"
  or "unclaimed"), because matching is by claimed-ids set, not by the legacy
  `^(web|pwn|re)-\d+` shape

### Requirement: Claimed workspace output is promoted for existing validation

Hermes SHALL write candidate challenge artifacts under the workspace output
tree at the fixed layout `./output/challenges/<category>/<id>(-<slug>)?/`,
where `<id>` is one of the ids listed in `input/shard.json::challenges[*].id`.
The build prompt SHALL render this layout into Hermes-visible instructions.

Before running the existing validator, the runner SHALL promote only output
directories whose name matches one of the claimed ids by exact match
(`name == id`) or by id-prefix (`name == f"{id}-{slug}"`). Matching MUST use
the claimed-ids set from the shard payload, NOT a regex over the id shape:
real design-task ids look like `<category>-<hex8>-<NNNN>` and are not covered
by stricter assumptions like `^(web|pwn|re)-\d+`. Promoted directories are
copied to `work/challenges/<category>/` expected by current resume and
validation code.

For resume runs, the runner SHALL first copy any existing canonical challenge
directory for a claimed id into the workspace output layout before invoking
Hermes, so carried-forward artifacts remain available without exposing the
canonical tree to the prompt.

Promotion SHALL reject output symlinks, path traversal, missing
`metadata.json`, metadata `id` or `category` mismatch, and multiple output
directories for the same claimed id. Promotion SHALL copy to a temporary
sibling under `work/challenges/<category>/` and atomically rename into place.
If a canonical directory for the same claimed id already exists, it SHALL be
quarantined under the fixed path
`work/executions/<workspace_id>/quarantine/<category>/<dirname>/` before
replacement (where `<dirname>` is the original canonical directory basename,
e.g. `web-0001-demo`). Unrelated challenge directories SHALL NOT be moved or
deleted. Quarantined directories SHALL be retained until the workspace itself
is GC'd by the rules in the workspace-reuse requirement; the runner MAY delete
quarantine entries earlier as part of the same retention pass.

This requirement SHALL NOT introduce execution rows, lease/fencing tokens,
operator approval, or a general publisher allowlist. Unclaimed output
directories SHALL NOT be copied to `work/challenges`.

This requirement is an explicit **compatibility bridge** and SHALL be REMOVED
by the subsequent `add-staged-publication-allowlist` change, which will replace
it with a stricter publisher-owned requirement in `worker-pool-execution`. The
narrow promotion logic SHALL NOT be extended to support arbitrary publication,
operator approval, or anything beyond the literal claimed challenge ids.

#### Scenario: Claimed Web output reaches validation

- **GIVEN** `input/shard.json` contains challenge id `web-0001`
- **AND** Hermes writes `./output/challenges/web/web-0001-demo/metadata.json`
- **WHEN** the runner prepares to validate
- **THEN** it promotes that directory to
  `work/challenges/web/web-0001-demo/`
- **AND** the existing validator can inspect it

#### Scenario: Unclaimed output is not promoted

- **GIVEN** `input/shard.json` contains only `web-0001`
- **AND** Hermes writes `./output/challenges/web/web-9999-extra/`
- **WHEN** output promotion runs
- **THEN** `web-9999-extra` is not copied to `work/challenges`
- **AND** the runner records an infrastructure or quality-gate failure

#### Scenario: Output written at a non-conforming layout is not promoted

- **GIVEN** `input/shard.json` contains `web-0001`
- **AND** Hermes writes `./output/web-0001/` (missing the
  `challenges/<category>/` prefix)
- **WHEN** output promotion runs
- **THEN** the directory does not match `./output/challenges/<category>/<id>-*/`
- **AND** it is not promoted to `work/challenges`
- **AND** the runner records an infrastructure or quality-gate failure

#### Scenario: Resume artifacts are edited in the workspace copy

- **GIVEN** `work/challenges/web/web-0001-demo/metadata.json` already exists
- **AND** the resume plan says `web-0001` has carried-forward stages
- **WHEN** the runner prepares workspace `W`
- **THEN** it copies that canonical directory to
  `work/executions/W/output/challenges/web/web-0001-demo/`
- **AND** the prompt still only references `./output/challenges/web/web-0001-demo/`

#### Scenario: Unsafe output symlink is rejected

- **GIVEN** Hermes writes `./output/challenges/web/web-0001-demo` as a symlink
  to a path outside the workspace
- **WHEN** output promotion runs
- **THEN** the symlink is not promoted
- **AND** the claimed shard is failed before validation runs

#### Scenario: Existing claimed artifact is quarantined before replacement

- **GIVEN** a claimed output directory for `web-0001`
- **AND** `work/challenges/web/web-0001-demo/` already exists
- **WHEN** promotion succeeds
- **THEN** the existing canonical directory is moved to
  `work/executions/<workspace_id>/quarantine/web/web-0001-demo/`
- **AND** the new claimed directory is atomically renamed into
  `work/challenges/web/web-0001-demo/`
- **AND** unrelated directories under `work/challenges/web/` are not touched

#### Scenario: Validation fails after successful promotion

- **GIVEN** promotion succeeds for `web-0001`
- **AND** the previous canonical version was quarantined under the workspace
- **WHEN** subsequent validation runs and `validate.sh` returns non-zero
- **THEN** the new canonical directory remains in place with
  `metadata.solve_status = failed` written by the existing validator
- **AND** the build_attempt is marked failed by the existing path
- **AND** the quarantined previous version is retained for the workspace's
  retention window for audit
- **AND** the runner does NOT automatically roll back to the quarantined
  version; rollback is an explicit operator action outside this change

### Requirement: Build prompts record progress through a workspace-local shim

For non-dry-run build invocations, the runner SHALL materialize a workspace
shim at `./bin/progress` whose body appends one compact JSON object per
invocation to `./logs/progress-events.jsonl`. The build prompt SHALL render
the progress command as `./bin/progress ...` and SHALL NOT render the host
Python path or absolute CLI path.

**Shim implementation language**: the shim MUST use either `jq` or a
`python3` shebang for the JSON encoding step. Hand-rolled POSIX-sh string
concatenation SHALL NOT be used, because robust JSON escaping for
`--message`/`--challenge` values containing `"`, `\`, control characters, or
non-ASCII bytes is non-trivial in raw `/bin/sh` and would silently produce
invalid JSONL that breaks the host import. The implementation in this change
uses a `python3` shebang and MUST fail closed (non-zero exit) when `python3` is
not on `PATH`. The prompt SHALL require Hermes to stop and propagate that
failure. Because an in-sandbox probe is explicitly deferred, the host runner
classifies the propagated non-zero execution as infrastructure failure but
does not independently inspect interpreter availability inside Docker.

The host runner SHALL live-tail `./logs/progress-events.jsonl` from a
background reader (poll interval ≤ 2s) and write corresponding events through
the existing `ProgressStore` so dashboard progress remains near-real-time
during long Hermes invocations. The runner SHALL also flush remaining records
once Hermes exits (catch-up read) before validation events are written. Read
`input/manifest.json` once at workspace creation for shard/worker/category
context; combine with each tailed record.

#### Scenario: Progress shim survives special-character values

- **GIVEN** Hermes runs `./bin/progress --challenge web-0001 --stage build
  --message 'fix \"quoted\" path / newline\\n'`
- **WHEN** the shim writes the record
- **THEN** the resulting JSONL line parses as a valid JSON object
- **AND** the host import does not fail or skip the record

#### Scenario: Build prompt uses local progress shim

- **WHEN** a build prompt is rendered for a claimed shard
- **THEN** it refers to the progress helper as `./bin/progress`
- **AND** it does not contain the host Python executable path
- **AND** it does not contain the absolute path to the project CLI script

#### Scenario: Progress shim recovers context from manifest

- **GIVEN** a workspace `work/executions/<id>/` with a valid `input/manifest.json`
- **WHEN** Hermes runs `./bin/progress --challenge web-0001 --stage build`
- **THEN** the shim appends a JSONL record under `./logs/progress-events.jsonl`
- **AND** the host runner imports the record with shard/worker/category
  context from `input/manifest.json`
  without requiring additional model-visible CLI flags

