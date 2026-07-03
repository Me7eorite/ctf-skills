## Why

Current validation-phase build failures treat very different failure modes as if they should all follow the same retry path. In batch workflows this wastes repair budget on noisy attempts, hides root causes like service readiness problems behind generic timeouts, and lowers overall batch success rate.

## What Changes

- Introduce a normalized failure taxonomy for validation-phase build-attempt failures.
- Route timeout, service-readiness, contract, and solver failures through different bounded recovery paths. A route may choose deterministic mechanical repair, Hermes repair with structured diagnostics, or no-op/escalation when deterministic repair is unsafe. Keep prompt-input classification as an explicit capture-point follow-up unless prompt diagnostics are added in this change.
- Stop automatic validation repair for an attempt once the same failure signature repeats within that attempt's active validation/repair invocation without progress.
- Keep sibling attempts in the same batch independent for validation/repair failures so one bad target does not stall the rest.
- Surface `validation_failure_class` and concise recovery summary in build-attempt diagnostics for validation-phase failures.
- Preserve existing runner-phase taxonomy and sequential infrastructure fail-fast behavior.

## Capabilities

### New Capabilities
- `batch-failure-governance`: class-aware validation failure routing, bounded retry policy, and attempt-local repair exhaustion.

### Modified Capabilities
- `build-orchestration`: build-attempt validation and repair behavior now depends on normalized validation failure classes, and sibling validation/repair failures remain isolated across attempts without changing existing infrastructure fail-fast behavior.

## Impact

Affected areas include `src/domain/validation.py`, `src/hermes/runner.py`, `src/services/build_attempt_auto_repair_service.py`, `src/services/build_attempt_revalidation_service.py`, `src/services/build_attempt_repair_service.py`, `src/services/build_orchestration_service.py`, `src/web/build_attempts_endpoints.py`, and the corresponding test coverage.
