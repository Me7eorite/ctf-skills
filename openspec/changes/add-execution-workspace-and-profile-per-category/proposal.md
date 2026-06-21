## Why

The build shard runner currently invokes Hermes from the repository root and
renders host absolute paths into the prompt. On this workstation those paths
may not be visible from the Hermes terminal context, especially when Hermes is
configured to use a persistent Docker task workspace. If the model cannot read
the intended shard, it can search stale files from another category and continue
the wrong task.

This change opens the first worker-pool split. It does not add a worker pool,
database execution rows, leases, publisher allowlists, or feedback iteration.
It only gives each build invocation a clean project-owned execution workspace
and invokes Hermes with the category profile that matches the claimed shard.

## What Changes

- Add a local build workspace root under `work/executions/<workspace_id>/`.
- Derive `workspace_id` without a schema change: use `build_attempt_id` for
  attributed build shards and `manual-<uuid>` for legacy/manual shards.
- Materialize only the current input bundle into the workspace:
  `input/shard.json`, `input/manifest.json`, selected reference material, and
  an empty `output/` plus `logs/`.
- Render the build prompt with workspace-relative paths such as
  `./input/shard.json` and `./output/`, not host absolute shard/report paths.
- Invoke build Hermes calls with `-p cf-<category>` and `cwd` set to the
  workspace.
- Run preflight before Hermes: verify input readability, output writability,
  category/profile consistency, and absence of unrelated challenge artifacts
  inside the workspace.
- Fail closed on preflight errors without invoking Hermes.
- Keep existing file-backed shard claim behavior and build-attempt dispatch
  semantics unchanged.

## Capabilities

### Modified Capabilities

- `hermes-execution-protocol`: build Hermes invocations use a per-run local
  workspace, category profile, workspace-relative prompt paths, and mandatory
  preflight.

### New Capabilities

None. This is a protocol hardening change for the existing runner.

## Impact

- **Code**: update `core.paths.ProjectPaths`, `hermes.runner.HermesRunner`,
  `hermes.prompt`, and shared Hermes argv/profile helpers as needed.
- **Database**: no schema change. `workspace_id` is a filesystem/log identifier
  only; persistent `execution_id` is reserved for the later lease/fencing
  change.
- **Filesystem**: add `work/executions/<workspace_id>/input`,
  `references`, `output`, and `logs`.
- **Hermes**: build execution uses `cf-web`, `cf-pwn`, or `cf-re` according to
  the claimed shard category. Git worktree is not part of the runtime
  isolation contract.
- **Compatibility**: legacy/manual shard execution remains available; manual
  shards receive a `manual-<uuid>` workspace id.
- **Tests**: cover workspace creation/materialization, relative prompt paths,
  profile argument injection, preflight failure, and stale cross-category
  artifact invisibility.

