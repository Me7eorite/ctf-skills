## ADDED Requirements

### Requirement: Hermes runner emits a stable failure-phase taxonomy

The Hermes runner SHALL classify every failure outcome with a stable
`hermes_phase` value drawn from a fixed eight-name set. When
`HermesRunner._process_real` returns a failure outcome (i.e. when
`status == "failed"`), the outcome dict SHALL include both a
`failure_type` (the existing field, used by `BuildReconciler`) and a
new `hermes_phase` field whose value is one of the following stable
strings:

- `preflight_workspace`
- `materialize`
- `contract_prepare`
- `hermes_auth`
- `hermes_runtime`
- `hermes_timeout`
- `hermes_cancelled`
- `validation`

The outcome dict SHALL also include `elapsed_seconds`, a float
representing monotonic-clock wall time from immediately before the
Hermes invocation to immediately after `_invoke` returned (or to the
moment the exception was caught for non-Hermes phases). Negative
values SHALL be replaced with `0.0`.

The `hermes_phase` classification is independent of, and additive to,
the publisher-level `phase` introduced by `add-staged-publication-allowlist`.
When a publisher exception triggers a runner failure, the runner SHALL
record the publisher's phase in a separate `publisher_phase` key and
SHALL NOT overwrite `hermes_phase`.

The classification rules SHALL be:

- `preflight_workspace`: `preflight_workspace()` raised
  `WorkspacePreflightError`.
- `materialize`: `materialize_resume_outputs` or
  `materialize_progress_shim` raised `OSError` /
  `WorkspacePromotionError` / `ValueError`.
- `contract_prepare`: `prepare_publication_contract` raised
  before Hermes invocation.
- `hermes_auth`: Hermes exited with `returncode == 1`,
  `elapsed_seconds < BUILD_HERMES_FAIL_FAST_MIN_SECONDS` (default
  `30`), AND the last 4 KB of the per-shard `hermes.log` matches at
  least one of the case-insensitive patterns `Anthropic 401`,
  `invalid_request_error`, `api[_- ]?key`, OR contains the literal
  substring `gic密钥`.
- `hermes_runtime`: any other non-zero positive `returncode` that
  doesn't match a more specific rule.
- `hermes_timeout`: `returncode == HERMES_TIMEOUT_RETURNCODE` (124)
  AND timeout recovery did not complete.
- `hermes_cancelled`: `returncode < 0` OR `KeyboardInterrupt` was
  caught inside `_process_real`. The outcome SHALL set
  `returncode = -2` for the `KeyboardInterrupt` branch even if no
  signal was actually delivered.
- `validation`: Hermes succeeded but every per-challenge validation
  result is `failed` after the configured validation-repair attempts.

The classifier SHALL be a pure function residing in
`src/domain/build_failure_taxonomy.py`; the runner is responsible
only for extracting the 4 KB log tail and the elapsed seconds.

`BUILD_HERMES_FAIL_FAST_MIN_SECONDS` SHALL be validated as a positive
integer at module import (zero and negative values rejected with a
clear error referencing the env var name).

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
- **THEN** `hermes_phase` reflects the runner's perspective
  (here `hermes_runtime` is not applicable — Hermes succeeded — so the
  runner SHALL emit `validation`-style coverage by recording the
  failure with no `hermes_phase` if and only if no Hermes-side phase
  applies; otherwise the runner uses the phase appropriate to its own
  exit branch)
- **AND** `publisher_phase == "allowlist"` is recorded as a separate
  key
