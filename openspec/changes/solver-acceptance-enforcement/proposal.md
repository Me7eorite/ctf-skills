## Why

Generated Web/Pwn challenges can currently reach validation with a weak or broken reference solver, and repair may stop after classifying the failure without guaranteeing that `writenup/exp.py` becomes usable. Operators need a hard acceptance boundary: a build cannot be considered successful unless the reference solver passes against the real validation service path, and solver repair must either make measurable progress or hand control back with a clear blocked reason.

The current system already has Phase 1 validation failure governance, Pwn failure-stage diagnostics, retry context carrying first/latest validation failures, current-workspace repair/revalidation, and manifest-bound publication consistency. This change is therefore an incremental enforcement change: keep those mechanisms as the baseline and add the missing solver-acceptance proof, progress fingerprint, and UI/API surfacing.

## What Changes

- Add a solver acceptance gate for Web/Pwn builds on top of the existing host validation path: `writenup/exp.py` must pass through `validate.sh` against the host-provided `CHAL_HOST` / `CHAL_PORT` validation target before publish, revalidate promotion, or `build_attempts.succeeded`.
- Extend the existing `ChallengeValidator.validate_one()` / validation-history path with additive solver acceptance fields; preserve `contract_errors`, `failure_details`, `validation_failure_details`, and older history readability.
- Extend existing static and runtime solver-quality checks for default validation paths, focusing only on gaps not already covered by contract checks, Pwn evidence freshness, and failure governance.
- Require solver repair rounds to prove progress by changing solver/debug evidence, validation diagnostics, validation signatures, or the solver acceptance fingerprint before consuming more repair budget; keep the existing `validation_failure_fingerprints()` path as the base guard.
- Add bounded blocked-route diagnostics when solver repair cannot prove progress. Solver-only regeneration remains a possible later route inside the existing repair abstraction; automated challenge regeneration is still out of scope and may only be recorded as a human-action reason.
- Require clean final validation in the current execution workspace after any repair before publication, with solver acceptance bound to the same output manifest that will be published or promoted.
- Preserve Phase 1 governance behavior from `batch-failure-governance`: failure classes and signatures remain diagnostic inputs, but solver acceptance is the hard release condition.

## Capabilities

### New Capabilities
- `solver-acceptance-enforcement`: hard reference-solver acceptance gates, solver-quality diagnostics, repair-progress enforcement, blocked/regeneration outcomes, and clean final validation requirements.

### Modified Capabilities
- `build-orchestration`: build attempts, retry, repair, and revalidate flows must treat solver acceptance as a required terminal success condition and must expose explicit blocked/regeneration outcomes as diagnostics on failed attempts, not as new `build_attempts.status` values.
- `hermes-execution-protocol`: runner validation and repair rounds must preserve solver-progress evidence, solver-quality diagnostics, regeneration decisions, output-manifest-bound acceptance, and clean final validation records.

## Impact

Affected areas include `src/domain/validation.py`, `src/domain/validation_failure_governance.py`, `src/domain/validation_repair_policy.py`, `src/domain/validation_state.py`, `src/hermes/runner.py`, `src/hermes/prompt.py`, `src/hermes/report.py`, `src/hermes/validation.py`, `src/services/build_attempt_repair_service.py`, `src/services/build_attempt_revalidation_service.py`, `src/services/build_orchestration_service.py`, `src/web/build_attempts_endpoints.py`, dashboard display for blocked solver failures, and tests covering Web/Pwn validation, solver repair progress, retry/revalidate, API/detail/list exposure, and final publication fences.

No schema migration is required for the first slice unless the implementation chooses to persist a new durable blocked reason. Existing progress events, validation history, attempt summaries, report fields, and endpoint-derived fields should be preferred. `blocked` is a solver acceptance outcome, blocked reason, or diagnostic summary on a failed attempt; it is not a sixth `build_attempts.status` value in this change. Any new fields must be additive and bounded to the current attempt/result rows; list APIs must not scan unrelated execution histories.
