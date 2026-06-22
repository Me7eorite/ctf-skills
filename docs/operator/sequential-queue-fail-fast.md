# Sequential Queue Fail-Fast Ops Note

The dashboard sequential build queue writes its latest batch summary to
`work/logs/dashboard-sequential-worker-result.json`.

## Abort Reasons

- `consecutive_infra`: the sequence stopped after repeated shared
  infrastructure failures. Current infra phases are `preflight_workspace`,
  `contract_prepare`, `hermes_auth`, and `hermes_rate_limit`.
- `interrupt`: the operator or runtime cancelled the in-flight Hermes process.

Attempts listed in `aborted` were not run by the sequence. They should stay in
their pre-batch queue state and can be submitted again after the underlying
issue is fixed.

## Environment Variables

- `BUILD_SEQ_INFRA_FAILFAST_STREAK`: default `2`. Number of consecutive infra
  outcomes that aborts the sequence. Set to `0` to disable this fail-fast
  streak.
- `BUILD_HERMES_FAIL_FAST_MIN_SECONDS`: default `30`. Fast-exit threshold used
  when classifying Hermes auth failures from log-tail evidence.

## Operator Action

When `abort_reason=consecutive_infra`, inspect the first failed attempts'
Hermes logs under `work/executions/<build_attempt_id>/logs/hermes.log`.

For `hermes_auth`, rotate or repair the affected `cf-<category>` Hermes profile
key, then re-submit only the attempts listed in `aborted`.

For `hermes_rate_limit`, wait for provider capacity or quota recovery, then
re-submit only the aborted attempts.
