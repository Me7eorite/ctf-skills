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

**Scope boundary for terminal backends**: workspace readiness is verified from
the host. For the `local` Hermes terminal backend this is sufficient. For the
Docker terminal backend, the operator MUST configure each `cf-<category>`
profile to mount `work/executions/` into the container at the same in-container
path; otherwise host-side preflight passes but the model cannot read the
workspace. An in-sandbox visibility probe is explicitly deferred. The first
post-deployment build run is a controlled smoke test that confirms the model
can actually read its claimed shard inside its sandbox.

## What Changes

- Add a local build workspace root under `work/executions/<workspace_id>/`.
- Derive `workspace_id` without a schema change: use `build_attempt_id` for
  attributed build shards and `manual-<uuid>` for legacy/manual shards.
- Materialize only the current input bundle into the workspace:
  `input/shard.json`, `input/manifest.json`, selected reference material,
  `output/`, `logs/`, and a workspace-local `bin/progress` shim. Dynamic
  per-claim files are copied; large static references are symlinked to avoid
  disk waste on bulk runs.
- Render the build prompt with workspace-relative paths such as
  `./input/shard.json`, `./output/`, and `./bin/progress`; never host absolute
  shard/report/CLI paths.
- Extract the duplicated `_build_arguments(profile_name)` (currently in
  `hermes/research.py` and `hermes/design.py`) into a single shared helper in
  `hermes/process.py`; migrate research, design, and the new build runner to
  it.
- Invoke build Hermes calls with `-p cf-<category>` inserted before the
  `chat` subcommand via that shared helper, and `cwd` set to the workspace.
- Run preflight before Hermes: verify `cf-<category>` profile exists
  (`profile_exists()` reuse), input readability, output writability,
  category/profile consistency, and absence of unrelated challenge artifacts
  inside the workspace. Missing profile message MUST include the exact
  `hermes profile create cf-<category>` recovery command.
- Fail closed on preflight errors without invoking Hermes.
- Run a minimal opportunistic GC pass over `work/executions/manual-*` (>7d or
  empty/orphaned) at workspace creation. Attributed `build_attempt_id`
  workspaces are not touched; their retention belongs to the later publisher
  change.
- Promote only claimed challenge output from `./output/` back to the canonical
  `work/challenges/<category>/...` tree before the existing validation path
  runs; this is an explicit narrow compatibility bridge that will be removed
  by `add-staged-publication-allowlist`.
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
  `references`, `output`, `logs`, and optional `bin` helper shims. A reused
  workspace id must start from an empty owned workspace or fail preflight.
- **Hermes**: build execution uses `cf-web`, `cf-pwn`, or `cf-re` according to
  the claimed shard category. Git worktree is not part of the runtime
  isolation contract.
- **Compatibility**: legacy/manual shard execution remains available; manual
  shards receive a `manual-<uuid>` workspace id.
- **Tests**: cover workspace creation/materialization, relative prompt paths,
  profile argument injection, preflight failure, claimed-output promotion, and
  stale cross-category artifact invisibility.
- **Operator runbook**: one-time `hermes profile create cf-{web,pwn,re}` is
  required before enabling this change; for Docker terminal backends, the
  profile config must also mount `work/executions/` into the container.
- **Platform**: POSIX-only shim under `./bin/progress`. Windows host support is
  not in scope (matches current project deployment).
