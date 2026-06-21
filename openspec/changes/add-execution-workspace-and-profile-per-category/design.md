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
- No staged artifact publisher or final `work/challenges` allowlist.
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

### Decision 3: prompt paths are workspace-relative

The build prompt must refer to paths such as:

- `./input/shard.json`
- `./references`
- `./output`
- `./logs/report.json`

It must not embed host absolute paths for the running shard, challenge output
root, report path, generation profile, or design references. Existing host
absolute paths may remain in non-build/research/design prompt paths that are
outside this change.

### Decision 4: category profiles are injected for build only

The build runner derives the category from the claimed shard payload and uses
profile `cf-<category>`, for example `cf-web`. The argv injection should follow
the existing research/design helper pattern by inserting `-p <profile>` before
the `chat` subcommand. The change must not migrate existing research/design
profile bindings.

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

### Decision 6: output is only staging for later changes

Hermes is instructed to write candidate artifacts under `./output/`. This
change does not enforce final publication. Current validation and final
artifact behavior may remain as-is until `add-staged-publication-allowlist`.
The important invariant for this first change is that prompt/runtime context no
longer asks the model to read a host-only shard path or infer the task from a
shared root.

## Risks / Trade-offs

- The workspace is not a security sandbox. A malicious or confused model may
  still access files available to the OS user. The later publisher allowlist is
  still required before treating output as safe.
- Symlinking large references avoids copying, but requires strict preflight
  target checks.
- Manual shards without build-attempt attribution cannot get stable DB-backed
  execution ids yet; `manual-<uuid>` is intentionally temporary.

