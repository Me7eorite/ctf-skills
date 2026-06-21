## Context

The current build path has already gained constrained category/build-attempt
claiming, but after claim it still renders host absolute paths and invokes
Hermes from the repository root. That means the model prompt can point at paths
that are not visible from the Hermes terminal backend. The safe first step is
to make the input boundary explicit before adding leases, agents, slots, or
publisher fencing.

Hermes profiles are useful for model/provider/persona state. They are not a
filesystem sandbox. This change therefore treats profiles as configuration
identity and treats the project-owned workspace as the runtime input/output
boundary exposed to the prompt.

## Goals / Non-Goals

**Goals:**

- Create a clean local workspace for each build Hermes invocation.
- Materialize a minimal immutable input snapshot for the claimed shard.
- Render prompt paths relative to the workspace.
- Invoke Hermes from that workspace with a category-matched profile.
- Fail closed before model invocation when workspace preflight fails.
- Preserve current queue, build-attempt, validation, and retry behavior.

**Non-Goals:**

- No database `executions` table or lease/fencing token.
- No general staged artifact publisher or final `work/challenges` allowlist
  beyond the narrow claimed-output promotion required by current validation.
- No agent registry, supervisor, slots, dashboard pool UI, or capacity control.
- No feedback/revision API.
- No Git worktree dependency.
- No claim/dispatch behavior changes beyond using the already claimed shard.
- **No in-sandbox visibility probe for non-local Hermes terminal backends.**
  This change verifies workspace readiness from the host perspective. It
  assumes the configured Hermes terminal backend exposes the workspace path to
  the model (always true for `local` backend; for Docker/remote backends the
  operator must explicitly mount or expose `work/executions/`). A full
  in-sandbox preflight probe is deferred to a later change.

## Decisions

### Decision 1: workspace id is local until execution rows exist

This change uses `workspace_id`, not `execution_id`.

- For build-attempt attributed shards, `workspace_id` is the top-level
  `build_attempt_id`.
- For legacy/manual shards without build-attempt attribution, `workspace_id`
  is `manual-<uuid>`.

The id is used only in `work/executions/<workspace_id>/` and logs. It is not a
database key. The later lease/fencing change may introduce persistent
`execution_id` rows and map them to workspace paths.

### Decision 2: build workspaces have a small fixed layout

Each non-dry-run build invocation gets:

```text
work/executions/<workspace_id>/
  input/
    shard.json
    manifest.json
  bin/
  references/
  output/
  logs/
```

`input/shard.json` is copied from the claimed running shard after claim.
`input/manifest.json` records the workspace id, original shard basename,
running shard basename, worker, category, build attempt id when present,
design task id when present, created timestamp, and input hashes.

Reference material is limited to what prompt rendering needs. Small
configuration snapshots are copied. Large static references may be symlinked
or read-only mounted, but preflight must reject symlinks pointing outside the
allowed reference roots.

If the prompt needs an executable helper, such as the current progress command,
the prompt should point at a workspace-local shim such as `./bin/progress`.
The shim may bridge to the existing project CLI or write runner-importable
local progress records, but the prompt must not ask the model to discover the
task by walking the repository root.

### Decision 3: prompt paths are workspace-relative

The build prompt must refer to paths such as:

- `./input/shard.json`
- `./references`
- `./output`
- `./logs/report.json`

It must not embed host absolute paths for the running shard, challenge output
root, report path, generation profile, design skill, or design references.
Workspace-local helper paths should also be rendered relative to the workspace.
Existing host absolute paths may remain in non-build/research/design prompt
paths that are outside this change.

Dry-run remains a preview operation: it may create an ephemeral workspace or
render from a workspace context, but it must requeue the claimed shard and must
not invoke Hermes or publish output. Dry-run tests still assert the rendered
build prompt uses the same workspace-relative path contract.

### Decision 4: category profiles are injected for build only

The build runner derives the category from the claimed shard payload and uses
profile `cf-<category>`, for example `cf-web`. The argv injection MUST reuse
the existing helper semantics already implemented in
[src/hermes/research.py `_build_arguments`](../../../src/hermes/research.py) and
[src/hermes/design.py `_build_arguments`](../../../src/hermes/design.py): find
the `chat` subcommand in the resolved Hermes argv and insert `-p <profile>`
immediately before it, falling back to insertion after the executable/wrapper
prefix when `chat` is absent. Because that helper is currently duplicated
verbatim across both modules, this change MUST extract a single shared helper
into [src/hermes/process.py](../../../src/hermes/process.py) and migrate
research, design, and the new build runner to it. No behavior change for
research/design profile bindings beyond the import path.

