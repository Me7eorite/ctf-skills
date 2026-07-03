## 1. Taxonomy and policy plumbing

- [ ] 1.1 Extend the validation failure classifier with normalized batch-oriented validation failure classes and an invocation-local stable failure signature.
- [ ] 1.2 Define the first-rollout closed class set as `timeout`, `service-readiness`, `contract`, and `solver`; leave prompt-input failures unclassified until prompt capture points and diagnostics are added.
- [ ] 1.3 Define the runner phase / validation status / `validation_failure_details` diagnostic-code mapping, including precedence rules and explicit `no normalized validation class` outcomes for non-validation runner phases.
- [ ] 1.4 Define a small policy object for class-specific retry ceilings, route types (`deterministic`, `hermes`, `escalate`), and stop conditions.
- [ ] 1.5 Thread the derived normalized validation failure class through runner progress messages and attempt summaries as `validation_failure_class` without adding a new durable source-of-truth table.
- [ ] 1.6 Preserve `validation_failure_details` in validation report merge output or explicitly use `validation-history.json` as the primary structured source for API derivation.

## 2. Attempt-scoped repair behavior

- [ ] 2.1 Split the current deterministic auto-repair entrypoint behind a class-aware policy router instead of wrapping the existing all-repairs function with ad hoc call-site checks.
- [ ] 2.2 Make the policy router choose bounded route types from the normalized validation failure class: deterministic mechanical repair for known safe fixes, Hermes repair for solver/runtime tuning, or no-op/escalation when automatic repair is unsafe.
- [ ] 2.3 Thread latest `validation_failure_details`, stdout/stderr tails, and concise `failure_summary` into `BuildAttemptRepairService` direct repair context while preserving `validation_contract_errors` / `contract_errors` compatibility.
- [ ] 2.4 Extend retry/repair submission diagnostics so `BuildOrchestrationService` carries `validation_failure_details` from the same latest failed validation result used by direct repair and attempt-detail APIs.
- [ ] 2.5 Stop automatic repair when the same validation failure class and structured-or-derived signature repeats within the same runner validation/repair invocation without progress.
- [ ] 2.6 Ensure timeout and service-readiness validation failures do not consume a shared batch-wide retry budget.

## 3. Batch isolation and orchestration

- [ ] 3.1 Update build orchestration so one attempt's validation/repair failure cannot block sibling attempts in the same batch.
- [ ] 3.2 Preserve per-attempt failure summaries and expose `validation_failure_class` in build-attempt API responses only for validation-phase failures, deriving from `work/executions/<attempt_id>/current/state/validation-history.json` before metadata/progress fallbacks.
- [ ] 3.3 Keep retry and revalidate flows bounded by the latest attempt and its own invocation-local diagnostic history.
- [ ] 3.4 Preserve existing sequential consecutive-infrastructure fail-fast behavior.

## 4. Verification

- [ ] 4.1 Add classifier mapping tests for timeout, service-readiness, contract, solver, `validation_failure_details` precedence, and non-validation runner phases that should have no normalized validation class.
- [ ] 4.2 Add API response field tests proving failed validation attempts expose `validation_failure_class` from validation history and non-validation failures do not include it.
- [ ] 4.3 Add repair-policy router tests proving each first-rollout class selects the intended route type and does not route solver failures through deterministic mechanical repair.
- [ ] 4.4 Add repair-context tests proving direct repair and retry/repair submission both expose `validation_failure_details`, `validation_contract_errors`, stdout/stderr tails, and `failure_summary` to Hermes repair prompts without replacing legacy contract-error fields.
- [ ] 4.5 Add report merge or API-derivation tests proving `validation_failure_details` are not lost between validation, attempt-detail API, retry context, and manual repair context.
- [ ] 4.6 Add tests proving repeated identical validation failures stop auto-repair for one attempt only within a single invocation.
- [ ] 4.7 Add tests proving sibling attempts continue independently for validation/repair failures inside the same batch.
- [ ] 4.8 Add a no-regression test proving `consecutive_infra` can still abort sequential tail attempts.
