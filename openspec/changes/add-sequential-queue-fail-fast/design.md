## Context

The dashboard's "顺序队列构建" feature was added incrementally across
`8c8b6bf2 新增顺序队列构建的功能`, `ad662831 添加顺序队列的前端显示`, and
`c1206181 修改 顺序队列的逻辑`. The operator hands a list of
`build_attempt_id`s to `/api/build-attempts/worker/start-sequential`; the
dashboard spawns a single `python -m cli run …
--build-attempt-sequence <id> --build-attempt-sequence <id> …` subprocess;
that subprocess calls `HermesRunner.run()` once per attempt and aggregates
outcomes.

The aggregation loop today is purely additive: it never inspects the
per-shard outcome shape, so it cannot distinguish "AI Gateway key is dead"
from "this challenge's exploit failed validation". On the lab host, this
produced the cascading failure documented in the proposal: 6 attempts
consumed in under two minutes because a single 401 was indistinguishable
from a real build failure.

Three orthogonal questions need design:

1. **Where should the classification live?** Inside the runner (so every
   future driver — sequence, loop, single-attempt — gets it for free) or
   inside the CLI driver (so the runner stays simple)?
2. **What counts as a fast-fail signal?** Only auth (401), or any
   "non-zero exit in under N seconds", or a curated allowlist of phases that
   also covers shared rate-limit / overloaded conditions?
3. **How is operator intent preserved?** SIGINT today is silently
   reclassified as `infrastructure`; the operator's "stop the queue"
   never reaches the driver.

## Goals / Non-Goals

**Goals**

- Add a stable, finite failure-phase taxonomy for runner-level outcomes
  that is independent of (and orthogonal to) the publisher phase
  taxonomy introduced by `add-staged-publication-allowlist`.
- Make the sequential CLI driver fail-fast on consecutive infrastructure
  failures, with a configurable streak threshold.
- Make cancellation (`returncode < 0`, `KeyboardInterrupt`) an
  unambiguous batch-stop signal at the CLI layer.
- Give the dashboard a way to refuse "obviously doomed" batches before
  spawning the worker.
- Keep all changes additive on the worker JSON contract so the
  `BuildReconciler` and dashboard see the same shape, only richer.

**Non-Goals**

- Defining a new failure taxonomy for `research` runs — that path
  already has `domain/research_failure_taxonomy.py`; we deliberately
  mirror it for `build` without merging the two.
- Active cancellation of running Hermes — the SIGINT path is still
  delivered by `kill`; this proposal only changes how the *next* attempt
  observes that signal.
- Automatic key rotation — operator action remains required when the
  AI Gateway invalidates a key.
- Parallelizing the sequential queue — the queue stays sequential.

## Decisions

### Decision 1: Classification lives in the runner, not the CLI driver

The runner already owns the Hermes subprocess return code, the
`hermes.log` path, the claimed shard payload, and the wall-clock start time. Pushing the
classification down to the runner means:

- `HermesRunner.run()` and `process_one()` return a fully-populated
  outcome dict, so future drivers (sequence, loop, GUI) all share the
  same vocabulary.
- The CLI sequence driver becomes a pure consumer — it never re-parses
  log files or re-reads exit codes.

The runner exposes the classification by adding two new keys to its
existing failure outcome dict:

```python
{
    "status": "failed",
    "failure_type": "infrastructure",   # unchanged
    "hermes_phase": "hermes_auth",      # NEW
    "elapsed_seconds": 8.4,             # NEW (float, monotonic-clock based)
    "timeout_source": "shard_policy",  # NEW when available
    "shard": ...,
    "returncode": ...,
    "error": ...,
}
```

`failure_type` keeps its existing semantics (the BuildReconciler still
reads it). `hermes_phase` is the new refinement. The driver layer only
needs the new keys.

### Decision 2: Nine runner-level phases, parallel to (not unified with) the publisher's nine

The publisher already owns nine phases (`contract`, `allowlist`,
`policy`, `limits`, `stage`, `commit`, `manifest`, `rollback`,
`recovery`). Merging them with the runner-level phases creates a 17-name
namespace that is hard to reason about and that conflates "we failed to
call Hermes" with "we failed to publish what Hermes produced".

The runner-level phases are deliberately kept in a different field name
(`hermes_phase`) and a different list:

