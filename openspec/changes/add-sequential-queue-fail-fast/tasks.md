## 1. Runner Failure Taxonomy

- [ ] 1.1 Create `src/domain/build_failure_taxonomy.py` mirroring the
      shape of `src/domain/research_failure_taxonomy.py`. Expose
      `BuildFailureCategory = Literal["preflight_workspace", "materialize",
      "contract_prepare", "hermes_auth", "hermes_runtime",
      "hermes_timeout", "hermes_cancelled", "validation"]` and a single
      classifier
      `classify_hermes_exit(returncode: int, log_tail: str, elapsed_seconds: float)
      -> BuildFailureCategory`. The classifier MUST NOT raise on empty
      `log_tail`; missing log is treated as `hermes_runtime` unless
      `returncode == 124` (then `hermes_timeout`) or `returncode < 0`
      (then `hermes_cancelled`).
- [ ] 1.2 Implement the `hermes_auth` rule as the conjunction of (a)
      `returncode == 1`, (b) `elapsed_seconds < BUILD_HERMES_FAIL_FAST_MIN_SECONDS`
      (default 30), and (c) at least one of these case-insensitive
      regex matches in `log_tail`: `Anthropic 401`, `invalid_request_error`,
      `api[_- ]?key`, plus the literal Chinese substring `gic密钥`.
      The 4 KB log-tail extraction lives in the runner, not in the
      classifier, so the classifier remains pure.
- [ ] 1.3 Validate `BUILD_HERMES_FAIL_FAST_MIN_SECONDS` as a positive
      integer at module import; reject `0` or negatives with a clear
      `ValueError` referencing the env var name.
- [ ] 1.4 In `src/hermes/runner.py`, capture
      `started_at = time.monotonic()` immediately before `_invoke()` and
      compute `elapsed_seconds = time.monotonic() - started_at`
      immediately after. Pass `elapsed_seconds` into every
      `_mark_shard_failed` call from this PR onward.
- [ ] 1.5 Extend `_mark_shard_failed` (and the failure-return dicts
      built inline at `_process_real`) so that every failure outcome
      includes the keys `hermes_phase: str` and `elapsed_seconds: float`.
      The `failure_type` key keeps its current value
      (`infrastructure` / `validation`).
- [ ] 1.6 At each failure site in `_process_real`, set
      `hermes_phase` deterministically:
        - `preflight_workspace` for `WorkspacePreflightError`
        - `materialize` for the `materialize_resume_outputs` and
          `materialize_progress_shim` branches
        - `contract_prepare` for the `prepare_publication_contract`
          exception branch
        - one of `hermes_auth` / `hermes_runtime` / `hermes_timeout` /
          `hermes_cancelled` for the post-`_invoke` non-zero return,
          via the classifier in 1.1
        - `validation` for the "Hermes ok but per-challenge validation
          all failed after repair" branch
      The publisher's own `phase` (when present) is recorded separately
      under `publisher_phase`; it does NOT overwrite `hermes_phase`.
- [ ] 1.7 In the `KeyboardInterrupt` branch of `_process_real` (currently
      at `runner.py:625–637`), set `hermes_phase = "hermes_cancelled"`
      and `returncode = -2` on the outcome dict before re-raising. The
      `_mark_shard_failed` call already exists; it just needs the new
      kwargs.
- [ ] 1.8 Treat any `returncode < 0` returned by `_invoke` as
      `hermes_cancelled`; the runner SHALL NOT attempt timeout recovery
      for negative return codes (today's `returncode != 0` branch must
      gate timeout recovery on `returncode == HERMES_TIMEOUT_RETURNCODE`,
      not just "non-zero").

## 2. CLI Sequential Driver

- [ ] 2.1 Rewrite the `if args.build_attempt_sequence:` block in
      `src/cli.py:992–1011` as the state machine specified in
      design Decision 3. Keep the existing per-attempt
      `runner.run(args.worker, ..., build_attempt_id=attempt_id)`
      call shape — only the surrounding loop changes.
- [ ] 2.2 Read `BUILD_SEQ_INFRA_FAILFAST_STREAK` from the environment
      at startup, default `2`, validated non-negative integer. Reject
      negatives with `argument_parser.error(...)` referencing the env
      var name. The value `0` SHALL be accepted and SHALL disable the
      streak (no fail-fast).
