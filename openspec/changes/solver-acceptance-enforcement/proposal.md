## Why

Generated Web/Pwn challenges can currently reach validation with a weak or broken reference solver, and repair may stop after classifying the failure without guaranteeing that `writenup/exp.py` becomes usable. Operators need a hard acceptance boundary: a build cannot be considered successful unless the reference solver passes against the real validation service path, and solver repair must either make measurable progress or hand control back with a clear blocked reason.

## What Changes

- Add a solver acceptance gate for Web/Pwn builds: `writenup/exp.py` must pass through `validate.sh` against the host-provided `CHAL_HOST` / `CHAL_PORT` validation target before publish, revalidate promotion, or `build_attempts.succeeded`.
- Extend the existing `ChallengeValidator.validate_one()` / validation-history path with additive solver acceptance fields; preserve `contract_errors`, `failure_details`, `validation_failure_details`, and older history readability.
- Add static and runtime solver-quality checks for default validation paths, including host/port use, bounded reads/loops, helper-module availability, and organizer-file/flag leakage guards.
- Require solver repair rounds to prove progress by changing solver/debug evidence, validation diagnostics, validation signatures, or the solver acceptance fingerprint before consuming more repair budget.
- Add a bounded escalation path when solver repair cannot prove progress: regenerate the solver when safe, or fail with an explicit human-action reason. Challenge regeneration is deferred to a later change and may only be requested as a recorded human-action reason in this scope.
- Require clean final validation in the current execution workspace after any repair or regeneration before publication, bound to the same output manifest that will be published or promoted.
- Preserve Phase 1 governance behavior from `batch-failure-governance`: failure classes and signatures remain diagnostic inputs, but solver acceptance is the hard release condition.

## Capabilities

### New Capabilities
- `solver-acceptance-enforcement`: hard reference-solver acceptance gates, solver-quality diagnostics, repair-progress enforcement, blocked/regeneration outcomes, and clean final validation requirements.

### Modified Capabilities
- `build-orchestration`: build attempts, retry, repair, and revalidate flows must treat solver acceptance as a required terminal success condition and must expose explicit blocked/regeneration outcomes as diagnostics on failed attempts, not as new `build_attempts.status` values.
- `hermes-execution-protocol`: runner validation and repair rounds must preserve solver-progress evidence, solver-quality diagnostics, regeneration decisions, output-manifest-bound acceptance, and clean final validation records.

## Impact

Affected areas include `src/domain/validation.py`, `src/domain/validation_failure_governance.py`, `src/domain/validation_repair_policy.py`, `src/hermes/runner.py`, `src/hermes/prompt.py`, `src/hermes/validation.py`, `src/services/build_attempt_auto_repair_service.py`, `src/services/build_attempt_repair_service.py`, `src/services/build_attempt_revalidation_service.py`, `src/services/build_orchestration_service.py`, `src/web/build_attempts_endpoints.py`, dashboard display for blocked solver failures, and tests covering Web/Pwn validation, solver repair, regeneration, retry/revalidate, and final publication fences.

No schema migration is required for the first slice unless the implementation chooses to persist a new durable blocked reason. Existing progress events, validation history, attempt summaries, report fields, and endpoint-derived fields should be preferred. `blocked` is a solver acceptance outcome, blocked reason, or diagnostic summary on a failed attempt; it is not a sixth `build_attempts.status` value in this change. Any new fields must be additive and bounded to the current attempt/result rows; list APIs must not scan unrelated execution histories.