The runner sets subprocess `cwd` to the execution workspace. Profile
`terminal.cwd` is separately controlled by the operator-managed
`config.yaml` of the `cf-<category>` profile and is NOT mutated by the runner.
The runner-supplied subprocess `cwd` is authoritative for path resolution.
A regression test MUST assert the actual `subprocess.Popen(cwd=...)` argument
equals the workspace path (and is not `paths.root`).

Operator runbook (out of code, in `docs/` or `README`): the `cf-<category>`
profile config SHOULD set `terminal.cwd: "."` so that operator-driven manual
`hermes -p cf-web chat` invocations also land in whatever directory the
operator is currently in, matching the runner contract. This is guidance, not
a runtime requirement.

### Decision 5: preflight is mandatory and fail-closed

Before invoking Hermes, the runner verifies in this order:

1. **`cf-<category>` profile exists on the host**. Reuses
   [`profile_exists()`](../../../src/hermes/process.py) (already in production
   use by [`services/research_agent_executor.py`](../../../src/services/research_agent_executor.py)).
   When missing, the infrastructure-failed message MUST include the exact
   bootstrap command `hermes profile create cf-<category>` so the operator
   can recover without reading source.
2. `input/shard.json` exists, is regular, and parses as JSON.
3. Every challenge in the shard has the same supported category.
4. The derived category matches the selected profile.
5. `output/` exists and is writable.
6. The workspace does not contain unrelated challenge artifacts. Any directory
   entry matching `(web|pwn|re)-\d+` whose challenge id is not in the claimed
   shard is rejected. Symlink entries are checked by their resolved target.
7. Symlinks in `references/` resolve only to allowed static reference roots.

Preflight failure returns an infrastructure failure outcome and does not call
Hermes. It must not move unrelated shard files or publish artifacts.

### Decision 6: output is staged, then claimed output is promoted

Hermes is instructed to write candidate artifacts under `./output/`. Because
the current validator and resume evidence still read canonical challenge
directories under `work/challenges/<category>/...`, the runner must promote
only directories for the claimed challenge ids from workspace output back to
the canonical tree before running existing validation.

This is not the later staged publisher allowlist: it does not add execution
rows, fencing tokens, operator approval, or arbitrary output publication.
It is a narrow compatibility bridge so the current validation path can keep
working while prompt/runtime input is isolated.

**Evolution path (deliberate technical debt)**: when the subsequent
`add-staged-publication-allowlist` change lands, the publisher will own this
boundary in full and the "Claimed workspace output is promoted" requirement
added by this change will be REMOVED, replaced by an equivalent (but stricter)
publisher requirement in `worker-pool-execution`. Until that replacement, the
narrow promotion logic in this change is the only path from `./output/` to
`work/challenges`; it MUST NOT be extended to support arbitrary output
publication, operator approval, or anything beyond the literal claimed
challenge ids. Reviewers of follow-up PRs should reject any scope creep in the
narrow promotion code.

### Decision 7: workspace reuse is fail-closed; minimal self-GC for manual workspaces

`build_attempt_id` gives attributed build shards a stable workspace id. If a
workspace with that id already exists, the runner must either remove only the
owned workspace subtree and recreate the fixed layout, or fail preflight before
Hermes. It must not merge new input with stale `input/`, `output/`, or
reference content.

Manual shards get `manual-<uuid>` for each invocation. They intentionally do
not have a stable retry id until execution rows exist. Because manual
workspaces have no natural cleanup trigger (they never go through the future
publisher allowlist), the runner MUST perform a minimal opportunistic GC pass
at workspace creation time:

- Delete any `work/executions/manual-*` subtree whose mtime is older than 7
  days, OR whose top-level directory is empty/orphaned.
- Skip GC for `work/executions/<uuid-shaped-id>` subtrees that match a known
  build_attempt id; their retention is owned by the later
  `add-staged-publication-allowlist` change.
- GC errors (permission, busy file) MUST NOT block the new workspace creation;
  log a warning and continue.

This is intentionally a minimum version: a full bounded retention policy
(`last-N failures` / `quarantine for audit` / etc.) is deferred to the
publisher change. The 7-day window here exists only to prevent unbounded
disk growth on hosts that run many manual shards.

### Decision 8: category scope follows the live build queue

