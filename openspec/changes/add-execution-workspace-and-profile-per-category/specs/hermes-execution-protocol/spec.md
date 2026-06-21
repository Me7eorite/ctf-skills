## ADDED Requirements

### Requirement: Build Hermes invocations use a local execution workspace

For non-dry-run build shard execution, the runner SHALL create a local
workspace under `work/executions/<workspace_id>/` before rendering the build
prompt or invoking Hermes. `workspace_id` SHALL be the shard payload's
top-level `build_attempt_id` when present and valid. For legacy/manual shards
without build-attempt attribution, the runner SHALL use `manual-<uuid>`.

The workspace SHALL contain `input/`, `references/`, `output/`, and `logs/`.
The runner SHALL copy the claimed running shard to `input/shard.json` and
SHALL write `input/manifest.json` with the workspace id, original shard
basename, running shard basename, worker, category, build attempt id when
present, design task id when present, creation timestamp, and input hashes.

The workspace id is not a database id. This change SHALL NOT add an
`executions` table or require persistent execution rows.

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

### Requirement: Build prompts use workspace-relative paths

The build prompt SHALL expose only workspace-relative runtime paths for the
claimed shard, reference context, output directory, and workspace logs. It
SHALL refer to the shard as `./input/shard.json` and the candidate output root
as `./output/`.

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

### Requirement: Build Hermes calls use category profiles and workspace cwd

The build runner SHALL derive the category from the claimed shard payload and
SHALL invoke Hermes with profile `cf-<category>`. The profile argument SHALL be
inserted into argv before the `chat` subcommand, following the existing
research/design profile invocation style.

The Hermes subprocess `cwd` SHALL be the execution workspace. The build runner
SHALL NOT require Git worktree mode (`-w`) for this contract. Existing
research/design profile binding behavior SHALL remain unchanged.

#### Scenario: Web shard uses Web profile

- **GIVEN** a claimed shard contains only Web challenges
- **WHEN** the build runner invokes Hermes
- **THEN** the argv includes `-p cf-web`
- **AND** the subprocess cwd is `work/executions/<workspace_id>`

#### Scenario: Git worktree is not required

- **WHEN** the build runner invokes Hermes
- **THEN** the argv does not need to include `-w`
- **AND** runtime isolation relies on the project workspace contract, not Git
  worktree behavior

### Requirement: Build preflight fails closed before model invocation

Before invoking Hermes, the build runner SHALL preflight the workspace. The
preflight SHALL verify that `input/shard.json` exists, is a regular file, and
parses as JSON; every challenge in the shard has one supported category; the
category matches the selected `cf-<category>` profile; `output/` exists and is
writable; and the workspace contains no unrelated challenge artifact names.

Unrelated challenge artifact names are directory entries matching
`(web|pwn|re)-\d+` whose challenge id is not present in the claimed shard.
Reference symlinks SHALL resolve only to allowed static reference roots.

When preflight fails, the runner SHALL return an infrastructure-failed outcome
and SHALL NOT invoke Hermes.

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

#### Scenario: Preflight failure does not mutate queue terminal state

- **WHEN** preflight fails after a shard is claimed
- **THEN** Hermes is not invoked
- **AND** no unrelated pending shard is moved
- **AND** no candidate artifact is published

