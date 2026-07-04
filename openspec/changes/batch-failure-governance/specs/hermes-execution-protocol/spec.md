## ADDED Requirements

### Requirement: Validation history preserves governed failure evidence

The runner and host validation path SHALL preserve the structured validation evidence required by batch failure governance for fresh validation-phase failures. For each fresh failed validation round with build-attempt attribution, the execution workspace SHALL expose the latest failed validation record through `work/executions/<attempt_id>/current/state/validation-history.json`, which is the primary source consumed by the shared governance derivation helper. The record SHALL preserve the terminal validation phase, `solve_status`, `validation_status`, concise validation error or summary, elapsed time when available, `validation_failure_details` when available, bounded solver stdout/stderr tails when available, and any normalized failure signature or signature inputs produced by the validator or runner. Historical attempts that predate this change SHALL remain readable through existing report/progress/metadata fallbacks; this requirement SHALL NOT require retroactive evidence generation or a schema migration.

#### Scenario: Validator failure writes structured history
- **WHEN** `ChallengeValidator` returns a failed result for a build-attempt-attributed challenge
- **THEN** the runner SHALL preserve that result in the validation-history source consumed by governance derivation
- **AND** `validation_failure_details`, solver stdout/stderr tails, validation status, elapsed time, and concise failure summary SHALL be preserved when the validator returned them
- **AND** the shard report merge SHALL preserve `validation_failure_details` as a compatibility fallback when those details are available

#### Scenario: Runner validation gate failure writes validation history evidence
- **WHEN** the runner writes `validate/failed` because design, implement, build, document, `validate.sh`, or `writenup/exp.py` evidence is incomplete before invoking `ChallengeValidator`
- **THEN** it SHALL write a failed validation-history round before API, repair-context, or repeated-signature derivation runs
- **AND** the record SHALL classify the source as a validation-phase gate or contract failure without pretending that solver execution occurred
- **AND** missing solver/runtime fields SHALL be represented as unavailable rather than omitted when those fields are needed by repair context

#### Scenario: Older attempts remain readable without backfill
- **WHEN** an older failed attempt has report, progress, or metadata evidence but lacks the new validation-history fields
- **THEN** governance derivation MAY fall back to the legacy evidence sources
- **AND** attempt detail, retry, repair, and list responses SHALL not fail solely because the new structured history record is absent
