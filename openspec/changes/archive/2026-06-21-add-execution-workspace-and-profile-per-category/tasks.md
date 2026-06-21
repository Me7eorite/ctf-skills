## 1. Workspace Path Contract

- [x] 1.1 Add `ProjectPaths.executions` returning `work/executions` and include
  it in `ProjectPaths.initialize()`.
- [x] 1.2 Add a helper that derives `workspace_id` from build-attempt payloads
  or creates `manual-<uuid>` for legacy shards.
- [x] 1.3 Create the fixed workspace directories: `input/`, `references/`,
  `output/`, `logs/`, and the `bin/` helper shim directory.
- [x] 1.4 Write `input/manifest.json` with shard identity, category,
  build-attempt/design-task attribution when present, worker, timestamps, and
  hashes.
- [x] 1.5 If the derived workspace already exists, remove only the owned
  workspace subtree and recreate it, or fail preflight before Hermes.
- [x] 1.6 Before creating a new workspace, run a minimal self-GC pass over
  `work/executions/manual-*`: delete subtrees with mtime older than 7 days or
  empty/orphaned; never touch attributed `build_attempt_id` workspaces (those
  are owned by the later publisher change). GC errors log a warning and do
  not block workspace creation.
- [x] 1.7 Redirect Hermes per-shard `log_path` from `paths.logs /
  f"{shard_name}.log"` to `work/executions/<workspace_id>/logs/hermes.log`.
  Preserve research/design log paths unchanged.
- [x] 1.8 Render the structured report path as `./logs/report.json` in the
  prompt, then import/sync it to the legacy
  `work/reports/<running-shard-stem>.report.json` path before existing report
  consumers run.

## 1A. Shared Profile-Injection Helper

- [x] 1A.1 Extract the duplicated `_build_arguments(profile_name)` from
  [src/hermes/research.py](src/hermes/research.py) and
  [src/hermes/design.py](src/hermes/design.py) into a single public helper
  (e.g. `inject_profile_argument`) in
  [src/hermes/process.py](src/hermes/process.py).
- [x] 1A.2 Migrate research and design call sites to the shared helper; assert
  no behavior change via existing research/design tests.
- [x] 1A.3 The new build runner MUST use the same shared helper (consumed in
  §3.3 below).

## 2. Input Materialization and Preflight

- [x] 2.1 Copy the claimed running shard to `input/shard.json`. Apply the
  materialize strategy: dynamic per-claim files (`shard.json`, `manifest.json`,
  generation profile snapshot) are copied. Copy only the current category's
  required Markdown guidance into `references/`; do not create external
  symlinks that break when Docker mounts only `work/executions/`.
- [x] 2.2 Materialize the minimal generation/profile/reference context needed
  by the build prompt.
- [x] 2.3 Reject unreadable or malformed `input/shard.json` before invoking
  Hermes.
- [x] 2.4 Reject category/profile mismatches.
- [x] 2.5 Reject workspaces containing unrelated challenge artifact names
  (regex `(web|pwn|re)-\d+` with id not in the claimed shard; resolve symlinks
  before matching).
- [x] 2.6 Reject reference symlinks that resolve outside allowed static roots.
- [x] 2.7 Return `status=failed` with `failure_type=infrastructure` on
  preflight failure without invoking Hermes.
- [x] 2.8 Ensure preflight failure moves only the already claimed shard through
  the existing failure path and does not move unrelated pending/running shards.
- [x] 2.9 Preflight MUST verify the selected `cf-<category>` profile exists in
  Hermes via `profile_exists()` (reuse the helper already used by
  [research_agent_executor.py](src/services/research_agent_executor.py)).
  Missing profile returns infrastructure-failed with a message that includes
  the literal recovery command `hermes profile create cf-<category>`.
- [x] 2.10 Manifest `allowed_static_reference_roots` as empty for the copy-only
  strategy; preflight rejects every injected reference symlink. A future
  read-only-mount implementation may populate and validate allowed roots.

## 3. Prompt and Hermes Invocation

- [x] 3.1 Update build prompt rendering to accept workspace-relative paths for
  shard input, references, output, and logs.
- [x] 3.2 Remove host absolute shard/report/challenge-root paths from the build
  prompt contract.
- [x] 3.3 Inject `-p cf-<category>` into build Hermes argv before `chat` using
  the shared helper extracted in §1A.
- [x] 3.4 Invoke Hermes with `cwd` set to the execution workspace.
- [x] 3.5 Preserve research/design Hermes profile binding behavior unchanged.
- [x] 3.6 Keep `hermes -w` out of the required build invocation contract.
- [x] 3.7 Generate the workspace-local progress shim at `./bin/progress` as
  described in Decision 9. The shim MUST use either `jq` or a `python3`
  shebang to encode JSONL; raw POSIX-sh string concatenation is NOT allowed
  (silent invalid-JSON risk on special characters). The shim MUST fail closed
  when neither `jq` nor `python3` is on PATH inside the Hermes terminal
  backend. Render the prompt's progress command as `./bin/progress ...` only.