- [ ] 2.3 Maintain the `infra_streak` counter only over phases in
      `{preflight_workspace, contract_prepare, hermes_auth}`. Phases
      `materialize` and `hermes_runtime` and `validation` and
      `hermes_timeout` SHALL reset the streak to `0`. (Justification:
      these can be per-challenge problems; the streak is reserved for
      cross-attempt credential / shared-resource failures.)
- [ ] 2.4 Catch `KeyboardInterrupt` around `runner.run(...)`; on catch,
      set `abort_reason = "interrupt"`, fill `aborted_attempts` with
      every remaining id, and break the loop. The exception is NOT
      re-raised — the CLI exits 1 cleanly with the structured JSON.
- [ ] 2.5 When the loop terminates with a non-None `abort_reason`,
      append one synthetic outcome per remaining attempt:
      `{"status": "aborted", "shard": "<id>", "abort_reason": ...}`.
      These outcomes do NOT increment `failed` and do NOT increment
      `processed`.
- [ ] 2.6 Extend the final JSON object to include
      `"abort_reason": <str|null>` and `"aborted": [<id>, ...]`. The
      pre-existing `"requested"` field is unchanged.
- [ ] 2.7 The single-`--build-attempt` and `--loop` paths are NOT
      modified; they SHALL NOT gain the new JSON keys (so existing
      callers that parse those shapes stay valid).
- [ ] 2.8 Exit code: when `abort_reason` is non-null, `sys.exit(1)`
      unconditionally. When `abort_reason` is null, retain today's
      `if result["failed"]: sys.exit(1)` behavior.

## 3. Dashboard Preflight

- [ ] 3.1 Add a new helper `hermes_profile_health(profile_name: str) ->
      tuple[bool, str, str]` in `src/hermes/process.py` (returns
      `(ok, error_code, message)`). The helper SHALL NOT make any
      subprocess call that contacts the upstream LLM. Permitted checks:
      `Path('~/.hermes/profiles/<profile>').expanduser().is_dir()`,
      reading the `.env` file's keys, and calling the existing
      `profile_exists(profile_name)` for the offline Hermes CLI probe.
- [ ] 3.2 The `.env` parser SHALL accept either `ANTHROPIC_API_KEY` or
      `ANTHROPIC_TOKEN` with a non-empty value (trimmed of whitespace
      and surrounding quotes). It SHALL NOT log or return the key
      value; only existence is reported.
- [ ] 3.3 Define stable error codes: `hermes_profile_missing`,
      `hermes_profile_env_missing`, `hermes_profile_key_missing`,
      `hermes_profile_cli_unavailable`. The dashboard maps each to a
      Chinese message identical to today's tone (see existing strings
      in `src/web/dashboard.py`).
- [ ] 3.4 In `src/web/dashboard.py::start_sequential_worker`, before
      `_start(...)`, derive the profile name from the first attempt's
      category (`f"cf-{category}"`) and call `hermes_profile_health`.
      On failure, return `(False, message)` WITHOUT spawning the
      worker. Existing single-`--build-attempt` paths are not touched.
- [ ] 3.5 If the sequence spans multiple categories, run the preflight
      once per distinct category and accumulate the messages. If any
      category fails, refuse to spawn.
- [ ] 3.6 In `src/web/build_attempts_endpoints.py`'s
      `/api/build-attempts/worker/start-sequential` handler, return
      HTTP `409` with body
      `{"ok": false, "error_code": "...", "message": "..."}` on
      preflight failure, mirroring the existing error-shape convention
      used by other start endpoints. The single-attempt endpoint stays
      unchanged.

## 4. Process-Level Cancellation Hygiene

- [ ] 4.1 In `src/hermes/process.py::invoke`, do NOT collapse negative
      returncodes to `1`. Today's path already returns the raw
      `process.returncode`, but verify with a regression test
      (`tests/app/test_hermes_process_signals.py`) that a child killed
      by SIGINT/SIGTERM is observed as `-2` / `-15`.
- [ ] 4.2 Verify `HERMES_TIMEOUT_RETURNCODE == 124` and that timeouts
      remain reachable independently of negative returncodes (a
      timed-out child gets `124` from this module, NOT the kernel's
      `-9`).

## 5. Tests

