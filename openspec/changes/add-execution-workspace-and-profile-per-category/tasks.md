## 1. Workspace Path Contract

- [ ] 1.1 Add `ProjectPaths.executions` returning `work/executions` and include
  it in `ProjectPaths.initialize()`.
- [ ] 1.2 Add a helper that derives `workspace_id` from build-attempt payloads
  or creates `manual-<uuid>` for legacy shards.
- [ ] 1.3 Create the fixed workspace directories: `input/`, `references/`,
  `output/`, `logs/`, and the `bin/` helper shim directory.
- [ ] 1.4 Write `input/manifest.json` with shard identity, category,
  build-attempt/design-task attribution when present, worker, timestamps, and
  hashes.
- [ ] 1.5 If the derived workspace already exists, remove only the owned
  workspace subtree and recreate it, or fail preflight before Hermes.
- [ ] 1.6 Before creating a new workspace, run a minimal self-GC pass over
  `work/executions/manual-*`: delete subtrees with mtime older than 7 days or
  empty/orphaned; never touch attributed `build_attempt_id` workspaces (those
  are owned by the later publisher change). GC errors log a warning and do
  not block workspace creation.
- [ ] 1.7 Redirect Hermes per-shard `log_path` from `paths.logs /
  f"{shard_name}.log"` to `work/executions/<workspace_id>/logs/hermes.log`.
  Preserve research/design log paths unchanged.
- [ ] 1.8 Render the structured report path as `./logs/report.json` in the
  prompt, then import/sync it to the legacy
  `work/reports/<running-shard-stem>.report.json` path before existing report
  consumers run.

## 1A. Shared Profile-Injection Helper

- [ ] 1A.1 Extract the duplicated `_build_arguments(profile_name)` from
  [src/hermes/research.py](src/hermes/research.py) and
  [src/hermes/design.py](src/hermes/design.py) into a single public helper
  (e.g. `inject_profile_argument`) in
  [src/hermes/process.py](src/hermes/process.py).
- [ ] 1A.2 Migrate research and design call sites to the shared helper; assert
  no behavior change via existing research/design tests.
- [ ] 1A.3 The new build runner MUST use the same shared helper (consumed in
  §3.3 below).

## 2. Input Materialization and Preflight

- [ ] 2.1 Copy the claimed running shard to `input/shard.json`. Apply the
  materialize strategy: dynamic per-claim files (`shard.json`, `manifest.json`,
  generation profile snapshot) are copied; large static references (skills,
  common guidance) are symlinked into `references/` (or read-only bind-mounted)
  to avoid disk waste on bulk runs.
- [ ] 2.2 Materialize the minimal generation/profile/reference context needed
  by the build prompt.
- [ ] 2.3 Reject unreadable or malformed `input/shard.json` before invoking
  Hermes.
- [ ] 2.4 Reject category/profile mismatches.
- [ ] 2.5 Reject workspaces containing unrelated challenge artifact names
  (regex `(web|pwn|re)-\d+` with id not in the claimed shard; resolve symlinks
  before matching).
- [ ] 2.6 Reject reference symlinks that resolve outside allowed static roots.
- [ ] 2.7 Return an infrastructure-failed outcome on preflight failure without
  invoking Hermes.
- [ ] 2.8 Ensure preflight failure moves only the already claimed shard through
  the existing failure path and does not move unrelated pending/running shards.
- [ ] 2.9 Preflight MUST verify the selected `cf-<category>` profile exists in
  Hermes via `profile_exists()` (reuse the helper already used by
  [research_agent_executor.py](src/services/research_agent_executor.py)).
  Missing profile returns infrastructure-failed with a message that includes
  the literal recovery command `hermes profile create cf-<category>`.
- [ ] 2.10 Manifest allowed static reference roots; preflight resolves every
  reference symlink and rejects targets outside those roots.

## 3. Prompt and Hermes Invocation

- [ ] 3.1 Update build prompt rendering to accept workspace-relative paths for
  shard input, references, output, and logs.
- [ ] 3.2 Remove host absolute shard/report/challenge-root paths from the build
  prompt contract.
- [ ] 3.3 Inject `-p cf-<category>` into build Hermes argv before `chat` using
  the shared helper extracted in §1A.
- [ ] 3.4 Invoke Hermes with `cwd` set to the execution workspace.
- [ ] 3.5 Preserve research/design Hermes profile binding behavior unchanged.
- [ ] 3.6 Keep `hermes -w` out of the required build invocation contract.
- [ ] 3.7 Generate the workspace-local progress shim at `./bin/progress` as
  described in Decision 9: a POSIX shell wrapper that appends compact JSON
  records to `./logs/progress-events.jsonl`. Render the prompt's progress
  command as `./bin/progress ...` only.
