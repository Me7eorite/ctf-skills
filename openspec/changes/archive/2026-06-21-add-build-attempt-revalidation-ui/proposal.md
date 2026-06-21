## Why

Build Attempts currently mixes three different operator concepts in one
surface:

- Global queue actions (`Start Worker`, `Validate`) appear on the list page even
  though the page is a build-attempt history view.
- `Retry` correctly creates a new attempt and shard, but operators can mistake
  it for "repair this failed attempt".
- Failed attempts often show only the reconciler summary
  `shard execution failed`, hiding the actionable progress error such as
  `validate/failed ... missing_challenge`.

That confusion matters for host-side validation bugs. If Hermes created a valid
artifact but the runner's validation gate failed because of stale host state,
the correct operator action is not to redesign the challenge and not necessarily
to create another build attempt. The system needs a first-class "revalidate this
attempt" path that re-runs host validation against existing artifacts.

## What Changes

- Rename and reorganize the Build Attempts dashboard view with Chinese UI copy:
  the list page becomes `构建记录`, and detail pages present a single
  `构建运行 #N`.
- Remove list-level global execution buttons (`Start Worker`, `Validate`) from
  the Build Attempts view. The list page keeps filtering, refresh, row details,
  retry, and delete.
- Detail-page actions become status-scoped:
  - queued: `运行`
  - failed: `重新校验`, `重试构建`, `删除`
  - lost: `重试构建`, `删除`
  - succeeded: `删除`
- Keep existing retry semantics: `重试构建` creates a new build attempt and a
  new shard, preserving the source attempt via `resume_from_shard_basename`.
- Add a build-attempt revalidation endpoint. `重新校验` does not create a new
  attempt and does not invoke Hermes. It re-runs host validation against the
  existing challenge directory, writes fresh progress events, moves a repaired
  failed shard to `done/`, and updates the same `build_attempts` row.
- Surface a concise failure summary derived from progress events before falling
  back to the broad reconciler error, so operators see
  `校验失败：missing_challenge` instead of only `shard execution failed`.

## Capabilities

### Modified Capabilities

- `build-orchestration`: adds per-attempt revalidation, refines retry vs
  revalidate semantics, and updates the Build Attempts dashboard contract.
- `hermes-execution-protocol`: host validation remains runner-owned; this
  change adds a service/API path that reuses the same validation gate and
  `ChallengeValidator` behavior for an existing failed attempt without invoking
  Hermes.

### New Capabilities

None. This is a correction and clarification of the existing build-attempt
workflow.

## Impact

- **Code**: add a service-layer revalidation entry point, likely
  `BuildAttemptRevalidationService`, plus
  `POST /api/build-attempts/{id}/revalidate` in
  `web/build_attempts_endpoints.py`. Update `build-attempts.js` UI labels,
  buttons, detail actions, and failure-summary rendering. Reuse existing
  validation helpers rather than importing persistence into `hermes`.
- **Database**: no schema change. The same `build_attempts` row is updated.
- **Filesystem**: successful revalidation moves the attributed shard from
  `work/shards/failed/` to `work/shards/done/`. Failed revalidation leaves it
  under `failed/`.
- **Compatibility**: `POST /api/build-attempts/{id}/retry` keeps its current
  behavior and response shape. Existing `/api/actions/validate` remains a global
  validation action, but it is no longer exposed from the Build Attempts list.
- **Tests**: add service tests for successful and failed revalidation, API tests
  for status gating and no-new-attempt behavior, and frontend/static tests for
  Chinese labels and removed misleading buttons.