- [x] 3.8 Implement a live-tailing background reader (poll interval ≤ 2s) in
  the host runner that reads `./logs/progress-events.jsonl` incrementally,
  combines each record with `input/manifest.json` (shard/worker/category/
  workspace_id), and writes events through the existing `ProgressStore`.
  Background reader MUST start before Hermes is invoked and MUST flush any
  remaining records (catch-up read) after Hermes exits but before validation
  events are written. Background reader MUST be cleaned up on every exit
  path (success / failure / preflight reject / KeyboardInterrupt).
- [x] 3.9 Add a shared claimed-shard timeout policy: Re 1800s, Web 2700s,
  Pwn 3600s, and Pwn 5400s when any claimed challenge has
  `difficulty=expert`. Missing/unknown Pwn difficulty uses 3600s.
- [x] 3.10 Resolve the effective timeout after claim with precedence
  `CLI --timeout` > `HERMES_TIMEOUT` > claimed-shard policy. Preserve whether
  CLI/env values were explicitly supplied; do not eagerly substitute the old
  global 1500s default before the runner can inspect the shard.
- [x] 3.11 Apply the derived timeout to the actual Hermes subprocess and
  include `effective_timeout_seconds` and its source (`cli`, `env`, or
  `shard_policy`) in the workspace manifest and Hermes log header.
- [x] 3.12 Update Web UI constrained worker dispatch (the primary entry) to
  use shard-policy timeout by default without requiring an editable timeout
  field. Return the effective timeout in the worker-start response and expose
  it in the build-attempt execution view/status output.
- [x] 3.13 Preserve explicit CLI `--timeout` and `HERMES_TIMEOUT` as
  operational overrides; reject non-positive values with no behavior change
  to research/design timeout configuration.

## 3A. Output Promotion for Existing Validation

- [x] 3A.1 Require Hermes to write claimed challenge directories under
  `./output/challenges/<category>/<id>-<slug>/` or an equivalent fixed
  workspace output layout.
- [x] 3A.2 For resume runs, copy the existing claimed canonical challenge
  directory into the workspace output layout before invoking Hermes.
- [x] 3A.3 Before existing validation runs, promote only directories whose
  challenge ids are present in `input/shard.json` into
  `work/challenges/<category>/`.
- [x] 3A.4 Reject output symlinks, path traversal, missing metadata, metadata
  id/category mismatch, and more than one output directory per claimed id.
- [x] 3A.5 Promote claimed directories atomically via a temporary sibling and
  quarantine any existing canonical directory for the same claimed id under a
  workspace-scoped backup path.
- [x] 3A.6 Reject or ignore unclaimed output directories; do not publish them
  to `work/challenges`.
- [x] 3A.7 Keep the later staged publisher allowlist, execution leases, and
  operator approval out of this change.
- [x] 3A.8 Match output directories to claimed ids by the shard payload's
  `challenges[*].id` set (exact name OR `<id>-<slug>` prefix), NOT by the
  legacy `^(web|pwn|re)-\d+` regex. Real design-task ids look like
  `<category>-<hex8>-<NNNN>` and were silently rejected by the old regex.
  Use the namespace pattern `^(web|pwn|re)-[a-zA-Z0-9][a-zA-Z0-9_-]*$` only
  to identify "this looks like a challenge directory" when deciding what to
  scrutinize, never to extract the id itself.

## 4. Compatibility

- [x] 4.1 Preserve existing constrained claim behavior from
  `add-category-safe-build-dispatch`.
- [x] 4.2 Preserve legacy/manual shard execution using `manual-<uuid>`
  workspaces.
- [x] 4.3 Keep database schema unchanged.
- [x] 4.4 Do not add publisher allowlists, execution leases, agent registry,
  supervisor, slots, feedback APIs, or dashboard pool controls in this change.

## 5. Verification

- [x] 5.1 Add unit tests for `workspace_id` derivation and workspace layout.
- [x] 5.2 Add prompt rendering tests proving build prompts use relative
  workspace paths and omit host absolute shard paths.
- [x] 5.3 Add Hermes argv tests proving `-p cf-<category>` is injected for
  build calls (and that research/design still inject correctly via the shared
  helper introduced in §1A).
- [x] 5.4 Add preflight tests for unreadable input, category mismatch,
  unrelated challenge artifacts, and unsafe reference symlinks.
- [x] 5.5 Add a runner test proving preflight failure does not invoke Hermes.
- [x] 5.6 Add claimed-output promotion tests proving only claimed challenge ids
  are copied to the canonical challenge tree before validation.
- [x] 5.7 Add a regression test with stale `pwn-*` artifacts outside the Web
  workspace proving the Web prompt/log does not expose them.
- [x] 5.8 Add dry-run coverage proving the shard is requeued and no output is
  promoted while the rendered prompt still uses workspace-relative paths.
- [x] 5.9 Add a preflight test that stubs `profile_exists()` to return False
  and asserts: infrastructure-failed outcome, Hermes not invoked, error
  message contains the literal `hermes profile create cf-<category>`.
- [x] 5.10 Add a subprocess-level test asserting the actual subprocess call's
  `cwd` receives the workspace path (NOT `paths.root`) when the runner invokes
  Hermes; do not couple the contract to `run` versus `Popen`.
