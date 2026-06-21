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
  per-claim files are copied. Only the current category's required Markdown
  references are copied, avoiding external symlinks that would break when a
  Docker backend mounts only `work/executions/`.
- Render the build prompt with workspace-relative paths such as
  `./input/shard.json`, `./output/`, `./logs/report.json`, and
  `./bin/progress`; never host absolute shard/report/CLI paths.
- Extract the duplicated `_build_arguments(profile_name)` (currently in
  `hermes/research.py` and `hermes/design.py`) into a single shared helper in
  `hermes/process.py`; migrate research, design, and the new build runner to
  it.
- Invoke build Hermes calls with `-p cf-<category>` inserted before the
  `chat` subcommand via that shared helper, and `cwd` set to the workspace.
- Select the build Hermes hard timeout from the claimed shard when the caller
  does not explicitly override it: Re 1800s, Web 2700s, Pwn 3600s, and Pwn
  5400s when any claimed challenge has `difficulty=expert`. Web UI worker
  dispatch is the primary consumer; explicit CLI `--timeout` and the existing
  `HERMES_TIMEOUT` environment override remain supported for operations.
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
- Import `./logs/report.json` into the legacy
  `work/reports/<running-shard-stem>.report.json` path before existing report
  merge/dashboard consumers read it.
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
  `references`, `output`, `logs`, and required `bin/progress` shim. A reused
  workspace id must start from an empty owned workspace or fail preflight.
- **Hermes**: build execution uses `cf-web`, `cf-pwn`, or `cf-re` according to
  the claimed shard category, with category/difficulty-aware hard timeouts.
  Git worktree is not part of the runtime isolation contract.
- **Web UI**: constrained build-worker starts use the timeout derived from the
  selected attempt/shard and expose the effective timeout in the start result
  and build-attempt execution view; no operator text entry is required.
- **Compatibility**: legacy/manual shard execution remains available; manual
  shards receive a `manual-<uuid>` workspace id.
- **Tests**: cover workspace creation/materialization, relative prompt paths,
  profile argument injection, preflight failure, claimed-output promotion, and
  stale cross-category artifact invisibility.
- **Operator runbook**: one-time `hermes profile create cf-{web,pwn,re}` is
  required before enabling this change; for Docker terminal backends, the
  profile config must also mount `work/executions/` into the container.
- **Platform**: POSIX-only shim under `./bin/progress`. The shim writes
  workspace-local JSONL progress records using either `jq` or a `python3`
  shebang (raw POSIX-sh is rejected because robust JSON escaping for special
  characters is non-trivial). For Docker terminal backends, the operator MUST
  ensure one of `jq` or `python3` is present in the image; the shim fails
  closed otherwise. Windows host support is not in scope.
- **Live progress**: the host runner runs a background reader that tails
  `./logs/progress-events.jsonl` with ≤2s poll interval and writes events
  through the existing `ProgressStore`. This preserves the current
  near-real-time dashboard behavior and is required (not optional) by this
  change.
