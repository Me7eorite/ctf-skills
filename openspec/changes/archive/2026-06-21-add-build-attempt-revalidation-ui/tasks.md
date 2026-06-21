## 1. Service revalidation

- [x] 1.1 Add a service-layer revalidation entry point for one build attempt.
- [x] 1.2 Validate eligibility: latest failed attempt only; reject queued, running, succeeded, lost, stale siblings, and missing attempts.
- [x] 1.3 Resolve the attributed failed shard payload and challenge ids from `work/shards/failed/{shard_basename}`; reject missing or mismatched payloads with `409`.
- [x] 1.4 Resolve the current challenge directory with the same lookup rules as resume/validation; reject missing or ambiguous directories with precise errors.
- [x] 1.5 Reuse existing validation gate and `ChallengeValidator` execution semantics without invoking Hermes.
- [x] 1.6 On success, write validate/complete passed progress events, move the shard from `failed/` to `done/`, update the attempt to `succeeded`, set artifact metadata, clear error, and mark the parent task `built`.
- [x] 1.7 On failure, keep the shard under `failed/`, write precise failed progress events, keep the attempt `failed`, and update `error` with the real validation reason.

## 2. HTTP API

- [x] 2.1 Add `POST /api/build-attempts/{id}/revalidate`.
- [x] 2.2 Return `200` with the repaired attempt payload on success.
- [x] 2.3 Return `409` with a precise message for ineligible status, stale sibling, missing failed shard, missing artifact directory, ambiguous challenge directory, or validation failure.
- [x] 2.4 Ensure no new `build_attempts` row is created by revalidate.
- [x] 2.5 Extend build-attempt detail/list payloads with a derived failure summary if backend derivation is chosen.

## 3. Build Attempts UI

- [x] 3.1 Change the navigation/page copy to Chinese: `构建记录` for the list and `构建运行 #N` for detail.
- [x] 3.2 Remove list-level `Start Worker` and `Validate` buttons from `src/web/static/js/views/build-attempts.js`.
- [x] 3.3 Keep list-level `刷新`, filters, and row actions; localize table columns and empty/loading/error copy.
- [x] 3.4 Detail actions: show `运行` only for queued attempts, `重新校验` + `重试构建` + `删除` for failed attempts, `重试构建` + `删除` for lost attempts, and `删除` for succeeded attempts.
- [x] 3.5 Wire `重新校验` to `POST /api/build-attempts/{id}/revalidate`; refresh the current detail after completion.
- [x] 3.6 Rename retry button copy to `重试构建` everywhere it creates a new attempt.
- [x] 3.7 Show failure summaries from progress evidence before `build_attempts.error`; render broad fallback as `构建执行失败`.
- [x] 3.8 Localize status labels: `待运行`, `运行中`, `成功`, `失败`, `丢失`; localize artifact labels: `已生成`, `缺失`, `未知`.

## 4. Tests

- [x] 4.1 Add service tests for successful revalidation of a failed attempt with an existing artifact.
- [x] 4.2 Add service tests for missing directory and stale sibling rejection.
- [x] 4.3 Add service coverage proving revalidate does not create a new attempt and updates the same row on success.
- [x] 4.4 Add API/status-path coverage through endpoint error translation and service errors.
- [x] 4.5 Add frontend/static tests proving list-level `Start Worker`/`Validate` are gone and Chinese detail actions render by status.
- [x] 4.6 Add regression coverage that a stale `missing_challenge`-style failed attempt can be repaired after the directory exists.

## 5. Verification

- [x] 5.1 Run focused tests for build attempts API, revalidation service, build reconciler, and runner validation (53 passed).
- [x] 5.2 Run frontend syntax checks for changed JS (`node --check` passed for the Build Attempts view and router).
- [x] 5.3 Run `openspec validate add-build-attempt-revalidation-ui --strict` successfully after aligning modified requirements with the current base specs.