The build queue currently supports `web`, `pwn`, and `re` through
`core.queue.SUPPORTED_CATEGORIES`. This change may derive `cf-<category>` only
for categories accepted by the live build queue. It must not imply that
research-only or future categories can already be built by the shard runner.

### Decision 9: progress helper is a workspace-local shell shim

The current `shard_prompt.md` renders `{progress_command}` as an absolute path
to `python <cli_script_path> progress ...`. Inside an isolated workspace such
a path is meaningless: the model would need to know the host's Python
interpreter and the project root, neither of which we want in the prompt.

The runner SHALL materialize a workspace-local shell shim at
`./bin/progress` whose body is a thin wrapper of the form:

```sh
#!/bin/sh
exec "<host-python>" "<host-cli>" progress --workspace "<workspace_id>" "$@"
```

The shim is generated at workspace creation time with the host paths baked in
once (so the prompt only sees `./bin/progress`). The CLI accepts a new
`--workspace` flag that reads `input/manifest.json` from the workspace to
recover shard/worker/category context. This keeps the prompt path
workspace-relative while preserving the existing progress writing path.

Decisions on alternative transports (Unix socket / HTTP side-channel /
runner-managed progress importer) are deferred to a future change. A shim is
the smallest move that satisfies the workspace-relative prompt contract
without rewriting how progress is reported.

**Platform scope**: the shim is a POSIX `/bin/sh` script. This change is scoped
to POSIX hosts (Linux / macOS), matching the current project deployment
targets. Windows host support is not a goal of this change; a Windows-equivalent
shim (`.cmd` wrapper or a Python entrypoint) can be added later without
breaking the prompt contract, since the prompt only refers to `./bin/progress`.

## Iterative Review Log

The proposal was reviewed against the live repo in 15 passes before this
revision:

1. Review: `HermesRunner._invoke` runs with `cwd=paths.root`. Problem: the
   proposal is justified; Hermes still sees repository-root context. Solution:
   keep workspace `cwd` as the core requirement.
2. Review: `render_prompt` embeds absolute shard, report, challenge, profile,
   skill, and reference paths. Problem: the original prompt contract would
   still leak host paths. Solution: explicitly ban those build prompt paths.
3. Review: `prompts/shard_prompt.md` also renders a host CLI progress command.
   Problem: workspace-relative inputs alone do not make helper execution clear.
   Solution: add workspace-local helper shim guidance or an explicit local
   progress import path.
4. Review: the current validator reads `work/challenges`. Problem: asking
   Hermes to write only `./output` would leave validation unable to find
   artifacts. Solution: add claimed-output promotion before validation.
5. Review: the prior text said output publication is a later change. Problem:
   that was too broad and contradicted validation. Solution: distinguish narrow
   claimed-output promotion from a future publisher allowlist.
6. Review: the spec named a dry-run prompt scenario while proposal said only
   non-dry-run creates workspaces. Problem: inconsistent preview contract.
   Solution: dry-run renders from workspace context but does not invoke or
   publish.
7. Review: research injects `-p` by locating `chat`, not by blindly prefixing
   the argv. Problem: vague insertion could break `uvx ... hermes chat`.
   Solution: require the existing `chat`-index insertion semantics.
8. Review: `build_attempt_id` is a UUID but manual shards are not attributed.
   Problem: path safety and retry diagnostics differ. Solution: UUID-derived
   stable ids for attributed shards, `manual-<uuid>` for manual only.
9. Review: a reused build-attempt workspace could contain stale output.
   Problem: preflight might pass while mixing runs. Solution: recreate the
   owned workspace or fail closed.
10. Review: the live queue supports only `web`, `pwn`, and `re`. Problem:
    `cf-<category>` could imply arbitrary research categories are buildable.
    Solution: bind profile derivation to `SUPPORTED_CATEGORIES`.
11. Review: preflight failure happens after claim. Problem: saying it does not
    mutate terminal queue state conflicts with existing runner failure paths.
    Solution: allow the existing claimed-shard failure path, while forbidding
    unrelated shard movement and output publication.
12. Review: current build-attempt worker dispatch already constrains category
    and attempt id. Problem: this change must not re-spec dispatch. Solution:
    preserve `/api/build-attempts/.../worker/start` behavior unchanged.
13. Review: `ProjectPaths.initialize()` currently creates work, shards,
    reports, logs, research, and design paths. Problem: executions would be
    missing unless initialized. Solution: add `ProjectPaths.executions` and
    include it in initialization.
