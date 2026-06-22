## ADDED Requirements

### Requirement: Hermes runner emits a stable failure-phase taxonomy

The Hermes runner SHALL classify every failure outcome with a stable
`hermes_phase` value drawn from a fixed nine-name set. When
`HermesRunner._process_real` returns a failure outcome (i.e. when
`status == "failed"`), the outcome dict SHALL include both a
`failure_type` (the existing field, used by `BuildReconciler`) and a
new `hermes_phase` field whose value is one of the following stable
strings:

- `preflight_workspace`
- `materialize`
- `contract_prepare`
- `hermes_auth`
- `hermes_rate_limit`
- `hermes_runtime`
- `hermes_timeout`
- `hermes_cancelled`
- `validation`

The outcome dict SHALL also include `elapsed_seconds`, a float
representing monotonic-clock wall time from claimed-shard processing start to
the failure. For post-Hermes classifications, the classifier may additionally
use the Hermes invocation elapsed time. Negative values SHALL be replaced with
`0.0`. When available from the workspace manifest, failed outcomes SHALL also
include `effective_timeout_seconds` and `timeout_source`.

The `hermes_phase` classification is independent of, and additive to,
the publisher-level `phase` already emitted by the baseline publisher.
When a publisher exception triggers a runner failure, the runner SHALL
record the publisher's phase in a separate `publisher_phase` key and
SHALL NOT overwrite `hermes_phase`.

The classification rules SHALL be:

- `preflight_workspace`: `preflight_workspace()` raised
  `WorkspacePreflightError`.
- `materialize`: workspace setup/resume/shim/output-promotion raised
  `OSError` / `WorkspacePromotionError` / `ValueError`.
- `contract_prepare`: `prepare_publication_contract` raised
  before Hermes invocation.
- `hermes_auth`: a structured marker reports an auth-shaped SDK/provider error
  (`authentication_error`, `status_code == 401`, or equivalent), OR Hermes
  exited with `returncode == 1`, `elapsed_seconds <
  BUILD_HERMES_FAIL_FAST_MIN_SECONDS` (default `30`), AND the last 4 KB of the
  per-shard `hermes.log` contains auth/key/401-specific provider text such as
  `Anthropic 401`, `gic密钥`, or API-key invalidation text. Generic
  `invalid_request_error` alone SHALL NOT classify as auth.
- `hermes_rate_limit`: a structured marker reports `rate_limit_error`,
  `overloaded_error`, `status_code == 429`, or equivalent provider overload,
  OR the last 4 KB of `hermes.log` contains provider rate-limit/overload
  context such as `rate_limit`, `rate limit`, `overloaded_error`, or
  status-code 429 adjacent to provider error text. A bare `429` inside
  generated challenge output SHALL NOT classify as rate-limit.
- `hermes_runtime`: any other non-zero positive `returncode` that
  doesn't match a more specific rule.
- `hermes_timeout`: `returncode == HERMES_TIMEOUT_RETURNCODE` (124)
  AND timeout recovery did not complete.
- `hermes_cancelled`: `returncode < 0` OR `KeyboardInterrupt` was
  caught inside `_process_real`. The outcome SHALL set
  `returncode = -2` for the `KeyboardInterrupt` branch even if no
  signal was actually delivered.
- `validation`: Hermes succeeded but one or more per-challenge validation
  results are still `failed` after the configured validation-repair attempts.

The classifier SHALL be a pure function residing in
`src/domain/build_failure_taxonomy.py`; the runner is responsible
only for extracting the 4 KB log tail, reading the structured marker sidecar,
and providing elapsed seconds.

The Hermes process wrapper SHALL write a bounded
`hermes.log.error_marker.json` sidecar when bounded post-exit log scanning or
an equivalent streaming tee observes parseable SDK/provider error metadata.
The build `invoke()` path SHALL NOT need to capture full stdout in memory to
produce this marker. The sidecar SHALL contain only non-secret metadata such as
`type`, `error_type`, `status_code`, and `source`; it SHALL NOT contain API
keys, prompts, or full response bodies. The classifier SHALL prefer the sidecar
over log-tail regexes.

`BUILD_HERMES_FAIL_FAST_MIN_SECONDS` SHALL be validated as a positive integer
at CLI/classifier-use boundary (zero and negative values rejected with a clear
error referencing the env var name). A malformed value SHALL NOT make unrelated
module imports fail.

#### Scenario: Hermes 401 inside the SDK is classified as hermes_auth

