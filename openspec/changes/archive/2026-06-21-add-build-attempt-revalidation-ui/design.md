## Context

The current build-attempt model deliberately separates audit from execution:
`build_attempts` rows live in PostgreSQL, while shard execution remains
file-backed under `work/shards/{pending,running,done,failed}/`. Retry already
uses that model correctly by creating a fresh attempt and pointing its shard at
the previous attempt's resume window.

That model does not cover "the artifact exists, but a host-side validation gate
misclassified this attempt." In that case, creating a new attempt is an
available workaround, but it is semantically noisy: it records a new build run
when the desired action is only to re-evaluate the existing artifact with fixed
host logic.

The dashboard also still exposes a list-level `Validate` button that starts the
global `challenge-factory validate` command. Operators can reasonably read that
as "validate this failed build attempt", but it does not update the attempt row
or move its failed shard.

## Goals / Non-Goals

**Goals:**

- Make Build Attempts UI copy Chinese and status-specific.
- Remove misleading list-level global actions from the Build Attempts view.
- Keep `重试构建` as a new-attempt operation.
- Add `重新校验` as a same-attempt host validation repair operation.
- Show the most actionable failure reason available from progress events.
- Keep Hermes runner independent from PostgreSQL.

**Non-Goals:**

- No design-iteration workflow. Revising an unsatisfactory challenge design is a
  separate design-task feature.
- No automatic background repair of all historical failed attempts.
- No schema migration or new build-attempt statuses.
- No replacement of the file-backed shard queue.
- No removal of `/api/actions/validate`; it remains available to legacy/global
  dashboard code.

## Decisions

### Decision 1: Revalidate is a service-layer operation, not a Hermes runner mode

The revalidation endpoint SHALL call a service-layer operation that reads the
existing build attempt, shard payload, current challenge directory, and progress
store, then invokes the same validation gate and `ChallengeValidator` behavior
used by the runner.

The service MAY reuse `hermes.validation.run_validation` if it can provide the
right progress store, paths, image checker, worker label, challenge ids, and a
current `ChallengeResumePlan`. It MUST NOT make `hermes.runner` depend on
`persistence` or make the runner update `build_attempts` directly.

**Why:** validation ownership remains in host code, while PostgreSQL audit
updates remain in services/repositories.

### Decision 2: Only failed attempts are eligible

`POST /api/build-attempts/{id}/revalidate` SHALL accept only
`build_attempts.status = failed`.

It SHALL reject:

- `queued` and `running` attempts because they still have an active execution
  path.
- `succeeded` attempts because they do not need repair.
- `lost` attempts because the shard outcome is missing rather than failed
  validation.
- stale failed attempts that have a newer sibling attempt, unless the
  implementation explicitly proves the newer sibling is absent from active
  states. The conservative default is "latest failed attempt only".

**Why:** this keeps revalidation from rewriting old audit history underneath a
newer attempt.

### Decision 3: Successful revalidation repairs the same attempt

When revalidation passes:

- Append `validate/running`, `validate/passed`, and `complete/passed` progress
  events under the same `shard_basename`.
- Move the attributed shard file from `failed/` to `done/`.
- Update the same `build_attempts` row to `succeeded`, set
  `artifact_status = present`, set `resulting_challenge_dir`, clear `error`,
  and set `finished_at`.
- Update the parent `design_tasks.status` to `built`.

When revalidation fails:

- Append `validate/running` when the gate passes and validator execution starts;
  otherwise append `validate/failed` with the gate error.
- Append or preserve `complete/failed` as appropriate.
- Keep the shard under `failed/`.
- Keep the attempt `failed`, update `error` to a precise message, and keep the
  parent task `build_failed`.

**Why:** the repaired state should be observable through the same queue and
database conventions as normal runner completion.

### Decision 4: Failure summaries prefer progress evidence

The API response for list and detail views SHOULD include a derived failure
summary, or the frontend SHALL derive it from `progress_events` when present.
The preferred source order is:

1. Latest `validate/failed` event's `error=...` token or message tail.
2. Latest `complete/failed` event message.
3. `build_attempts.error`.
4. Fallback `构建执行失败`.

The summary text shown in the UI SHALL be Chinese, for example
`校验失败：missing_challenge`.

**Why:** `shard execution failed` is a useful machine-level terminal category,
but not a useful operator diagnosis.

### Decision 5: Build Attempts list is read-only plus row actions

The list filter bar SHALL keep filters and `刷新`; it SHALL NOT show
`Start Worker` or `Validate`. Starting execution moves to the queued attempt
detail page as `运行`. Revalidating a failed attempt moves to the failed attempt
detail page as `重新校验`.

**Why:** execution actions should be anchored to the specific attempt they will
operate on.

## Implementation Notes

- The revalidation service should build the validation plan from current disk
  lookup rather than reusing a stale pre-Hermes snapshot.
- Revalidation should use a deterministic worker label such as
  `dashboard-revalidate` or the authenticated/operator label if one exists
  later.
- The shard move from failed to done should use the same basename-preserving
  queue conventions as `ShardQueue.complete`, but it starts from `failed/`
  rather than `running/`.
- If the failed shard file is missing but the artifact exists, the first
  implementation should return `409` instead of inventing a shard file.
  Repairing missing shard metadata can be a later recovery feature.
