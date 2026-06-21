## ADDED Requirements

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
immediately before the `chat` subcommand, following the existing research
profile invocation style, with the same fallback behavior when `chat` is not
present.

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
and SHALL NOT invoke Hermes. It MAY move the already claimed shard through the
existing failed-shard path so build-attempt reconciliation can observe the
failure, but it SHALL NOT move unrelated shards or publish workspace output.

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

### Requirement: Claimed workspace output is promoted for existing validation

Hermes SHALL write candidate challenge artifacts under the workspace output
tree. Before running the existing validator, the runner SHALL promote only
output directories whose challenge ids are present in `input/shard.json` into
the canonical `work/challenges/<category>/` tree expected by current resume and
validation code.

This requirement SHALL NOT introduce execution rows, lease/fencing tokens,
operator approval, or a general publisher allowlist. Unclaimed output
directories SHALL NOT be copied to `work/challenges`.

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
