## Context

This change builds on `add-design-asset-flow-gate`. Design generation already
validates the structured payload, but Build submission is the last inexpensive
point to stop weak designs before they turn into implementation work.

## Decision

Add a lightweight `DesignDifficultyValidator` service that produces:

```json
{
  "pass": true,
  "claimed_difficulty": "hard",
  "actual_difficulty": "hard",
  "confidence": 0.84,
  "reasons": [],
  "detected_risks": [],
  "required_revision": []
}
```

The service is intentionally review-only. It never mutates the design payload
and never asks Build to compensate for missing design quality. On failure,
`BuildOrchestrationService` records the failed review, supersedes the current
draft design, writes the required revision feedback onto the latest design
attempt, requeues the design task when attempts remain, and raises
`difficulty_review_failed`.

The initial reviewer is deterministic: it reuses the existing difficulty rubric
and asset-flow checks, with violations persisted as `reasons`,
`detected_risks`, and `required_revision`. This gives immediate quality gates
without adding another Agent failure mode.

## Persistence

`design_difficulty_reviews` is append-only and stores one row per pre-build
review:

- `design_task_id`
- `challenge_design_id`
- `passed`
- `claimed_difficulty`
- `actual_difficulty`
- `confidence`
- `reasons`
- `detected_risks`
- `required_revision`
- `reviewer`
- `created_at`

Append-only storage lets operators inspect repeated failed submissions and
later compute difficulty drift by request, category, technique, or reviewer.

## Failure Semantics

Failed review happens before staging files are written, so no pending shard can
survive a blocked submission. The failed review and design requeue happen in
one transaction so the system cannot record diagnostics while leaving the task
stuck in `designed`.

Passed review is recorded in the same transaction that creates the build
attempt, so a successfully submitted build always has a review audit row.

Retry and repair submissions are also reviewed because they submit the current
draft design back into Build. Repair may use `execution_mode = clean` while
still reusing the existing execution container; this change keeps that path
distinct from explicit clean rebuilds.

When a failed review requeues the task, the next `ChallengeDesignService`
attempt uses the existing retry-feedback path: `latest_attempt.last_error`
contains the review reasons and required revisions.