| `hermes_phase` | Trigger |
| -------------- | ------- |
| `preflight_workspace` | `preflight_workspace()` raised `WorkspacePreflightError`. |
| `materialize` | Workspace setup/resume/shim/output-promotion raised `OSError` / `WorkspacePromotionError` / `ValueError`. |
| `contract_prepare` | `prepare_publication_contract` raised before Hermes invocation. |
| `hermes_auth` | A structured error marker reports `authentication_error` / auth-like code, OR Hermes exited with `returncode == 1`, `elapsed_seconds < 30`, AND the last 4 KB of `hermes.log` contains auth/key/401-specific provider text such as `Anthropic 401`, `gic密钥`, or API-key invalidation text. Generic `invalid_request_error` alone is not enough. |
| `hermes_rate_limit` | A structured error marker reports `rate_limit_error`, `overloaded_error`, or 429, OR the last 4 KB of `hermes.log` contains provider rate-limit/overload context such as `rate_limit`, `rate limit`, `overloaded_error`, or status-code 429 adjacent to provider error text. A bare `429` inside generated challenge output is not enough. |
| `hermes_runtime` | Hermes exited with a non-zero returncode that doesn't match any other rule. |
| `hermes_timeout` | `returncode == HERMES_TIMEOUT_RETURNCODE` (124) and timeout recovery did not complete. |
| `hermes_cancelled` | `returncode < 0` (Unix signal-encoded) OR a `KeyboardInterrupt` was caught inside `_process_real`. |
| `validation` | Hermes succeeded but one or more per-challenge validation results are still `failed` after the configured validation-repair attempts. |

When the publisher fails, the runner records `failure_type=infrastructure`
and the publisher's own `phase` separately under `publisher_phase`; the
two fields coexist. (This proposal does NOT change publisher phase
emission — that lives in `add-staged-publication-allowlist`.)

Rationale for `elapsed_seconds < 30` as part of the auth tail fallback: a real
build failure under cf-web takes minutes; a 401 fails inside the SDK
before the first model token is requested, typically under 5 seconds.
The combined "fast exit + auth keyword in tail" heuristic is robust
against false positives on long real failures that happen to contain
the word "401" inside generated payloads. The threshold is configurable
via `BUILD_HERMES_FAIL_FAST_MIN_SECONDS` (positive integer, default 30).

Structured markers are preferred over tail matching because the 4 KB tail can
miss the useful line when a stack trace follows it, and localized gateway text
can drift. The current build `invoke()` streams stdout/stderr directly to the
log, so marker production must be either bounded post-exit log scanning or an
equivalent streaming tee. It must not require capturing the full build stdout
in memory. The marker is intentionally tiny and sidecar-based:

```json
{
  "type": "error",
  "error_type": "authentication_error",
  "status_code": 401,
  "source": "hermes_sdk"
}
```

Only type/status metadata is persisted; API key material and full response
bodies are never written to the marker. The sidecar path is derived from the
log path as `log_path.with_name(log_path.name + ".error_marker.json")`.

### Decision 3: CLI sequence driver becomes an explicit state machine

The sequence driver in `src/cli.py:992–1011` is rewritten as:

```python
INFRA_PHASES = {"preflight_workspace", "contract_prepare", "hermes_auth", "hermes_rate_limit"}
CANCEL_PHASES = {"hermes_cancelled"}
STREAK_THRESHOLD = int(os.environ.get(
    "BUILD_SEQ_INFRA_FAILFAST_STREAK", "2"
))

infra_streak = 0
abort_reason: str | None = None
aborted_attempts: list[str] = []
interrupted_attempt: str | None = None

for index, attempt_id in enumerate(args.build_attempt_sequence):
    try:
        item = runner.run(args.worker, ..., build_attempt_id=attempt_id)
    except KeyboardInterrupt:
        abort_reason = "interrupt"
        interrupted_attempt = str(attempt_id)
        aborted_attempts = [str(a) for a in args.build_attempt_sequence[index + 1:]]
        break

    outcomes.extend(item["outcomes"])
    processed += item["processed"]
    failed += item["failed"]

    last = item["outcomes"][-1] if item["outcomes"] else {}
    phase = last.get("hermes_phase")
    if phase in CANCEL_PHASES:
        abort_reason = "interrupt"
        aborted_attempts = [str(a) for a in args.build_attempt_sequence[index + 1:]]
        break
    if phase in INFRA_PHASES:
        infra_streak += 1
        if infra_streak >= STREAK_THRESHOLD:
            abort_reason = "consecutive_infra"
            aborted_attempts = [str(a) for a in args.build_attempt_sequence[index + 1:]]
            break
    else:
        infra_streak = 0

for aborted_id in aborted_attempts:
    outcomes.append({
        "status": "aborted",
        "shard": aborted_id,
        "abort_reason": abort_reason,
    })
```

