## ADDED Requirements

### Requirement: Sequential build queue fails fast on consecutive infrastructure failures

The CLI sequential driver SHALL maintain a streak counter over per-attempt
outcomes and SHALL stop consuming the remaining attempts once the streak
reaches a configurable threshold. The driver is invoked via
`python -m cli run --build-attempt-sequence <id> [--build-attempt-sequence <id> ...]`
(spawned by `src/web/dashboard.py::start_sequential_worker` from the
HTTP endpoint `/api/build-attempts/worker/start-sequential`). The streak
SHALL be incremented by exactly the runner-level `hermes_phase` values in
`{preflight_workspace, contract_prepare, hermes_auth, hermes_rate_limit}` and SHALL be
reset to `0` by any other phase value or by a `done` outcome.

When the streak reaches the value of the environment variable
`BUILD_SEQ_INFRA_FAILFAST_STREAK` (non-negative integer, default `2`,
where `0` disables the streak entirely and negatives are rejected at
startup), the driver SHALL stop consuming the remaining attempts and
SHALL emit one synthetic outcome per remaining attempt of the shape
`{"status": "aborted", "shard": "<attempt_id>",
"abort_reason": "consecutive_infra"}`.

The driver SHALL extend the final JSON result with the additional
top-level keys `abort_reason` (one of `null`, `"consecutive_infra"`,
`"interrupt"`) and `aborted` (the list of attempt ids that received a
synthetic `aborted` outcome). The result SHALL also include
`interrupted_attempt`, set to the in-flight attempt id when the CLI catches
`KeyboardInterrupt`, otherwise `null`. When `abort_reason` is non-null the CLI
SHALL exit with status code `1` regardless of `failed`.

The `aborted` outcomes SHALL NOT increment `failed` and SHALL NOT
increment `processed`. The reconciler SHALL treat `aborted` outcomes
as no-ops; the underlying `build_attempt` row SHALL stay in its
pre-batch state so the operator can re-submit without going through
retry/clean-rebuild.

The single-`--build-attempt` and `--loop` paths SHALL NOT gain the
new JSON keys; this requirement applies only to the
`--build-attempt-sequence` driver.

The sequential driver SHALL persist its final structured JSON result to
`work/logs/dashboard-sequential-worker-result.json`. The dashboard SHALL read
that file when rendering the build-attempt queue surface so synthetic
`aborted` outcomes remain visible after refresh. This result file SHALL NOT be
used by the reconciler to move `build_attempt` rows to `failed`.

#### Scenario: Two consecutive hermes_auth outcomes halt the queue

- **GIVEN** a sequence of attempt ids `[A1, A2, A3, A4, A5]`
- **AND** `BUILD_SEQ_INFRA_FAILFAST_STREAK == 2`
- **AND** `A1` succeeds, `A2` returns `hermes_phase == "hermes_auth"`,
  `A3` returns `hermes_phase == "hermes_auth"`
- **WHEN** the sequential driver runs the sequence
- **THEN** the driver does NOT call the runner for `A4` or `A5`
- **AND** the final JSON contains
  `outcomes = [<A1>, <A2>, <A3>, {status: "aborted", shard: "A4", abort_reason: "consecutive_infra"}, {status: "aborted", shard: "A5", abort_reason: "consecutive_infra"}]`
- **AND** `abort_reason == "consecutive_infra"`
- **AND** `aborted == ["A4", "A5"]`
- **AND** the CLI exits with status `1`

#### Scenario: Validation failures do not trigger fail-fast

- **GIVEN** a sequence where every attempt returns
  `hermes_phase == "validation"` (Hermes ran fine but per-challenge
  validation failed)
- **AND** `BUILD_SEQ_INFRA_FAILFAST_STREAK == 2`
- **WHEN** the sequential driver runs the sequence
- **THEN** the driver consumes every attempt
- **AND** `abort_reason` is `null`
- **AND** `aborted` is empty

#### Scenario: Rate limit after auth reaches the infrastructure streak

- **GIVEN** a sequence of attempt ids `[A1, A2, A3, A4]`
- **AND** `BUILD_SEQ_INFRA_FAILFAST_STREAK == 2`
- **AND** `A1` returns `hermes_phase == "hermes_auth"`
- **AND** `A2` returns `hermes_phase == "hermes_rate_limit"`
- **WHEN** the sequential driver runs the sequence
- **THEN** the driver does NOT call the runner for `A3` or `A4`
- **AND** `abort_reason == "consecutive_infra"`
- **AND** `aborted == ["A3", "A4"]`

#### Scenario: Cancellation propagates as immediate batch stop

- **GIVEN** a sequence of attempt ids `[A1, A2, A3]`
- **AND** `A1` returns `hermes_phase == "hermes_cancelled"` (e.g.
  Hermes was killed by SIGINT)
- **WHEN** the sequential driver runs the sequence
- **THEN** the driver does NOT call the runner for `A2` or `A3`
- **AND** `abort_reason == "interrupt"`
- **AND** `aborted == ["A2", "A3"]`
- **AND** the CLI exits with status `1`