- [ ] 3.8 Import (or live-tail) `./logs/progress-events.jsonl` from the host
  runner, combine records with `input/manifest.json`, and write them through
  the existing `ProgressStore` before validation events are written.

## 3A. Output Promotion for Existing Validation

- [ ] 3A.1 Require Hermes to write claimed challenge directories under
  `./output/challenges/<category>/<id>-<slug>/` or an equivalent fixed
  workspace output layout.
- [ ] 3A.2 For resume runs, copy the existing claimed canonical challenge
  directory into the workspace output layout before invoking Hermes.
- [ ] 3A.3 Before existing validation runs, promote only directories whose
  challenge ids are present in `input/shard.json` into
  `work/challenges/<category>/`.
- [ ] 3A.4 Reject output symlinks, path traversal, missing metadata, metadata
  id/category mismatch, and more than one output directory per claimed id.
- [ ] 3A.5 Promote claimed directories atomically via a temporary sibling and
  quarantine any existing canonical directory for the same claimed id under a
  workspace-scoped backup path.
- [ ] 3A.6 Reject or ignore unclaimed output directories; do not publish them
  to `work/challenges`.
- [ ] 3A.7 Keep the later staged publisher allowlist, execution leases, and
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
  build calls (and that research/design still inject correctly via the shared
  helper introduced in §1A).
- [ ] 5.4 Add preflight tests for unreadable input, category mismatch,
  unrelated challenge artifacts, and unsafe reference symlinks.
- [ ] 5.5 Add a runner test proving preflight failure does not invoke Hermes.
- [ ] 5.6 Add claimed-output promotion tests proving only claimed challenge ids
  are copied to the canonical challenge tree before validation.
- [ ] 5.7 Add a regression test with stale `pwn-*` artifacts outside the Web
  workspace proving the Web prompt/log does not expose them.
- [ ] 5.8 Add dry-run coverage proving the shard is requeued and no output is
  promoted while the rendered prompt still uses workspace-relative paths.
- [ ] 5.9 Add a preflight test that stubs `profile_exists()` to return False
  and asserts: infrastructure-failed outcome, Hermes not invoked, error
  message contains the literal `hermes profile create cf-<category>`.
- [ ] 5.10 Add a subprocess-level test asserting `subprocess.Popen(cwd=...)`
  receives the workspace path (NOT `paths.root`) when the runner invokes
  Hermes; covers the cwd-authority claim in Decision 4.
- [ ] 5.11 Add a self-GC test: seed `work/executions/manual-old/` with mtime
  > 7 days and `work/executions/manual-fresh/`; create a new workspace; assert
  `manual-old` is removed and `manual-fresh` is kept. Seed an attributed
  `work/executions/<uuid>/` and assert it is never touched by GC.
- [ ] 5.12 Add a progress-spool test proving `./bin/progress` writes JSONL
  without host absolute paths and the runner imports records into
  `ProgressStore` with shard/worker context from `input/manifest.json`.
- [ ] 5.13 Add report compatibility tests proving `./logs/report.json` is
  imported to `work/reports/<running-shard-stem>.report.json` and
  `merge-reports` still sees the build report.
- [ ] 5.14 Add resume promotion tests proving existing claimed canonical
  artifacts are copied into workspace output before Hermes and atomically
  replaced/quarantined only for claimed ids.
- [ ] 5.15 Add promotion security tests for output symlinks, path traversal,
  duplicate claimed-id directories, and metadata id/category mismatch.
- [ ] 5.16 Run focused pytest coverage for the changed runner/prompt/workspace
  paths.
- [ ] 5.17 Run `openspec validate add-execution-workspace-and-profile-per-category --strict`.

## 6. Operator Runbook and Rollout

- [ ] 6.1 Document the one-time bootstrap commands in the project README or
  `docs/`: `hermes profile create cf-web` (plus `cf-pwn`, `cf-re`), and
  optionally `hermes -p cf-<category> config set terminal.cwd "."` for
  consistent manual usage.
- [ ] 6.2 Document the Docker backend mount requirement: operators using the
  Docker terminal backend MUST ensure each `cf-<category>` profile mounts the
  host `work/executions/` path into the container at the same in-container
  path (e.g. `./work/executions:/work/executions:rw`). Without this mount,
  host-side preflight passes but the model cannot read `./input/shard.json`.
- [ ] 6.3 Mandate a single controlled end-to-end smoke run after enabling this
  change: pick one queued Web shard, run the build runner, and confirm
  Hermes actually reads `./input/shard.json` inside its sandbox (check the
  Hermes log under `work/executions/<id>/logs/`). Only proceed to bulk runs
  if the smoke test passes; this catches Docker mount misconfiguration before
  it produces silent failures at scale.
