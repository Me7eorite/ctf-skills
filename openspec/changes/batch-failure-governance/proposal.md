## Why

Current validation-phase build failures treat very different failure modes as if they should all follow the same retry path. In batch workflows this wastes repair budget on noisy attempts, hides root causes like service readiness problems behind generic timeouts, and lowers overall batch success rate.

The latest remote batch analysis on `192.168.6.233:/root/ctf-skills` showed the larger success-rate problem: the most recent `iter-009` queue processed 8 Pwn attempts and failed all 8 in `hermes_phase=validation`. A recent 80-report sample had 24 passed and 56 `nonzero_exit` failures; 49 failures were solver-class and 7 were service-readiness. The queue, Hermes connectivity, and build dispatch were not the bottleneck. The bottleneck was that generated Pwn artifacts entered validation with unstable service readiness, weak or missing application-level probes, path/layout mistakes, and reference exploits that connected but did not recover the flag.

This change therefore focuses first on governing validation failures consistently so the system stops spending repair budget blindly. Pre-validation artifact normalization and deeper Pwn scaffold ownership remain important follow-up work, but should land after the class/derivation/repair loop has a reliable source of truth.

## What Changes

- Introduce a normalized failure taxonomy for validation-phase build-attempt failures.
- Route timeout, service-readiness, contract, and solver failures through different bounded recovery paths. A route may choose deterministic mechanical repair, Hermes repair with structured diagnostics, or no-op/escalation when deterministic repair is unsafe. Keep prompt-input classification as an explicit capture-point follow-up unless prompt diagnostics are added in this change.
- Defer pre-validation normalization for generated Web/Pwn artifacts to Milestone B or a follow-up change. That follow-up must define the source chain for gate failures before implementation: a gate failure before host validation must enter `validation-history.json` as a synthetic failed validation round consumed by the same derivation helper.
- Defer treating the default Pwn xinetd/chroot launcher as a system-owned scaffold contract until the follow-up defines a safe overwrite matrix. Any future scaffold normalization must preserve challenge-specific source, binary, port, xinetd server args, flag path, Dockerfile dependencies, metadata, attachments, and solver logic.
- Require application-level readiness evidence and bounded solver diagnostics for Pwn validation so a port-open/xinetd-started container does not get mistaken for a solvable service. Pwn readiness evidence is modeled as `established`, `failed-fresh-connection`, or `unavailable` so missing evidence does not get misrouted as failed readiness.
- Stop automatic validation repair for an attempt once the same failure signature repeats within that attempt's active validation/repair invocation without progress.
- Add explicit solver/exp stability diagnostics so Web/Pwn reference exploits that fail validation expose whether the problem is target wiring, dependencies, unbounded reads, flag mismatch, payload assumptions, or missing diagnostic evidence.
- Keep solver-quality defects such as hardcoded default validation targets, unbounded reads, missing helper modules, payload assumptions, and missing rich exploit evidence diagnostic-first in Phase 1; route them into bounded repair and promote them to hard blockers only in later enforcement phases.
- Keep sibling attempts in the same batch independent for validation/repair failures so one bad target does not stall the rest.
- Surface `validation_failure_class` and concise recovery summary in build-attempt diagnostics for validation-phase failures.
- Preserve existing runner-phase taxonomy and sequential infrastructure fail-fast behavior.
- Use `work/executions/<attempt_id>/current/state/validation-history.json` as the primary structured source for validation failure class derivation. Existing report/progress/metadata fields remain compatibility fallbacks, not competing sources of truth.
- Extend the Hermes execution protocol so fresh validation results and runner-owned validation gates preserve the structured fields consumed by that derivation helper. This change does not require historical attempts to be backfilled.

## Capabilities

### New Capabilities
- `batch-failure-governance`: class-aware validation failure routing, bounded retry policy, and attempt-local repair exhaustion.

### Modified Capabilities
- `build-orchestration`: build-attempt validation and repair behavior now depends on normalized validation failure classes, and sibling validation/repair failures remain isolated across attempts without changing existing infrastructure fail-fast behavior.

## Impact

Affected areas include `src/domain/validation.py`, `src/hermes/runner.py`, `src/hermes/prompt.py`, `prompts/shard_prompt.md`, `src/services/build_attempt_auto_repair_service.py`, `src/services/build_attempt_revalidation_service.py`, `src/services/build_attempt_repair_service.py`, `src/services/build_orchestration_service.py`, `src/web/build_attempts_endpoints.py`, the Hermes validation-history/report merge surface, and the corresponding test coverage.

The first implementation slice should remain one-round practical: preserve rich diagnostics, route repair by class, stop identical no-progress loops, expose the class/signature in the existing API, and fix the current `pwn_prompt_eof` readiness-vs-solver semantics with the three-state readiness model. Milestone A acceptance is limited to that governance loop. Historical analytics, pre-validation normalization, scaffold overwrite, durable cross-request suppression, hard solver-quality gates, broad solver-style scanners, and full Pwn evidence-profile enforcement remain later work. Any Phase 2+ solver-quality or evidence-profile notes in this change are design context only until a follow-up change promotes them with explicit tests and acceptance criteria.
