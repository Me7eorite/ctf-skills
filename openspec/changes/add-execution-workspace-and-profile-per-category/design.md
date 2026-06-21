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
profile `cf-<category>`, for example `cf-web`. The argv injection should reuse
the existing research helper semantics: find the `chat` subcommand in the
resolved Hermes argv and insert `-p <profile>` immediately before it, falling
back to insertion after the executable/wrapper prefix when `chat` is absent.
The change must not migrate existing research/design profile bindings.

The runner sets subprocess `cwd` to the execution workspace. Profile
`terminal.cwd` may be configured for manual profile use, but the host runner's
explicit `cwd` is authoritative for this path.

### Decision 5: preflight is mandatory and fail-closed

Before invoking Hermes, the runner verifies:

- `input/shard.json` exists, is regular, and parses as JSON.
- Every challenge in the shard has the same supported category.
- The derived category matches the selected profile.
- `output/` exists and is writable.
- The workspace does not contain unrelated challenge artifacts. Any directory
  entry matching `(web|pwn|re)-\d+` whose challenge id is not in the claimed
  shard is rejected.
- Symlinks in `references/` resolve only to allowed static reference roots.

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

### Decision 7: workspace reuse is fail-closed

`build_attempt_id` gives attributed build shards a stable workspace id. If a
workspace with that id already exists, the runner must either remove only the
owned workspace subtree and recreate the fixed layout, or fail preflight before
Hermes. It must not merge new input with stale `input/`, `output/`, or
reference content.

Manual shards get `manual-<uuid>` for each invocation. They intentionally do
not have a stable retry id until execution rows exist.

### Decision 8: category scope follows the live build queue

The build queue currently supports `web`, `pwn`, and `re` through
`core.queue.SUPPORTED_CATEGORIES`. This change may derive `cf-<category>` only
for categories accepted by the live build queue. It must not imply that
research-only or future categories can already be built by the shard runner.

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

## Risks / Trade-offs

- The workspace is not a security sandbox. A malicious or confused model may
  still access files available to the OS user. The later publisher allowlist is
  still required before treating output as safe.
- Symlinking large references avoids copying, but requires strict preflight
  target checks.
- Manual shards without build-attempt attribution cannot get stable DB-backed
  execution ids yet; `manual-<uuid>` is intentionally temporary.