- [ ] 5.1 Unit: `tests/app/test_build_failure_taxonomy.py` covers
      every `classify_hermes_exit` branch:
        - `(rc=1, "...Anthropic 401...", 4.0) → hermes_auth`
        - `(rc=1, "...gic密钥已失效...", 2.0) → hermes_auth`
        - `(rc=1, "exploit failed", 600.0) → hermes_runtime` (long run)
        - `(rc=1, "401 in JSON payload", 600.0) → hermes_runtime` (slow
          path even if the keyword appears)
        - `(rc=124, "", 2700.0) → hermes_timeout`
        - `(rc=-2, "", 12.0) → hermes_cancelled`
        - `(rc=-15, "", 60.0) → hermes_cancelled`
        - `(rc=0, "", 12.0)` is treated as an invariant violation and
          MUST raise (the classifier is only for failures).
- [ ] 5.2 Unit: env-var validation rejects
      `BUILD_HERMES_FAIL_FAST_MIN_SECONDS=0`, negative, non-integer.
- [ ] 5.3 Unit: `BUILD_SEQ_INFRA_FAILFAST_STREAK=0` disables the streak
      and the driver consumes the full sequence.
- [ ] 5.4 Integration: `tests/app/test_sequential_queue_failfast.py`
      drives a fake `HermesRunner` that returns scripted outcomes:
        - 5 successes followed by 2 `hermes_auth` → driver aborts before
          attempt 8, `aborted` outcomes for attempts 8–N, final JSON has
          `abort_reason="consecutive_infra"`.
        - 2 `hermes_runtime` outcomes do NOT abort (streak only counts
          infra-class phases). The driver consumes the full sequence.
        - 1 `hermes_cancelled` outcome aborts immediately regardless of
          streak threshold, with `abort_reason="interrupt"`.
        - A `KeyboardInterrupt` raised from `runner.run` is caught and
          produces `abort_reason="interrupt"` with the in-flight
          attempt absent from outcomes (no synthetic outcome for it).
        - Replay of the lab-host incident shape (`4d → d47 → 9b →
          4e → 96 → ced→cancel → 670→auth → bec→auth`): driver stops at
          attempt #8 with two infra streak entries, leaving 4 aborted.
- [ ] 5.5 Integration: dashboard preflight in
      `tests/app/test_dashboard_preflight.py`:
        - missing profile directory → 409
        - present directory but missing `.env` → 409
        - `.env` present but both `ANTHROPIC_API_KEY` and
          `ANTHROPIC_TOKEN` empty → 409
        - all three checks pass → 200 and worker is spawned
        - multiple categories in sequence, one bad → 409 with both
          messages
- [ ] 5.6 Regression: `tests/app/test_runner_resume.py` (already
      modified in this branch) gains assertions that every failed
      outcome carries `hermes_phase` and `elapsed_seconds`. The
      `KeyboardInterrupt` branch test asserts `hermes_phase ==
      "hermes_cancelled"` and `returncode == -2`.
- [ ] 5.7 Regression: BuildReconciler tests confirm that the new
      `aborted` outcomes do NOT trigger a `build_failed` state
      transition; the underlying `build_attempt` row stays in its
      pre-batch state. (This is the behavior today because reconciler
      reads filesystem, not outcome JSON, but the test pins it.)
- [ ] 5.8 Run `uv run pytest tests/app/test_build_failure_taxonomy.py
      tests/app/test_sequential_queue_failfast.py
      tests/app/test_dashboard_preflight.py
      tests/app/test_hermes_process_signals.py
      tests/app/test_runner_resume.py -q` and confirm all green.

## 6. Documentation

- [ ] 6.1 Update `worker-pool-split-plan.md` (or the relevant docs index)
      to reference this proposal as a sibling reliability fix landed
      alongside, but not inside, the worker pool split sequence.
- [ ] 6.2 Add a short ops note under `docs/operator/` (create if
      missing) describing the new `abort_reason` values, the two env
      vars, and the operator action when
      `abort_reason=consecutive_infra` (rotate Hermes key, re-submit
      `aborted` attempts).

## 7. Spec Validation

- [ ] 7.1 Verify `openspec validate add-sequential-queue-fail-fast --strict`
      passes.
- [ ] 7.2 Confirm the change can be archived independently: nothing in
      this proposal references files or specs introduced by
      `add-staged-publication-allowlist` that are not already present
      in the baseline.