#### Scenario: KeyboardInterrupt at the CLI is caught and emitted as interrupt

- **GIVEN** a sequence of attempt ids `[A1, A2, A3]`
- **AND** `runner.run(A1)` raises `KeyboardInterrupt`
- **WHEN** the sequential driver catches the exception
- **THEN** `A1` does NOT appear in `outcomes` (the runner had no chance
  to return an outcome to the driver)
- **AND** `interrupted_attempt == "A1"`
- **AND** synthetic `aborted` outcomes are appended only for `A2` and `A3`
- **AND** `abort_reason == "interrupt"`
- **AND** `aborted == ["A2", "A3"]`

#### Scenario: Fail-fast can be disabled by env var for CI

- **GIVEN** `BUILD_SEQ_INFRA_FAILFAST_STREAK == 0`
- **AND** every attempt in a 5-attempt sequence returns
  `hermes_phase == "hermes_auth"`
- **WHEN** the sequential driver runs the sequence
- **THEN** the driver consumes all 5 attempts
- **AND** `abort_reason` is `null`
- **AND** `aborted` is empty

#### Scenario: Dashboard shows aborted tail after refresh

- **GIVEN** a sequential worker stopped with
  `abort_reason == "consecutive_infra"`
- **AND** the final result file contains synthetic `aborted` outcomes for
  `[A4, A5]`
- **WHEN** the operator refreshes the build-attempt dashboard
- **THEN** the dashboard shows `A4` and `A5` as aborted / ready to re-submit
- **AND** neither row is presented as a real `build_failed` attempt because
  the reconciler has not moved the DB row to `failed`

### Requirement: Dashboard refuses to spawn a sequential worker without a healthy Hermes profile

The sequential-worker HTTP endpoint SHALL resolve the requested attempts'
distinct categories to Hermes profile names, call a new local-filesystem-only
helper `hermes_profile_health(profile_name)`, and only then call
`src/web/dashboard.py::start_sequential_worker` to spawn the worker subprocess.
The helper SHALL NOT contact the upstream LLM and SHALL NOT issue any network
request.

The helper SHALL inspect (a) that
`Path(f"~/.hermes/profiles/{profile_name}").expanduser()` exists and
is a directory; (b) that `<profile>/.env` exists, is readable, and
contains a non-empty value for at least one of `ANTHROPIC_API_KEY` or
`ANTHROPIC_TOKEN`; (c) that `hermes.process.profile_exists(profile_name)`
(the existing offline Hermes-CLI probe) returns `True`. The key value
SHALL NOT be logged or returned to the caller.

When the sequence contains multiple categories, the helper SHALL be
called once per distinct category (with `profile_name = f"cf-{category}"`)
and all messages SHALL be aggregated.

On failure the dashboard SHALL refuse to spawn the worker and SHALL
return a stable error code from the set
`{hermes_profile_missing, hermes_profile_env_missing,
hermes_profile_key_missing, hermes_profile_cli_unavailable}`.
The HTTP endpoint `/api/build-attempts/worker/start-sequential` SHALL
return status code `409` with body
`{"ok": false, "error_code": "...", "message": "...", "errors": [...]}`.
Each `errors[]` entry SHALL include `profile`, `error_code`, and `message`.
The existing single-`--build-attempt` endpoints SHALL NOT be changed.

#### Scenario: Missing profile directory blocks startup

- **GIVEN** the operator submits a sequence whose first attempt has
  category `web`
- **AND** `~/.hermes/profiles/cf-web/` does not exist
- **WHEN** the dashboard endpoint receives the request
- **THEN** the worker subprocess is NOT spawned
- **AND** the response is HTTP `409`
- **AND** the response body contains
  `error_code == "hermes_profile_missing"`

#### Scenario: Empty API key blocks startup

- **GIVEN** `~/.hermes/profiles/cf-web/.env` exists and contains
  `ANTHROPIC_API_KEY=""` and no other Anthropic credential
- **WHEN** the dashboard endpoint receives a `web`-category sequence
- **THEN** the worker subprocess is NOT spawned
- **AND** the response body contains
  `error_code == "hermes_profile_key_missing"`

#### Scenario: Mixed-category sequence with one bad profile blocks startup

- **GIVEN** the sequence contains both `web` and `pwn` attempts
- **AND** `cf-web` is healthy but `cf-pwn` is missing its `.env`
- **WHEN** the dashboard endpoint receives the request
- **THEN** the worker subprocess is NOT spawned
- **AND** the response body lists the `cf-pwn` failure
- **AND** the response body's error_code is the cf-pwn failure code

#### Scenario: Preflight does not contact the upstream LLM

- **GIVEN** any sequence
- **WHEN** the dashboard performs preflight
- **THEN** no outbound HTTP request is made
- **AND** every subprocess probe is local and bounded by its configured timeout
