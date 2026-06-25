## 1. Domain and Review Service

- [x] 1.1 Add `DifficultyReviewResult` and persisted review DTOs.
- [x] 1.2 Add `DesignDifficultyValidator` as a review-only service.
- [x] 1.3 Reuse the deterministic difficulty/asset-flow rubric to produce
  pass/fail, reasons, detected risks, and required revisions.
- [x] 1.4 Expose all difficulty violations for review persistence without
  changing the existing design-time strict/lenient behavior.

## 2. Persistence

- [x] 2.1 Add `DesignDifficultyReview` SQLAlchemy model.
- [x] 2.2 Add `DesignDifficultyReviewRepository` with append-only `record`.
- [x] 2.3 Add Alembic migration `0016_design_difficulty_reviews`.
- [x] 2.4 Export model/repository from existing package entry points.
- [x] 2.5 Add repository support to supersede a failed draft, store review
  feedback on the latest attempt, and return the task to Design retry.

## 3. Build Orchestration

- [x] 3.1 Run pre-build difficulty review in `BuildOrchestrationService._prepare`.
- [x] 3.2 Persist failed review results and block submission before shard staging.
- [x] 3.3 Persist passed review results in the build-attempt commit transaction.
- [x] 3.4 Surface failed reviews with `BuildOrchestrationError.code =
  "difficulty_review_failed"`.
- [x] 3.5 Preserve repair clean-mode container reuse while keeping explicit clean
  rebuilds as new containers.
- [x] 3.6 On failed review, supersede the draft design and requeue the design
  task before raising `difficulty_review_failed`.

## 4. Tests

- [x] 4.1 Assert submitted tasks record passed difficulty reviews.
- [x] 4.2 Assert invalid medium designs are blocked before build attempts or
  pending shards are created.
- [x] 4.3 Keep focused design-domain, prompt, design-service, and build
  orchestration tests passing.
- [x] 4.4 Assert failed pre-build review requeues the task, supersedes the
  draft, and feeds review guidance into the next design prompt.

## 5. Verification

- [x] 5.1 Run focused Ruff checks for touched files.
- [x] 5.2 Run focused pytest suite.
- [ ] 5.3 Run mypy successfully.
  - Blocked: current environment reports unrelated SQLAlchemy missing-stub and
    existing project type errors outside this change.
- [x] 5.4 Run `uv run openspec validate add-prebuild-difficulty-review --strict`.
