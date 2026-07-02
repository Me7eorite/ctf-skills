## Why

Current build validation treats very different failure modes as if they should all follow the same retry path. In batch workflows this wastes repair budget on noisy attempts, hides root causes like service readiness problems behind generic timeouts, and lowers overall batch success rate.

## What Changes

- Introduce a normalized failure taxonomy for build-attempt validation and repair.
- Route timeout, service-readiness, prompt, contract, and solver failures through different bounded recovery paths.
- Stop automatic repair for an attempt once the same failure signature repeats without progress.
- Keep sibling attempts in the same batch independent so one bad target does not stall the rest.
- Surface the failure class and concise recovery summary in build-attempt diagnostics.

## Capabilities

### New Capabilities
- `batch-failure-governance`: class-aware batch failure routing, bounded retry policy, and attempt-local repair exhaustion.

### Modified Capabilities
- `build-orchestration`: build-attempt validation and repair behavior now depends on normalized failure classes, and batch execution must remain isolated across attempts.

## Impact

Affected areas include `src/domain/validation.py`, `src/hermes/runner.py`, `src/services/build_attempt_auto_repair_service.py`, `src/services/build_attempt_revalidation_service.py`, `src/services/build_attempt_repair_service.py`, `src/services/build_orchestration_service.py`, `src/web/build_attempts_endpoints.py`, and the corresponding test coverage.
