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
configuration snapshots and only the selected category's required Markdown
references are copied. Workspace reference symlinks are not created by this
change because a Docker backend that mounts only `work/executions/` cannot
resolve host-repository symlink targets. The manifest records an empty allowed
static-reference-root list, so preflight rejects any injected reference
symlink. A future read-only mount implementation may populate that allowlist.

If the prompt needs an executable helper, such as the current progress command,
the prompt should point at a workspace-local shim such as `./bin/progress`.
The shim writes runner-importable local progress records; it must not exec a
host absolute Python or project CLI path because those paths are not visible
inside Docker/remote terminal backends. The prompt must not ask the model to
discover the task by walking the repository root.

### Decision 3: prompt paths are workspace-relative

The build prompt must refer to paths such as:

- `./input/shard.json`
- `./references`
- `./output`
- `./logs/report.json`
- `./bin/progress`

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

Preflight failure returns the existing runner status `failed` with
`failure_type="infrastructure"` and does not call Hermes. This preserves the
current run summary and build reconciler contract while distinguishing an
environment/input failure from model or validation failure. It must not move
unrelated shard files or publish artifacts.

### Decision 6: output is staged, then claimed output is promoted

Hermes is instructed to write candidate artifacts under `./output/`. Because
the current validator and resume evidence still read canonical challenge
directories under `work/challenges/<category>/...`, the runner must promote
only directories for the claimed challenge ids from workspace output back to
the canonical tree before running existing validation.

Resume compatibility matters: when a resume plan carries forward existing
claimed artifacts, the runner must materialize the claimed canonical challenge
directory into `./output/challenges/<category>/<id>-<slug>/` before invoking
Hermes. The model then edits the workspace copy, not the canonical tree. After
Hermes returns, promotion replaces or creates only the canonical directories
for claimed ids.

Promotion must be atomic and conservative:

- Reject output symlinks and path traversal; only regular directories below
  `./output/challenges/<category>/` are promotable.
- Require exactly one output directory per claimed challenge id, matching
  `<id>-<slug>`.
- Validate `metadata.json.id` and `metadata.json.category` before promotion.
- Copy to a temporary sibling under `work/challenges/<category>/` and then
  rename into place.
- If an existing canonical directory for the claimed id exists, quarantine it
  to the fixed path
  `work/executions/<workspace_id>/quarantine/<category>/<dirname>/` before
  replacement and retain it until the workspace itself is GC'd. Do not delete
  unrelated challenge directories. Rollback to the quarantined version when
  validation later fails is **not** automatic in this change; it is an
  explicit operator action recovered from the quarantine path.
- If promotion fails, mark the claimed shard failed before validation; do not
  let the reconciler observe a `done` shard with missing canonical artifacts.

Hermes also writes the runner-visible report to `./logs/report.json`. Before
calling `ensure_report`, `merge_validation_into_report`, or any legacy report
merge path, the runner imports that workspace report into the existing
`work/reports/<running-shard-stem>.report.json` location. This preserves
`merge-reports`, dashboard state, and existing tests that scan `work/reports`
while still keeping the prompt workspace-relative.

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

Dry-run MUST NOT run this GC pass and MUST NOT delete or recreate an existing
workspace. Dry-run may create an ephemeral preview workspace and write its
prompt/log there, but the only durable queue mutation remains the existing
claim-and-requeue behavior.

### Decision 8: category scope follows the live build queue

The build queue currently supports `web`, `pwn`, and `re` through
`core.queue.SUPPORTED_CATEGORIES`. This change may derive `cf-<category>` only
for categories accepted by the live build queue. It must not imply that
research-only or future categories can already be built by the shard runner.

### Decision 9: progress helper is a workspace-local JSONL spool shim

The current `shard_prompt.md` renders `{progress_command}` as an absolute path
to `python <cli_script_path> progress ...`. Inside an isolated workspace such
a path is meaningless: the model would need to know the host's Python
interpreter and the project root, neither of which we want in the prompt.

The runner SHALL materialize a workspace-local shim at `./bin/progress` whose
body appends one JSON object per invocation to `./logs/progress-events.jsonl`.
The prompt renders only `./bin/progress`. The shim accepts the same
agent-facing flags as the existing progress command (`--challenge`/`--stage`/
`--status`/`--message`).

**Implementation language is constrained.** The shim MUST use either `jq` or a
`python3` shebang to do the JSON encoding step. Hand-rolled POSIX `/bin/sh`
string concatenation MUST NOT be used: robust escaping for `"`, `\`, control
characters, and non-ASCII bytes in the `--message`/`--challenge` values is
non-trivial in raw shell and would silently produce invalid JSONL that the
host import cannot parse. Two acceptable shapes:

```sh
#!/bin/sh
# Variant A: jq-based (requires jq in PATH)
jq -nc --arg challenge "$CH" --arg stage "$ST" --arg status "$SS" \
       --arg message "$MSG" \
       '{ts:now, challenge:$challenge, stage:$stage, status:$status, message:$message}' \
       >>"./logs/progress-events.jsonl"
