## 1. Workspace Path Contract

- [ ] 1.1 Add `ProjectPaths.executions` returning `work/executions`.
- [ ] 1.2 Add a helper that derives `workspace_id` from build-attempt payloads
  or creates `manual-<uuid>` for legacy shards.
- [ ] 1.3 Create the fixed workspace directories: `input/`, `references/`,
  `output/`, and `logs/`.
- [ ] 1.4 Write `input/manifest.json` with shard identity, category,
  build-attempt/design-task attribution when present, worker, timestamps, and
  hashes.

## 2. Input Materialization and Preflight

- [ ] 2.1 Copy the claimed running shard to `input/shard.json`.
- [ ] 2.2 Materialize the minimal generation/profile/reference context needed
  by the build prompt.
- [ ] 2.3 Reject unreadable or malformed `input/shard.json` before invoking
  Hermes.
- [ ] 2.4 Reject category/profile mismatches.
- [ ] 2.5 Reject workspaces containing unrelated challenge artifact names.
- [ ] 2.6 Reject reference symlinks that resolve outside allowed static roots.
- [ ] 2.7 Return an infrastructure-failed outcome on preflight failure without
  invoking Hermes.

## 3. Prompt and Hermes Invocation

- [ ] 3.1 Update build prompt rendering to accept workspace-relative paths for
  shard input, references, output, and logs.
- [ ] 3.2 Remove host absolute shard/report/challenge-root paths from the build
  prompt contract.
- [ ] 3.3 Inject `-p cf-<category>` into build Hermes argv before `chat`.
- [ ] 3.4 Invoke Hermes with `cwd` set to the execution workspace.
- [ ] 3.5 Preserve research/design Hermes profile binding behavior unchanged.
- [ ] 3.6 Keep `hermes -w` out of the required build invocation contract.

## 4. Compatibility

- [ ] 4.1 Preserve existing constrained claim behavior from
  `add-category-safe-build-dispatch`.
- [ ] 4.2 Preserve legacy/manual shard execution using `manual-<uuid>`
  workspaces.
- [ ] 4.3 Keep database schema unchanged.
- [ ] 4.4 Do not add publisher allowlists, execution leases, agent registry,
  supervisor, slots, feedback APIs, or dashboard pool controls in this change.

## 5. Verification

- [ ] 5.1 Add unit tests for `workspace_id` derivation and workspace layout.
- [ ] 5.2 Add prompt rendering tests proving build prompts use relative
  workspace paths and omit host absolute shard paths.
- [ ] 5.3 Add Hermes argv tests proving `-p cf-<category>` is injected for
  build calls.
- [ ] 5.4 Add preflight tests for unreadable input, category mismatch,
  unrelated challenge artifacts, and unsafe reference symlinks.
- [ ] 5.5 Add a runner test proving preflight failure does not invoke Hermes.
- [ ] 5.6 Add a regression test with stale `pwn-*` artifacts outside the Web
  workspace proving the Web prompt/log does not expose them.
- [ ] 5.7 Run focused pytest coverage for the changed runner/prompt/workspace
  paths.
- [ ] 5.8 Run `openspec validate add-execution-workspace-and-profile-per-category --strict`.

