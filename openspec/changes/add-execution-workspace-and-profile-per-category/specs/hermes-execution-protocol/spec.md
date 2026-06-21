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

Per-invocation Hermes log output SHALL be written under
`work/executions/<workspace_id>/logs/` (replacing the prior
`work/logs/<shard_name>.log` location for build shards). Research and design
log paths SHALL remain unchanged.

The build prompt SHALL render the structured report path as
`./logs/report.json`. Before existing report consumers run, the runner SHALL
import or sync that workspace report to the legacy
`work/reports/<running-shard-stem>.report.json` path. Existing report summary
behavior that scans `work/reports/*.report.json` SHALL continue to work.

**Materialization strategy** SHALL distinguish per-claim files from static
references. Per-claim files MUST be copied so that claim-time snapshots cannot
be modified retroactively: `input/shard.json`, `input/manifest.json`, and any
small per-claim configuration/profile snapshot. Static reference material
(skills directories, common guidance) MAY be symlinked (or read-only
bind-mounted) into `references/` to avoid duplicating multi-MB data per
execution; preflight MUST reject symlinks resolving outside the allowed static
reference roots (see preflight requirement below).

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

### Requirement: Build preflight fails closed before model invocation

Before invoking Hermes, the build runner SHALL preflight the workspace. The
preflight SHALL verify in order:

1. The selected `cf-<category>` Hermes profile exists on the host (checked via
   the same `profile_exists()` helper already used by research execution).
2. `input/shard.json` exists, is a regular file, and parses as JSON.
3. Every challenge in the shard has one supported category.
4. The category matches the selected `cf-<category>` profile.
5. `output/` exists and is writable.
6. The workspace contains no unrelated challenge artifact names. Unrelated
   names are directory entries matching `(web|pwn|re)-\d+` whose challenge id
   is not present in the claimed shard; symlinks are resolved before matching.
7. Reference symlinks SHALL resolve only to allowed static reference roots.

When preflight fails, the runner SHALL return an infrastructure-failed outcome
and SHALL NOT invoke Hermes. The infrastructure-failed message for a missing
profile SHALL include the literal recovery command
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

### Requirement: Claimed workspace output is promoted for existing validation

Hermes SHALL write candidate challenge artifacts under the workspace output
tree at the fixed layout `./output/challenges/<category>/<id>-<slug>/`. The
build prompt SHALL render this layout into Hermes-visible instructions.
Before running the existing validator, the runner SHALL promote only output
directories whose challenge ids are present in `input/shard.json` into the
canonical `work/challenges/<category>/` tree expected by current resume and
validation code. Promotion matches `./output/challenges/<category>/<id>-*/`
and copies to `work/challenges/<category>/<id>-*/`.

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
invalid JSONL that breaks the host import. The shim MUST fail closed (non-zero
exit) when neither `jq` nor `python3` is on `PATH`.

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
