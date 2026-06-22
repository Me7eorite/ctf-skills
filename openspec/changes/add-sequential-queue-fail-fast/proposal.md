## Source

This change is **not** part of the Worker Pool split plan
(`worker-pool-split-plan.md`). It is a sibling reliability fix on top of the
current `hermes-execution-protocol` and the dashboard's existing sequential
queue feature (introduced by commit `8c8b6bf2 新增顺序队列构建的功能` /
`ad662831 添加顺序队列的前端显示` / `c1206181 修改 顺序队列的逻辑`).

It can land independently of `add-staged-publication-allowlist` because it
only touches the runner's failure return shape, the CLI sequence driver, the
dashboard/API preflight, and the dashboard's latest sequential-worker result
surface; it does NOT touch the publisher, the canonical tree, or the database
schema.

## Reporter-provided incident summary

The lab host `192.168.6.150` ran an explicit sequential build queue ordered
`4d564868 -> ... -> ccce77b0`. Attempt #6 hit `KeyboardInterrupt` inside the
Hermes Anthropic SDK, after which the AI Gateway invalidated the upstream key.
Attempts #7-#12 then failed within roughly eight seconds with 401/auth text in
`work/executions/<build_attempt_id>/logs/hermes.log`. The current driver
treated each non-zero Hermes exit as an ordinary build failure, continued
through the remaining queue, and marked six unrelated `build_attempt_id`s as
`build_failed`.

The incident proves three things at once:

1. runner failures need a stable build-specific taxonomy rather than a single
   `failure_type=infrastructure` bucket;
2. the sequential driver must be a state machine that can stop on repeated
   shared-infrastructure failures and on cancellation;
3. dashboard startup should reject obviously broken local Hermes profiles
   before spawning a long batch.

This proposal supersedes any narrower "downstream build failure type"
backfill-only idea: classification must be produced at runtime where the
return code, Hermes log, elapsed time, and cancellation signal are still
available.

## Why

The dashboard "顺序队列构建" feature lets an operator hand a list of
`build_attempt_id`s to a single long-running worker. The current
implementation drives that list in
`src/cli.py:992–1011`:

```python
for attempt_id in args.build_attempt_sequence:
    item = runner.run(args.worker, ..., build_attempt_id=attempt_id)
    outcomes.extend(item["outcomes"])
    processed += item["processed"]
    failed += item["failed"]
```

The loop reads neither `failure_type` nor `returncode` from each `outcome`,
and the dashboard launcher in `src/web/dashboard.py:89–111` performs no
preflight beyond checking that the list is non-empty.

A real incident on the lab host `192.168.6.150` (see
`work/logs/dashboard-sequential-worker.log`, sequence
`4d564868 → … → ccce77b0`, dated 2026-06-22) exposed three concrete failure
modes that the current driver cannot survive:

1. **Cascading Hermes auth failure.** After attempt #6 (`ced77528-3d40…`)
   triggered a `KeyboardInterrupt` deep inside the Hermes Anthropic client,
   the AI Gateway invalidated the upstream API key. Every subsequent
   attempt #7–#12 — including `1f8da69d-14ee-499c-b789-2d2d3c638405` cited
   by the reporter — terminated within seconds with
   `Anthropic 401 — gic密钥已失效, Token prefix: 5013756-mdux...` in
   `work/executions/<id>/logs/hermes.log` and `returncode=1` in the worker
   log. The sequence driver kept consuming them and burned six attempts'
   worth of `build_failed` state instead of halting.
2. **Cancellation misclassification.** Attempt #6 returned `returncode=-2`
   (process-level SIGINT inside Hermes), but the runner labels every
   non-zero return as `failure_type=infrastructure` and the sequence driver
   keeps going. The operator's interrupt did not actually stop the queue.
3. **No upfront credential health gate.** The dashboard endpoint
   `/api/build-attempts/worker/start-sequential` spawns the worker with no
   visibility into whether the Hermes profile under
   `~/.hermes/profiles/cf-<category>/` is even usable. The reporter has no
   way to learn "the key is dead" before submitting a batch.

The blast radius of these three gaps is "any infrastructure incident during
a sequential batch silently consumes the entire remaining queue", which
matches exactly the symptom the reporter described
(`实现逻辑有问题，多个题目直接报错：Hermes exited with 1`).

## What Changes

- **Modify** `hermes-execution-protocol`: add a stable failure-phase
  taxonomy for the runner-level outcomes that complements the publisher
  phase taxonomy introduced by `add-staged-publication-allowlist`.
  Specifically the runner SHALL classify every non-success return from a
  Hermes invocation into one of
  `preflight_workspace | materialize | contract_prepare | hermes_auth |
  hermes_rate_limit | hermes_runtime | hermes_timeout | hermes_cancelled |
  validation`, and
  carry that classification through `_mark_shard_failed` into the
  per-shard outcome dict.
- **Modify** `hermes-execution-protocol`: make classification prefer a
  structured Hermes error marker over tail regexes. Because the current build
  `invoke()` streams output directly to `hermes.log`, the marker SHALL be
  produced by bounded post-exit log scanning or an equivalent streaming tee,
  not by changing the build path to capture full stdout in memory. The
  classifier consumes that marker first and falls back to bounded log-tail
  heuristics.
