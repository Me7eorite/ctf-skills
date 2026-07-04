## Why

Current validation-phase build failures treat very different failure modes as if they should all follow the same retry path. In batch workflows this wastes repair budget on noisy attempts, hides root causes like service readiness problems behind generic timeouts, and lowers overall batch success rate.

The latest remote batch analysis on `192.168.6.233:/root/ctf-skills` showed the larger success-rate problem: the most recent `iter-009` queue processed 8 Pwn attempts and failed all 8 in `hermes_phase=validation`. A recent 80-report sample had 24 passed and 56 `nonzero_exit` failures; 49 failures were solver-class and 7 were service-readiness. The queue, Hermes connectivity, and build dispatch were not the bottleneck. The bottleneck was that generated Pwn artifacts entered validation with unstable service readiness, weak or missing application-level probes, path/layout mistakes, and reference exploits that connected but did not recover the flag.

This change therefore governs failures and also moves the most common Pwn failure prevention earlier in the pipeline so the system has a realistic chance of passing in the first generation/repair invocation.

## What Changes

- Introduce a normalized failure taxonomy for validation-phase build-attempt failures.
- Route timeout, service-readiness, contract, and solver failures through different bounded recovery paths. A route may choose deterministic mechanical repair, Hermes repair with structured diagnostics, or no-op/escalation when deterministic repair is unsafe. Keep prompt-input classification as an explicit capture-point follow-up unless prompt diagnostics are added in this change.
- Add a pre-validation normalization gate for generated Web/Pwn artifacts before the first host validation run. The gate should deterministically fix or reject known high-frequency defects such as nested output/challenges trees, non-isolated Compose commands, bad compose path construction, missing Pwn xinetd/chroot scaffold files, and insufficient validation diagnostics.
- Treat the default Pwn xinetd/chroot launcher as a system-owned scaffold contract rather than a prompt preference. Generation may fill challenge-specific source, binary, port, metadata, and solver logic, but the build/validation path should normalize the deployment scaffold and validate wrapper to the canonical form before spending Hermes repair budget.
- Require application-level readiness evidence and bounded solver diagnostics for Pwn validation so a port-open/xinetd-started container does not get mistaken for a solvable service.
- Stop automatic validation repair for an attempt once the same failure signature repeats within that attempt's active validation/repair invocation without progress.
- Add explicit solver/exp stability diagnostics so Web/Pwn reference exploits that fail validation expose whether the problem is target wiring, dependencies, unbounded reads, flag mismatch, payload assumptions, or missing diagnostic evidence.
- Make the first rollout diagnostic-first for deep exploit-quality evidence, but hard-block deterministic stability defects that are already known to waste an entire batch lane: missing/default Compose isolation, invalid challenge layout, missing validation entrypoint, missing Pwn scaffold files for xinetd/chroot tasks, hardcoded default validation target in `exp.py`, unbounded solver reads that can hang validation, and missing validation diagnostics after a validation failure.
- Keep sibling attempts in the same batch independent for validation/repair failures so one bad target does not stall the rest.
- Surface `validation_failure_class` and concise recovery summary in build-attempt diagnostics for validation-phase failures.
- Preserve existing runner-phase taxonomy and sequential infrastructure fail-fast behavior.
- Use `work/executions/<attempt_id>/current/state/validation-history.json` as the primary structured source for validation failure class derivation. Existing report/progress/metadata fields remain compatibility fallbacks, not competing sources of truth.

## Capabilities

### New Capabilities
- `batch-failure-governance`: class-aware validation failure routing, bounded retry policy, and attempt-local repair exhaustion.

### Modified Capabilities
- `build-orchestration`: build-attempt validation and repair behavior now depends on normalized validation failure classes, and sibling validation/repair failures remain isolated across attempts without changing existing infrastructure fail-fast behavior.

## Impact

Affected areas include `src/domain/validation.py`, `src/hermes/runner.py`, `src/hermes/prompt.py`, `prompts/shard_prompt.md`, `src/services/build_attempt_auto_repair_service.py`, `src/services/build_attempt_revalidation_service.py`, `src/services/build_attempt_repair_service.py`, `src/services/build_orchestration_service.py`, `src/web/build_attempts_endpoints.py`, and the corresponding test coverage.

The first implementation slice should remain one-round practical: normalize deterministic artifact/scaffold/validation defects before the first validation run, preserve rich diagnostics, route repair by class, stop identical no-progress loops, and expose the class/signature in the existing API. Historical analytics, durable cross-request suppression, and full Pwn evidence-profile enforcement remain later work.