- **GIVEN** a workspace whose `hermes.log` ends with `Anthropic 401 — authentication failed` and `gic密钥已失效，请访问AI-Gateway创建新密钥`
- **AND** Hermes exited with `returncode == 1` after `elapsed_seconds == 8.4`
- **WHEN** the runner classifies the failure
- **THEN** the outcome includes `hermes_phase == "hermes_auth"`
- **AND** `failure_type == "infrastructure"`
- **AND** `elapsed_seconds == 8.4`

#### Scenario: A long real failure is not misclassified as auth

- **GIVEN** Hermes exited with `returncode == 1` after `elapsed_seconds == 612.0`
- **AND** the log tail mentions `401` only inside a generated test payload
- **WHEN** the runner classifies the failure
- **THEN** the outcome includes `hermes_phase == "hermes_runtime"`
- **AND** the sequence driver does NOT count this toward its
  fail-fast streak

#### Scenario: Structured marker beats a truncated log tail

- **GIVEN** `hermes.log.error_marker.json` contains
  `{"type":"error","error_type":"authentication_error","status_code":401}`
- **AND** the last 4 KB of `hermes.log` contains only a Python stack trace
- **WHEN** the runner classifies the failure
- **THEN** the outcome includes `hermes_phase == "hermes_auth"`

#### Scenario: Provider rate limit is classified separately from runtime

- **GIVEN** Hermes exited non-zero after `elapsed_seconds == 7.0`
- **AND** the structured marker contains `{"error_type":"rate_limit_error"}`
- **WHEN** the runner classifies the failure
- **THEN** the outcome includes `hermes_phase == "hermes_rate_limit"`
- **AND** the sequential driver counts it toward the infrastructure streak

#### Scenario: SIGINT to Hermes is classified as cancelled, not infrastructure

- **GIVEN** Hermes was interrupted by SIGINT and exited with
  `returncode == -2`
- **WHEN** the runner classifies the failure
- **THEN** the outcome includes `hermes_phase == "hermes_cancelled"`
- **AND** the outcome's `failure_type` remains `"infrastructure"` for
  the existing `BuildReconciler`
- **AND** the runner does NOT attempt timeout recovery

#### Scenario: KeyboardInterrupt inside the runner records cancellation before re-raising

- **GIVEN** a `KeyboardInterrupt` is raised inside `_process_real`
- **WHEN** the runner's `except KeyboardInterrupt` branch executes
- **THEN** `_mark_shard_failed` is called with
  `hermes_phase == "hermes_cancelled"` and `returncode == -2`
- **AND** the exception is re-raised so callers above the runner can
  decide how to react
- **AND** the shard's failure report contains the same phase value

#### Scenario: Publisher phase is recorded alongside, not in place of, the runner phase

- **GIVEN** Hermes succeeded but the publisher raised
  `WorkspacePublishError(phase="allowlist")`
- **WHEN** the runner records the failure
- **THEN** the failure outcome still includes a `hermes_phase`
- **AND** that value is the runner-side phase for the branch that caught the
  publisher exception (for the current publication/promotion branch, that is
  `materialize`)
- **AND** `publisher_phase == "allowlist"` is recorded as a separate
  key

### Requirement: Sequential build attempts expose effective timeout/source evidence

When the sequential driver is invoked without an explicit CLI timeout and
without `HERMES_TIMEOUT`, the runner SHALL derive the effective Hermes timeout
from the claimed shard payload via `shard_timeout_policy(payload)`. The driver
SHALL NOT fall back to `DEFAULT_HERMES_TIMEOUT=1500` before the shard payload is
available. This is a diagnostic guardrail for this proposal, not a statement
that the current implementation is known to be falling back to 1500 seconds.

#### Scenario: Pwn shard policy is not overwritten by default timeout

- **GIVEN** a pwn-category sequential build attempt whose shard timeout policy
  exceeds `1500`
- **AND** neither `--timeout` nor `HERMES_TIMEOUT` is set
- **WHEN** the sequential driver runs the attempt
- **THEN** the Hermes invocation receives the pwn shard-policy timeout
- **AND** stdout begins with
  `effective_timeout=shard-policy source=shard_policy`

#### Scenario: Timeout failure carries the source that was actually used

- **GIVEN** a sequential build attempt fails with `returncode == 124`
- **AND** the workspace manifest recorded
  `effective_timeout_seconds == 3600` and `timeout_source == "shard_policy"`
- **WHEN** the runner returns the failure outcome
- **THEN** the outcome includes `hermes_phase == "hermes_timeout"`
- **AND** the outcome includes `effective_timeout_seconds == 3600`
- **AND** the outcome includes `timeout_source == "shard_policy"`
