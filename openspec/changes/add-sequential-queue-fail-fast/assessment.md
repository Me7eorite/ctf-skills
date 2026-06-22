## 20-pass proposal assessment

Each pass follows: analyze proposal -> compare current implementation -> choose
solution -> fold back into the proposal.

| Pass | Analysis | Current implementation check | Resolution folded into proposal |
| ---- | -------- | ---------------------------- | -------------------------------- |
| 01 | The "1500s timeout" wording reads like a confirmed precondition bug. | `src/cli.py::_resolve_run_timeout()` returns `(None, None)` without CLI/env timeout, and `_process_real()` derives `shard_timeout_policy(payload)`. | Reframe as diagnostic guardrail: record/assert effective timeout and source; do not claim a live 1500s bug. |
| 02 | `_invoke()` still has a `DEFAULT_HERMES_TIMEOUT` fallback, which can confuse readers. | `_process_real()` passes an effective timeout; direct `_invoke()` fallback is not the sequential path. | Mention the fallback only as a regression risk to test, not as evidence of current behavior. |
| 03 | Error marker writing cannot assume stdout capture in `invoke()`. | `hermes.process.invoke()` streams combined stdout/stderr directly to `hermes.log`. | Specify bounded post-exit log scanning or streaming tee detection, not full stdout capture. |
| 04 | The marker filename must be deterministic and tied to the log. | Current workspaces already place logs under `work/executions/<id>/logs/`. | Use `log_path.with_name(log_path.name + ".error_marker.json")`. |
| 05 | Cancellation cannot rely only on negative POSIX return codes. | Windows may not report signal exits as negative codes; `KeyboardInterrupt` branch is the portable path. | Make signal-code tests platform-aware and keep `KeyboardInterrupt` classification mandatory. |
| 06 | Synthetic `aborted` for an in-flight `KeyboardInterrupt` can conflict with a shard already moved to failed. | Current runner marks the claimed shard failed before re-raising `KeyboardInterrupt`. | Separate the interrupted attempt from the unclaimed tail: do not treat an already-claimed shard as merely aborted. |
| 07 | Dashboard preflight cannot live only in `TaskManager.start_sequential_worker`. | That method receives only IDs and has no repository/session category lookup. | Move category/profile resolution to the endpoint/service layer, then spawn through `TaskManager`. |
| 08 | `tuple[bool, str, str]` return from `start_sequential_worker` conflicts with existing `tuple[bool, str]`. | `_start()` and callers expect the existing two-value shape. | Keep `TaskManager` spawn result unchanged; return structured preflight errors from the HTTP endpoint. |
| 09 | Multi-category preflight needs more than one error in response. | Current API error conventions use structured detail but not a single fixed multi-error shape. | Add `errors[]` with `{profile, error_code, message}` while preserving top-level `error_code`. |
| 10 | Preflight "under one second" is too strict. | `profile_exists()` runs a subprocess with a 10s timeout. | Replace with "bounded local check"; no outbound LLM/network call is the real guarantee. |
| 11 | `invalid_request_error` alone is too broad for auth. | Provider invalid-request errors can be schema/model errors. | Require structured auth marker or auth/key/401-specific tail evidence for `hermes_auth`. |
| 12 | Raw `429` tail matching can false-positive on generated payload text. | Logs may contain challenge content and model output. | Match structured marker first; tail fallback requires provider/rate-limit context, not arbitrary `429`. |
| 13 | `validation` phase says "every result failed". | Current runner marks shard failed when any per-challenge validation result failed. | Change to "one or more validation results failed after repair." |
| 14 | `elapsed_seconds` definition is unclear for pre-Hermes failures. | Some failure branches happen before `_invoke()`. | Define elapsed as time from claimed-shard processing start to failure; classifier uses Hermes invocation elapsed only for post-invoke branches. |
| 15 | Module-import env rejection is risky. | Repo patterns often validate env at command/helper boundary to avoid breaking unrelated imports. | Validate env knobs at CLI/classifier use boundary, not import time. |
| 16 | `hermes_rate_limit` streak should not be disabled by "runtime" reset. | Shared gateway throttles affect subsequent attempts. | Keep `hermes_rate_limit` in the streak set. |
| 17 | `materialize` may cover both input materialization and output promotion. | Current runner uses workspace materialization and output promotion branches. | State that `materialize` covers workspace setup/resume/shim/output-promotion failures. |
| 18 | Publisher phase coexistence must not invent a no-phase exception. | Existing proposal says every failed outcome gets `hermes_phase`. | Keep every failure phased; store publisher detail separately as `publisher_phase` when available. |
| 19 | Result JSON file must not become reconciler truth. | Current reconciler observes DB/filesystem shard state, not worker stdout. | Specify result JSON is dashboard-only display state. |
| 20 | Aborted rows should remain re-submittable without retry semantics. | Current build attempts can remain queued if never claimed. | Preserve DB no-op for unclaimed aborted tail. |
| 21 | OpenSpec validation task conflicts with this review request. | User explicitly requested no OpenSpec validation in this pass. | Replace validation task with static consistency review for this proposal. |
| 22 | Hermes profile management UI is related but unsafe without auth. | Current dashboard has no authentication layer. | Keep profile UI out of scope; preflight error codes are reusable later. |

## Task 0.1 verification evidence

- Current code evidence:
  - `src/cli.py::_resolve_run_timeout()` returns `(None, None)` when neither
    `--timeout` nor `HERMES_TIMEOUT` is set.
  - The explicit `--build-attempt-sequence` loop passes that `timeout=None`
    through to `HermesRunner.run(...)`.
  - `HermesRunner._process_real()` derives
    `effective_timeout = shard_timeout_policy(payload)` after claiming and
    reading the shard payload, then records `timeout_source="shard_policy"` in
    the workspace manifest.
- Test evidence:
  - `uv run pytest tests/app/test_cli.py::CLITimeoutPrecedenceTests -q --basetemp .pytest-basetemp/sequential-timeout-guardrail`
  - Result: `5 passed`.
