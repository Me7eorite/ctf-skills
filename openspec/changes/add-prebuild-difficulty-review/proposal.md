## Why

The asset-flow gate makes the design payload more measurable, but build
submission still needs an explicit pre-build review record. Without that
checkpoint, weak medium/hard designs can enter Build and the Build Agent may
spend effort implementing a challenge whose claimed difficulty already drifted
below the parent task.

## What Changes

- Add a pre-build difficulty review stage to `BuildOrchestrationService`.
- The review evaluates but does not rewrite a design.
- Failed reviews block Build submission before staging files or build attempts
  are created.
- Each review result is persisted for audit and future difficulty-drift
  metrics.
- The first implementation is deterministic and reuses the existing
  difficulty/asset-flow rubric; Hermes/model review can replace or augment the
  same service later.

## Impact

- New domain DTOs for difficulty review results.
- New service `DesignDifficultyValidator`.
- New `design_difficulty_reviews` persistence model, repository, and Alembic
  migration.
- Build submission now records a passed review before creating a build attempt,
  and records a failed review before raising `difficulty_review_failed`.
