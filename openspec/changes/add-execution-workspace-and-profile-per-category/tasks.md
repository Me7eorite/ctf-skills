## 1. Workspace Path Contract

- [ ] 1.1 Add `ProjectPaths.executions` returning `work/executions`.
- [ ] 1.2 Add a helper that derives `workspace_id` from build-attempt payloads
  or creates `manual-<uuid>` for legacy shards.
- [ ] 1.3 Create the fixed workspace directories: `input/`, `references/`,
  `output/`, `logs/`, and any required `bin/` helper shim directory.
- [ ] 1.4 Write `input/manifest.json` with shard identity, category,
  build-attempt/design-task attribution when present, worker, timestamps, and
  hashes.
- [ ] 1.5 If the derived workspace already exists, remove only the owned
  workspace subtree and recreate it, or fail preflight before Hermes.

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
- [ ] 2.8 Ensure preflight failure moves only the already claimed shard through
  the existing failure path and does not move unrelated pending/running shards.

## 3. Prompt and Hermes Invocation

- [ ] 3.1 Update build prompt rendering to accept workspace-relative paths for
  shard input, references, output, and logs.
- [ ] 3.2 Remove host absolute shard/report/challenge-root paths from the build
  prompt contract.
- [ ] 3.3 Inject `-p cf-<category>` into build Hermes argv before `chat`.
- [ ] 3.4 Invoke Hermes with `cwd` set to the execution workspace.
- [ ] 3.5 Preserve research/design Hermes profile binding behavior unchanged.
- [ ] 3.6 Keep `hermes -w` out of the required build invocation contract.
- [ ] 3.7 Render any build progress/report helper path as workspace-relative
  and cover it with a regression test.

## 3A. Output Promotion for Existing Validation

- [ ] 3A.1 Require Hermes to write claimed challenge directories under
  `./output/challenges/<category>/<id>-<slug>/` or an equivalent fixed
  workspace output layout.
- [ ] 3A.2 Before existing validation runs, promote only directories whose
  challenge ids are present in `input/shard.json` into
  `work/challenges/<category>/`.
- [ ] 3A.3 Reject or ignore unclaimed output directories; do not publish them
  to `work/challenges`.
- [ ] 3A.4 Keep the later staged publisher allowlist, execution leases, and
  operator approval out of this change.

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
- [ ] 5.6 Add claimed-output promotion tests proving only claimed challenge ids
  are copied to the canonical challenge tree before validation.
- [ ] 5.7 Add a regression test with stale `pwn-*` artifacts outside the Web
  workspace proving the Web prompt/log does not expose them.
- [ ] 5.8 Add dry-run coverage proving the shard is requeued and no output is
  promoted while the rendered prompt still uses workspace-relative paths.
- [ ] 5.9 Run focused pytest coverage for the changed runner/prompt/workspace
  paths.
- [ ] 5.10 Run `openspec validate add-execution-workspace-and-profile-per-category --strict`.