- **Modify** `build-orchestration`: define the sequential-queue contract.
  The CLI sequence driver (`src/cli.py` `--build-attempt-sequence`)
  SHALL fail-fast on consecutive infrastructure failures, SHALL propagate
  cancellation as an immediate batch stop (not a single-shard failure),
  and SHALL emit `aborted` outcomes for every attempt it chose not to run
  so that the dashboard and reconciler observe an explicit reason rather
  than missing rows.
- **Modify** `build-orchestration`: require a Hermes credential / profile
  preflight gate in the sequential-worker HTTP path before
  `src/web/dashboard.py::start_sequential_worker` spawns the subprocess. The
  endpoint/service layer resolves attempt categories to profile names, then the
  gate checks `~/.hermes/profiles/<profile>/` read-only, MUST NOT call the
  upstream LLM, and MUST refuse to spawn the worker on negative result.
- **Modify** `build-orchestration`: surface aborted sequential tails to the
  dashboard by writing and reading the latest sequential-worker result JSON.
  This preserves the no-migration boundary while avoiding a dashboard refresh
  that hides the fact that a batch was intentionally stopped.

This proposal does **not**:

- redefine publisher phases or touch `worker-pool-execution`;
- change the existing single-`--build-attempt` path (it remains a single
  attempt and the existing reconciler semantics apply);
- introduce new database columns or migrations;
- change how the validator reads `output/`.

## Capabilities

### Modified Capabilities

- `hermes-execution-protocol`: ADD runner-level failure-phase taxonomy.
- `build-orchestration`: ADD sequential queue fail-fast, cancellation
  propagation, and Hermes credential preflight.

### New Capabilities

- None.

## Impact

- **Code**:
  - `src/cli.py` — sequence driver rewritten as a small state machine
    that consults `outcome["hermes_phase"]` and `outcome["status"]`.
  - `src/hermes/runner.py` — `_process_real` populates `hermes_phase`
    and `elapsed_seconds`; SIGINT / `returncode < 0` paths route through
    `hermes_cancelled` instead of `infrastructure`.
  - `src/hermes/process.py` — `invoke` returns the actual signed
    `returncode` (today's path already returns the int; this proposal
    only formalizes that negative values are reserved for the cancelled
    classification and not collapsed to 1) and writes a bounded
    `hermes.log.error_marker.json` sidecar after SDK-shaped auth/rate-limit
    errors are observed in the log stream.
  - `src/web/build_attempts_endpoints.py` — resolves the sequence's distinct
    categories, calls `hermes_profile_health(profile_name)` for each derived
    `cf-<category>` profile, and surfaces preflight errors as 409-shaped JSON
    for the dashboard frontend.
  - `src/web/dashboard.py` — keeps `start_sequential_worker` responsible for
    spawning the already-approved explicit sequence; it does not need database
    category lookup.
  - New `src/domain/build_failure_taxonomy.py` (parallel to the existing
    `src/domain/research_failure_taxonomy.py`) classifies
    `(returncode, hermes_log_tail, elapsed_seconds, error_marker)` into the
    nine phase names above.
- **Database**: none.
- **Filesystem**:
  - `work/executions/<id>/logs/hermes.log.error_marker.json` records a tiny
    structured marker derived from Hermes/SDK output; it never stores API keys.
  - `work/logs/dashboard-sequential-worker-result.json` records the latest
    structured sequence result so the dashboard can show `aborted` tails after
    refresh. The per-shard workspace under `work/executions/<id>/` is otherwise
    untouched.
- **Operator runbook**: when the queue halts with
  `abort_reason: consecutive_infra`, the operator follows the same
  remediation as today (rotate the Hermes profile key, then re-submit
  the aborted attempts). The terminal worker log and the latest result JSON now
  name the affected attempts in the `outcomes` block.
- **Compatibility**: the existing single-`--build-attempt` and
  legacy `--loop` paths keep their current return shape; only the
  sequential driver gains the new abort fields. The
  `failure_type=infrastructure` value remains, with `hermes_phase`
  added alongside as a refinement; the BuildReconciler already tolerates
  unknown extra keys in the worker outcome dict.
- **Out of scope**:
  - Recovering the AI Gateway key automatically — operator action only.
  - A Hermes Profile / API-key management UI. This proposal creates the
    preflight codes that such a UI can reuse later, but a key-editing UI first
    needs dashboard authentication, masking, single-profile writes, and audit
    logging.
  - Treating the reporter's 1500-second timeout log observation as a confirmed
    current implementation bug. Current code already routes the no-CLI/no-env
    sequential path through `shard_timeout_policy(payload)`; this proposal only
    pins that behavior with diagnostics/tests so future fail-fast classification
    cannot confuse a logging observation with actual timeout source.
  - Per-attempt parallelism (sequential queue stays sequential).
  - Cross-host preflight (only local profile files are inspected).
  - Live cancellation from the dashboard mid-batch — `kill -INT` on
    the worker is still the only stop signal, but it will now be
    honored as a clean abort instead of a misreported failure.

## Forward compatibility note

When `add-execution-lease-and-fencing` (proposal #3 of the worker pool
split) introduces an `execution_kind` column and an explicit lease, the
`abort_reason` field added here can be promoted from a JSON-only field
into a typed enum on the new execution row without changing the
sequence-driver state machine. The Hermes credential preflight stays
purely local-filesystem until then; only proposal #3's fencing token
allows a lease-aware preflight against a remote control plane.