14. Review: symlinked references can escape the intended material boundary.
    Problem: workspace layout alone is not a safety boundary. Solution:
    preflight resolves reference symlinks against allowed static roots.
15. Review: no schema field currently stores execution identity. Problem:
    using `execution_id` would overreach into lease/fencing work. Solution:
    keep `workspace_id` filesystem-only and reserve DB execution rows.

## Post-Review Patches

The following decisions were added or revised after the initial 15-pass review,
based on cross-checks against the project's `worker-pool-split-plan.md` and a
second-pass re-evaluation against Docker terminal backend reality.

16. Patch: `profile_exists()` already exists in [src/hermes/process.py](../../../src/hermes/process.py)
    and is used by research execution, but the proposal did not require build
    to use it. Solution: D5 promotes the profile-existence check to preflight
    step 1 and mandates an error message containing the literal
    `hermes profile create cf-<category>` recovery command.
17. Patch: manual `manual-<uuid>` workspaces have no natural cleanup trigger
    and would grow unbounded. Solution: D7 adds opportunistic GC for
    `manual-*` subtrees older than 7 days or empty/orphaned; UUID-attributed
    workspaces remain owned by the future publisher change.
18. Patch: `_build_arguments(profile_name)` was duplicated verbatim across
    `hermes/research.py` and `hermes/design.py` (existing technical debt).
    Solution: D4 mandates extraction into a single helper in `hermes/process.py`
    and migration of research, design, and the new build runner to it.
19. Patch: the narrow output promotion in D6 has no defined hand-off to the
    future `add-staged-publication-allowlist`. Solution: D6 now declares the
    "claimed-output promotion" requirement as a deliberate compatibility
    bridge that the publisher change MUST remove and replace; reviewers SHALL
    reject scope creep until that handoff lands.
20. Patch: the runner could not previously distinguish profile-level vs
    runner-level `cwd` precedence. Solution: D4 declares runner-supplied
    subprocess `cwd` authoritative, removes the originally proposed
    "runner MAY mutate profile terminal.cwd" belt-and-suspenders (operators
    can configure that manually if useful), and adds a regression test
    asserting `subprocess.Popen(cwd=...)` actually equals the workspace path.
21. Patch: the original prompt rendered a host Python + CLI absolute path for
    progress reporting. Solution: D9 adds a workspace-local `./bin/progress`
    POSIX shell shim that exec's the host CLI with `--workspace <id>`; the
    CLI reads `input/manifest.json` for context. POSIX-only by design.
22. Patch: this proposal claims to fix "Hermes Docker backend cannot see
    host paths" but its preflight only verifies workspace from the host. If
    the operator's Docker backend does not mount `work/executions/`, the
    workspace path is invisible to the model just like the old host shard
    paths were. Solution: Non-Goals explicitly excludes in-sandbox visibility
    probing; Risks documents the operator mount requirement; this is the
    smallest move that does not pretend to solve a backend-isolation problem
    out of scope.

## Risks / Trade-offs

- The workspace is not a security sandbox. A malicious or confused model may
  still access files available to the OS user. The later publisher allowlist is
  still required before treating output as safe.
- Symlinking large references avoids copying, but requires strict preflight
  target checks.
- Manual shards without build-attempt attribution cannot get stable DB-backed
  execution ids yet; `manual-<uuid>` is intentionally temporary.
- **Hermes terminal backend visibility is the operator's responsibility.** For
  the `local` backend the subprocess `cwd` works out of the box. For the
  Docker backend, the operator MUST configure the `cf-<category>` profile to
  mount the host `work/executions/` path into the container at the same
  in-container path (e.g. `terminal.docker.mounts` entry mapping
  `./work/executions:/work/executions:rw`). Without such mounting, host-side
  preflight passes but the model inside the container cannot read
  `./input/shard.json`. The first run after introducing this change MUST be a
  controlled smoke test that confirms the model can read its claimed shard;
  this catches Docker mount misconfiguration before the change is enabled for
  bulk runs.
- The 7-day GC window for `manual-*` workspaces is a baked-in magic number,
  not yet configurable. Bulk dev/test runs that retain manual workspaces
  beyond 7 days for debugging must rename them out of `manual-*` or accept
  loss. A configurable retention knob is deferred to the publisher change.
- POSIX-only shim. Windows hosts cannot use the build runner under this
  change. This matches current project deployment.