The driver only consumes `outcome["hermes_phase"]`; it does **not**
re-read log files or re-execute the runner's classifier. `validation`
and `hermes_runtime` deliberately do NOT contribute to the streak
counter — they're real per-challenge failures, not infrastructure.
`hermes_rate_limit` does contribute because it normally represents a shared
gateway/provider condition that will affect the next attempt too.

The final JSON gains two new top-level keys:

```json
{
  "processed": N,
  "failed": N,
  "outcomes": [...],
  "requested": N,
  "abort_reason": "consecutive_infra" | "interrupt" | null,
  "aborted": ["<attempt_id>", ...],
  "interrupted_attempt": "<attempt_id>" | null
}
```

### Decision 4: `aborted` outcomes are first-class, not silent gaps

When the driver short-circuits, every attempt in the tail receives a
synthetic outcome:

```python
{"status": "aborted",
 "shard": "<attempt_id>",
 "abort_reason": "consecutive_infra"}
```

This is intentionally not the same shape as a `failed` outcome:
`failed` means "we ran it and it didn't work"; `aborted` means "we
chose not to run it." The BuildReconciler treats `aborted` as a
no-op — the underlying `build_attempt` row stays in its current
state (queued / running / failed) and the operator can re-submit it
without going through retry/clean-rebuild. The dashboard renders
`aborted` rows differently from failed ones, with the
`abort_reason` text shown alongside.

The in-flight attempt that receives `KeyboardInterrupt` is not treated as a
synthetic `aborted` tail item. Current runner behavior can already claim the
shard and mark it failed before re-raising the interrupt, so the sequence
result records it separately as `interrupted_attempt`. Only attempts after that
point are synthetic `aborted` outcomes.

Because there is no `build_attempts.last_attempt_note` column in the current
schema, this proposal does not invent a migration just to persist aborted
display text. Instead, the sequential worker writes its final JSON to
`work/logs/dashboard-sequential-worker-result.json`, and the dashboard reads
that file when rendering the queue result chip. A durable DB note can be a
later proposal if operator history across restarts becomes a requirement.

### Decision 5: SIGINT / `returncode < 0` becomes `hermes_cancelled`, not `infrastructure`

Today, the runner's exit-code branch at `runner.py:670–690` calls
`_mark_shard_failed(..., f"Hermes exited with {returncode}", returncode)`
for everything non-zero, including the SIGINT-encoded
`returncode == -2`. The reporter's incident shows this is wrong: the
operator's intent ("stop the queue") was reclassified as "infrastructure
failure" and the driver kept going.

New rule:

- If `returncode < 0`, the runner sets `hermes_phase = "hermes_cancelled"`
  AND `failure_type = "infrastructure"` (unchanged for reconciler).
  The driver sees the `cancelled` phase and stops the batch
  immediately.
- If `KeyboardInterrupt` is caught inside `_process_real` (existing
  branch at `runner.py:625–637`), the same classification is applied
  before re-raising, so the CLI driver's `except KeyboardInterrupt`
  also stops the batch with `abort_reason = "interrupt"`.

Both paths converge on the same batch-level behavior: **the queue stops at the
cancellation point, the interrupted attempt is identified explicitly when the
driver knows it, and the unstarted tail is marked `aborted`.**

### Decision 6: Dashboard/API preflight is local-filesystem only

The sequential-worker HTTP path runs a preflight check before calling
`dashboard_tasks.start_sequential_worker(...)`:

```python
def hermes_profile_health(profile_name: str) -> tuple[bool, str, str]:
    """Read-only sanity check on ~/.hermes/profiles/<profile>/.

    Returns (ok, error_code, message). Never contacts the upstream LLM.
    """
```

The check inspects only:

1. `~/.hermes/profiles/<profile>/` exists and is readable.
2. `~/.hermes/profiles/<profile>/.env` exists, is readable, and
   contains a non-empty value for at least one of `ANTHROPIC_API_KEY`
   or `ANTHROPIC_TOKEN`.
3. The Hermes CLI `profile_exists(profile_name)` returns True
   (existing helper at `src/hermes/process.py:341`).

On failure the HTTP endpoint returns `409` with a stable top-level error code,
a Chinese message, and an `errors[]` array for multi-category sequences, for
example `{"profile":"cf-pwn","error_code":"hermes_profile_key_missing","message":"..."}`.
`TaskManager.start_sequential_worker` keeps its existing `(ok, message)` spawn
contract; it does not need repository access to discover categories.