```

```python
#!/usr/bin/env python3
# Variant B: python3-based (requires python3 in PATH)
import json, sys, time, argparse, pathlib
# parse args; pathlib.Path("./logs/progress-events.jsonl").open("a").write(json.dumps(...)+"\n")
```

The shim MUST fail closed (non-zero exit) when neither `jq` nor `python3` is
available on `PATH` inside the Hermes terminal backend.

The host runner SHALL live-tail `./logs/progress-events.jsonl` from a
background reader (poll interval ≤ 2s) and write events through the existing
`ProgressStore`. This restores the near-real-time dashboard behavior that
exists today. The runner SHALL also flush remaining records once Hermes exits
(catch-up read) before validation events are written. Without live tailing the
dashboard would only see progress in bursts after each Hermes run completes,
which is a user-visible regression and is therefore NOT acceptable as a final
state for this change.

Decisions on alternative transports (Unix socket / HTTP side-channel /
direct CLI bridge) are deferred to a future change. The JSONL spool is the
smallest move that satisfies the workspace-relative prompt contract without
requiring host absolute paths to exist inside the Hermes terminal backend.

**Platform scope**: the shim is a POSIX `/bin/sh` script. This change is scoped
to POSIX hosts (Linux / macOS), matching the current project deployment
targets. Windows host support is not a goal of this change; a Windows-equivalent
shim (`.cmd` wrapper or a Python entrypoint) can be added later without
breaking the prompt contract, since the prompt only refers to `./bin/progress`.

### Decision 10: report and artifact compatibility stay explicit

Existing consumers still depend on canonical paths:

- `domain.reports.merge_reports()` scans `work/reports/*.report.json`.
- `DashboardService.state()` reads `work/reports/validation.json`.
- `BuildReconciler._artifact()` discovers artifacts only under
  `work/challenges/<category>/<id>-*/metadata.json`.

This change must not silently move those canonical read paths. Workspace
reports and artifacts are runtime staging surfaces; the runner imports reports
and promotes claimed artifacts into the existing canonical surfaces before
legacy consumers run. If a future change moves these consumers, it must do so
as an explicit compatibility migration with its own tests.

### Decision 11: build timeout follows category and Pwn expert difficulty

The build hard timeout is derived only after a shard is claimed and parsed,
because Pwn expert selection depends on the claimed challenge payload:

| Claimed shard | Hard timeout |
| --- | ---: |
| Re | 1800 seconds |
| Web | 2700 seconds |
| Pwn without expert difficulty | 3600 seconds |
| Pwn with any `difficulty=expert` challenge | 5400 seconds |

For a multi-challenge Pwn shard, any expert challenge selects 5400 seconds for
the whole Hermes invocation. Missing or unknown Pwn difficulty uses 3600
seconds. Mixed-category shards remain invalid and fail preflight; timeout
selection does not make them executable.

Timeout precedence is `CLI --timeout` > `HERMES_TIMEOUT` > claimed-shard
policy. The CLI must therefore preserve whether a value was explicitly
provided instead of eagerly replacing an absent value with the old global
1500-second default before claim. Direct runner callers that pass a positive
timeout receive the same explicit-override behavior.

The Web UI is the primary operational entry. Its constrained build-worker
endpoint does not require an editable timeout field: it starts the worker
without an override, the runner derives the policy after claim, and the API/UI
show the effective timeout for observability. This keeps routine dispatch
consistent while retaining CLI/environment escape hatches for incident
response. This decision introduces hard timeouts only; activity/idle timeout
extension is deferred because the current Hermes process log has no reliable
structured activity signal.

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
    POSIX shell shim that appends JSONL to `./logs/progress-events.jsonl`;
    the host runner imports those records with context from
    `input/manifest.json`. POSIX-only by design.
22. Patch: this proposal claims to fix "Hermes Docker backend cannot see
    host paths" but its preflight only verifies workspace from the host. If
    the operator's Docker backend does not mount `work/executions/`, the
    workspace path is invisible to the model just like the old host shard
    paths were. Solution: Non-Goals explicitly excludes in-sandbox visibility
    probing; Risks documents the operator mount requirement; this is the
    smallest move that does not pretend to solve a backend-isolation problem
    out of scope.
23. Patch: `domain.reports.merge_reports()` and dashboard state still read
    `work/reports`, while this proposal moved prompt report paths to
    `./logs/report.json`. Solution: D6/D10 now require importing the workspace
    report back to `work/reports/<running-shard-stem>.report.json` before
    existing report consumers run.
24. Patch: the proposed `./bin/progress` shim originally exec'd the host
    Python and project CLI. Problem: those absolute paths are still invisible
    in Docker terminal backends. Solution: D9 replaces that with a
    workspace-local JSONL spool that the host runner imports.
25. Patch: existing `challenge-factory progress` requires `--shard`, so adding
    `--workspace` would be a larger CLI compatibility change than necessary.
    Solution: remove the required CLI bridge from the proposal; the runner
    imports progress JSONL through `ProgressStore` instead.
26. Patch: resume flow currently reads canonical `work/challenges` evidence.
    Problem: starting Hermes with an empty `./output` would lose carried
    artifacts. Solution: D6 requires materializing claimed canonical dirs into
    workspace output before Hermes.
27. Patch: output promotion could copy malicious symlinks or wrong-category
    metadata. Solution: D6/spec require symlink rejection plus metadata
    id/category validation before atomic promotion.
28. Patch: `BuildReconciler._artifact()` only sees canonical challenge dirs.
    Problem: a done shard before promotion would become `artifact directory
    missing`. Solution: promotion failure marks the claimed shard failed
    before validation/reconciler observes done.
29. Patch: retrying the same challenge can collide with an existing canonical
    directory. Solution: D6 requires quarantine of only the claimed existing
    directory before atomic replacement, never deletion of unrelated dirs.
30. Patch: dry-run should not perform cleanup side effects. Solution: D7 now
    forbids dry-run workspace GC or destructive workspace recreation.
31. Patch: validation mutates `metadata.solve_status` in canonical challenge
    dirs. Solution: D6 makes canonical promotion happen before validation so
    validation updates the visible artifact, not a discarded workspace copy.
32. Patch: report path and Hermes log path were conflated. Solution: per-run
    Hermes stdout/stderr stays in workspace logs, while the structured report
    is imported back to `work/reports` for legacy merge/dashboard consumers.
33. Patch: live dashboard progress may degrade if the JSONL spool is only
    imported after Hermes returns. Solution: D9 allows live tailing but sets
    post-Hermes import as the minimum contract; verification distinguishes the
    two.
34. Patch: materialized static references via symlink require stable allowed
    roots. Solution: preflight must resolve each reference symlink against the
    explicit static roots copied into the manifest.
35. Patch: prompt examples must match the exact output layout. Solution:
    spec now fixes `./output/challenges/<category>/<id>-<slug>/` and rejects
    non-conforming output.
36. Patch: the output bridge is temporary but could become permanent by
    accident. Solution: D6/spec require the later publisher change to remove
    this requirement and replace it under `worker-pool-execution`.
37. Patch: canonical consumer paths are a compatibility boundary, not an
    implementation detail. Solution: D10 names the current consumers and
    forbids silent movement in this change.
38. Patch: quarantine path was described as "workspace-scoped" but not
    specified. Solution: D6/spec lock it to
    `work/executions/<workspace_id>/quarantine/<category>/<dirname>/`.
39. Patch: "live tailing is preferred but not required" was a wish, not a
    contract; without it, dashboard real-time progress regresses. Solution:
    D9/spec promote live tailing to SHALL with ≤2s poll interval.
40. Patch: writing JSONL from raw `/bin/sh` is fragile; messages with `"`,
    `\`, or non-ASCII bytes would silently produce invalid lines. Solution:
    D9/spec require `jq` or `python3` shebang and fail closed when neither is
    on PATH. Tests cover special-character messages.
41. Patch: "what happens when validation fails after a successful promotion?"
    was undefined. Solution: spec scenario "Validation fails after successful
    promotion" clarifies: new canonical stays, validator marks
    `solve_status=failed`, quarantine retained for audit, no automatic
    rollback. Rollback becomes an explicit operator action.
42. Patch: repository-external reference symlinks are not visible when the
    Docker profile mounts only `work/executions/`. Solution: copy the minimal
    selected-category Markdown set, record an empty reference-root allowlist,
    and reject any injected reference symlink.
43. Patch: `infrastructure-failed` was not a valid runner status and would be
    omitted from the existing failed counter. Solution: retain `status=failed`
    and add `failure_type=infrastructure` to the outcome.

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
- JSONL progress spooling requires live tailing to avoid a user-visible
  dashboard regression (without live tailing, progress would only appear in
  bursts after Hermes returns). This change therefore mandates live tailing as
  part of the runner. The trade-off is that the runner now owns a background
  reader thread per active workspace, with explicit cleanup at execution end.
- The progress shim depends on `jq` OR `python3` being available inside the
  Hermes terminal backend. For the `local` backend this is almost always true;
  for Docker backends the operator MUST ensure the chosen interpreter is
  present in the image. The shim fails closed if neither is found, so a
  misconfigured image produces an early infrastructure failure rather than
  silent data loss.