- [x] 5.11 Add a self-GC test: seed `work/executions/manual-old/` with mtime
  > 7 days and `work/executions/manual-fresh/`; create a new workspace; assert
  `manual-old` is removed and `manual-fresh` is kept. Seed an attributed
  `work/executions/<uuid>/` and assert it is never touched by GC.
- [x] 5.12 Add a progress-spool test proving `./bin/progress` writes JSONL
  without host absolute paths and the runner imports records into
  `ProgressStore` with shard/worker context from `input/manifest.json`. The
  test set MUST include `--message` values containing `"`, `\`, control
  characters (`\n`), and non-ASCII (CJK / emoji) to prove the shim's chosen
  encoder (jq or python3) escapes correctly and the host import parses the
  resulting JSONL without skipping records.
- [x] 5.13 Add report compatibility tests asserting that after the runner
  completes, `domain.reports.merge_reports()` (the Python function, not a
  CLI) returns a merged report containing the entry imported from
  `./logs/report.json`, and that `work/reports/<running-shard-stem>.report.json`
  exists on disk with matching contents.
- [x] 5.14 Add resume promotion tests proving existing claimed canonical
  artifacts are copied into workspace output before Hermes and atomically
  replaced. Quarantine target path MUST be
  `work/executions/<workspace_id>/quarantine/<category>/<dirname>/`;
  unrelated dirs under `work/challenges/<category>/` are not touched.
- [x] 5.15 Add promotion security tests for output symlinks, path traversal,
  duplicate claimed-id directories, and metadata id/category mismatch.
- [x] 5.16 Add a "validation fails after successful promotion" test: assert
  that when `validate.sh` returns non-zero after a successful promotion, the
  new canonical directory stays in place with `solve_status=failed`, the
  quarantined previous version is retained under the workspace, and the
  runner does NOT auto-rollback.
- [x] 5.17 Add a shim-runtime test with `python3` absent from `PATH`: the shim
  MUST exit non-zero. The prompt MUST require Hermes to stop on that error;
  when the non-zero result propagates, the runner records infrastructure
  failure. Do not claim host-side interpreter detection without the deferred
  in-sandbox probe.
- [x] 5.18 Add a live-tailing test: while a long-running fake-Hermes process
  writes JSONL records over time, assert the runner's `ProgressStore` already
  contains those records before the fake-Hermes process exits (proves live
  tailing, not just post-exit catch-up).
- [x] 5.19 Run focused pytest coverage for the changed runner/prompt/workspace
  paths.
- [x] 5.20 Run `openspec validate add-execution-workspace-and-profile-per-category --strict`
  (verified on dev host with the openspec CLI installed; Windows hosts that
  lack the CLI MUST not mark this complete from PowerShell alone).
- [x] 5.21 Add timeout-policy unit tests covering Re 1800s, Web 2700s, Pwn
  3600s, Pwn expert 5400s, mixed Pwn difficulty, missing Pwn difficulty, and
  mixed-category rejection.
- [x] 5.22 Add precedence tests for CLI override, environment override, and
  shard-policy fallback, asserting the exact timeout passed to Hermes.
- [x] 5.23 Add Web API/UI tests proving constrained worker starts use and
  display the derived effective timeout without requiring manual input.
- [x] 5.24 Add a real-id regression test for promotion: a claimed id like
  `web-abcdef12-0001` with output dir `web-abcdef12-0001-demo` MUST promote
  successfully and end up under `work/challenges/web/`.
- [x] 5.25 Add a preflight test for "shim missing": preflight MUST fail
  closed with a message identifying `bin/progress` BEFORE Hermes is invoked.
- [x] 5.26 Add a Windows-skip marker on the workspace test suite (POSIX-only
  scope from Decision 9 + Risks); CI MUST run these on a POSIX host.
- [x] 5.27 Realign CLI `_resolve_run_timeout` behavior with the Web API: an
  invalid `HERMES_TIMEOUT` warns and falls back to shard policy (returncode
  0), instead of exiting non-zero and silently killing dashboard-launched
  worker subprocesses. Cover with both CLI and API tests.

## 6. Operator Runbook and Rollout

- [x] 6.1 Document the one-time bootstrap commands in the project README or
  `docs/`: `hermes profile create cf-web` (plus `cf-pwn`, `cf-re`), and
  optionally `hermes -p cf-<category> config set terminal.cwd "."` for
  consistent manual usage.
- [x] 6.2 Document the Docker backend mount requirement: operators using the
  Docker terminal backend MUST ensure each `cf-<category>` profile mounts the
  host `work/executions/` path into the container at the same in-container
  path (e.g. `./work/executions:/work/executions:rw`). Without this mount,
  host-side preflight passes but the model cannot read `./input/shard.json`.
- [x] 6.3 Mandate a single controlled end-to-end smoke run after enabling this
  change: pick one queued Web shard, run the build runner, and confirm
  Hermes actually reads `./input/shard.json` inside its sandbox (check the
  Hermes log under `work/executions/<id>/logs/`). Only proceed to bulk runs
  if the smoke test passes; this catches Docker mount misconfiguration before
  it produces silent failures at scale.