**The preflight does NOT make a network call.** A network call would
add 5–30 seconds of latency per submission and could itself fail in
ways unrelated to the operator's batch. The accepted trade-off: a
key that was valid at preflight time but rotated five seconds later
is caught by Decision 3's fail-fast streak, not by preflight.

The helper MAY support an opt-in `BUILD_PREFLIGHT_PING=1` mode in a later
operator-authenticated profile-management change. That mode is deliberately
out of scope here because the current dashboard has no authentication and this
proposal must remain offline by default.

The endpoint/service layer uses each attempt's `design_task.category` to derive
distinct profile names (for example, `cf-web` and `cf-pwn`) and checks each
profile once. The dashboard already loads attempt category data for the queue
view; the start endpoint may reuse the same repository path instead of pushing
category lookup into `TaskManager`.

### Decision 7: Configurability is environment-variable based

Two knobs, both with sane defaults:

- `BUILD_SEQ_INFRA_FAILFAST_STREAK` (default `2`) — number of
  consecutive infrastructure-class outcomes that triggers an abort. This is a
  non-negative integer; `0` disables fail-fast.
- `BUILD_HERMES_FAIL_FAST_MIN_SECONDS` (default `30`) — the
  "fast exit" boundary for `hermes_auth`. This is a positive integer.

Neither knob has a dashboard control. They are intentionally invisible
to typical operators; they exist for incident investigation and for
the CI environment where the streak is set to `0` to disable fail-fast
on transient sandbox auth failures. `0` is treated as "disabled";
negative values are rejected at CLI startup.

### Decision 8: Effective timeout/source is diagnostic evidence, not a prerequisite bug

The reporter's analysis included a 1500-second timeout log observation. Live
code shows the current explicit sequential path does not directly use 1500
when no CLI/env timeout is set: `src/cli.py::_resolve_run_timeout()` returns
`(None, None)`, and `HermesRunner._process_real()` derives
`effective_timeout = shard_timeout_policy(payload)`.

This proposal must therefore avoid implying a confirmed current timeout bug.
Instead, it requires diagnostics and tests that preserve the current policy
chain:

```text
CLI --timeout > HERMES_TIMEOUT env var > shard_timeout_policy(payload)
```

Failure outcomes should carry the recorded `effective_timeout_seconds` and
`timeout_source` from the workspace manifest when available. That lets operators
distinguish "Hermes timed out at the configured shard policy" from "a caller
accidentally bypassed policy and used a default fallback" without treating a
single log line as implementation truth.

## Risks and Mitigations

- **R1: A real "first attempt happens to fail with `hermes_auth` shape"
  prematurely stops the queue.** Mitigation: the streak threshold
  defaults to 2, not 1. A single auth-looking failure is not enough to
  abort. Operators can lower it to 1 with the env var for stricter
  queues.
- **R2: The 4 KB log-tail regex match could miss a localized 401
  message in a non-Latin locale.** Mitigation: prefer the structured
  `hermes.log.error_marker.json` sidecar when present, then fall back to the
  observed Chinese `gic密钥` keyword and SDK-shaped text in the bounded tail.
- **R3: A shared gateway throttle could be hidden inside
  `hermes_runtime`, so the queue keeps colliding with the same outage.**
  Mitigation: promote 429/rate-limit/overloaded markers to
  `hermes_rate_limit` and count that phase in the same streak as auth.
- **R4: Preflight passes but the key is revoked between preflight and
  the worker's first Hermes call.** Mitigation: Decision 3's streak
  catches this on attempt #2 (default threshold). Preflight is a
  best-effort filter for the "obviously bad" case, not a guarantee.
- **R5: The `hermes_cancelled` classification depends on the shell
  / supervisor delivering SIGINT in a way that reaches Hermes as a
  negative returncode.** Mitigation: negative return codes are classified when
  the platform reports them, but the runner's `KeyboardInterrupt` branch is the
  mandatory portable path. Signal-returncode regression tests are
  platform-aware.

## Migration

- No data migration. The new fields are additive on the worker's JSON
  output and the runner's per-shard outcome dict.
- Existing single-`--build-attempt` callers continue to receive
  outcomes without the `abort_reason` / `aborted` top-level keys; the
  sequence driver is the only place those keys exist.
- `tests/app/test_runner_resume.py` already exercises the failure
  outcome shape; the new `hermes_phase` field is asserted by new
  tests under `tests/app/test_sequential_queue_failfast.py` and
  `tests/app/test_build_failure_taxonomy.py`.
- Operators who relied on "the queue keeps going through failures" can
  set `BUILD_SEQ_INFRA_FAILFAST_STREAK=0` to restore today's
  behavior. The default is the safer one.
