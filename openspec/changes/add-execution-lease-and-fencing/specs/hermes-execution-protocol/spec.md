## MODIFIED Requirements

### Requirement: Build Hermes invocations use a local execution workspace

For non-dry-run build shard execution, the runner SHALL create a local
workspace under `work/executions/<workspace_id>/` before rendering the build
prompt or invoking Hermes. `workspace_id` SHALL be the shard payload's
top-level `build_attempt_id` when present and valid — the **per-build-session
container** id, which is now stable across retry/revision iterations (those
append `executions` rows under the same container rather than minting a new
build attempt; see capability `worker-pool-execution`). For legacy/manual shards
without build-attempt attribution, the runner SHALL use `manual-<uuid>`.

The workspace SHALL be **two-layer**:

- Container level: `references/` (materialized once per container) and
  `attempts/` (the archive of prior iterations).
- Active iteration: `current/`, containing `input/`, `output/`, `logs/`, and
  any `bin/` helper shim directory needed by the rendered build prompt.

The runner SHALL set the Hermes subprocess `cwd` to
`work/executions/<workspace_id>/current/`, so the existing workspace-relative
prompt paths (`./input/shard.json`, `./output/`, `./logs/report.json`) resolve
inside the active iteration without change.

The runner SHALL copy the claimed running shard to `current/input/shard.json`
and SHALL write `current/input/manifest.json` with the workspace id, original
shard basename, running shard basename, worker, category, build attempt id when
present, design task id when present, the active `iteration_no`, creation
timestamp, and input hashes.

Per-invocation Hermes log output SHALL be written under
`work/executions/<workspace_id>/current/logs/` (replacing the prior
`work/logs/<shard_name>.log` location for build shards). Research and design
log paths SHALL remain unchanged.

The build prompt SHALL render the structured report path as
`./logs/report.json`. Before existing report consumers run, the runner SHALL
import or sync that workspace report to the legacy
`work/reports/<running-shard-stem>.report.json` path. Existing report summary
behavior that scans `work/reports/*.report.json` SHALL continue to work.

**Materialization strategy** SHALL copy per-claim files so claim-time snapshots
cannot be modified retroactively: `current/input/shard.json`,
`current/input/manifest.json`, and the generation-profile snapshot. It SHALL
also copy only the selected category's required Markdown guidance into the
container-level `references/`. This change SHALL NOT create repository-external
reference symlinks because Docker profiles are only required to mount
`work/executions/`; such symlinks would be broken in that backend.
`current/input/manifest.json` SHALL record `allowed_static_reference_roots: []`,
and preflight SHALL reject any injected reference symlink.

**Iteration advance (replaces wholesale recreation).** When a workspace already
exists for the derived workspace id and a new iteration is being prepared, the
runner SHALL atomically rename the entire prior `current/` directory to its
canonical zero-padded archive `attempts/iter-NNN/` (where `NNN` is the prior
iteration's `iteration_no`), then create a fresh empty `current/`. It SHALL NOT
move only the children of `current/`, and it SHALL NOT delete the `attempts/`
history. The atomic rename ensures a stale process that still holds the old
`current/` as its cwd cannot resolve relative writes into the new iteration's
`current/`. The `exit_class` and other run metadata SHALL be retained in the
database / manifest rather than encoded into the archive directory name.

Workspace reclamation SHALL be **per-container bounded**: the runner SHALL keep
only the most recent N archived iterations under each container's `attempts/`
(default: last 20 or 7 days, whichever is stricter), reclaim stale
`manual-<uuid>` containers (empty, orphaned, or older than 7 days), and SHALL
NOT let GC errors block new workspace creation. A dry-run SHALL NOT perform GC.

The execution workspace is keyed by the container id; the per-run state of
record is the `executions` row (capability `worker-pool-execution`). The
workspace id remains a filesystem key and is not itself a database primary key.

#### Scenario: Build-attempt shard gets stable container workspace id

- **GIVEN** a claimed shard payload contains `build_attempt_id = A`
- **WHEN** the build runner prepares the workspace for the initial iteration
- **THEN** it creates `work/executions/A/current/`
- **AND** writes the claimed shard to `work/executions/A/current/input/shard.json`
- **AND** the Hermes subprocess cwd is `work/executions/A/current/`

#### Scenario: New iteration archives the prior current directory atomically

- **GIVEN** `work/executions/A/current/` holds iteration 1's output and logs
- **WHEN** the runner prepares iteration 2 for the same container
- **THEN** the entire prior `current/` is atomically renamed to
  `work/executions/A/attempts/iter-001/`
- **AND** a fresh empty `work/executions/A/current/` is created
- **AND** the `attempts/` history is not deleted

#### Scenario: Stale process write stays in the archived iteration

- **GIVEN** an expired iteration-1 process still has the old `current/` as cwd
- **WHEN** the directory has been atomically renamed to `attempts/iter-001/`
- **THEN** that process's later relative writes land under `attempts/iter-001/`
- **AND** never inside iteration 2's freshly created `current/`

#### Scenario: Per-container attempts retention is bounded

- **GIVEN** a container whose `attempts/` holds more than the retained number of
  iterations
- **WHEN** the runner prepares a new iteration
- **THEN** only the most recent retained iterations are kept and older archives
  are reclaimed
- **AND** GC errors do not block new workspace creation

#### Scenario: Legacy shard gets manual workspace id

- **GIVEN** a claimed legacy shard has no `build_attempt_id`
- **WHEN** the build runner prepares the workspace
- **THEN** it creates `work/executions/manual-<uuid>/current/`

#### Scenario: Build Hermes log lands inside the active iteration

- **WHEN** the build runner invokes Hermes for container `W`
- **THEN** Hermes log output is written under `work/executions/W/current/logs/`
- **AND** no new log file appears under the legacy `work/logs/` for that
  build shard
- **AND** research and design log paths under `work/research/logs/` and
  `work/design/logs/` are unchanged

#### Scenario: Workspace report is visible to legacy report merge

- **GIVEN** Hermes writes `./logs/report.json` in container `W`'s `current/`
- **WHEN** the runner finishes the Hermes invocation
- **THEN** the report is imported to
  `work/reports/<running-shard-stem>.report.json`
- **AND** `merge-reports` can include it without scanning `work/executions`
