## 1. Taxonomy and policy plumbing

- [ ] 1.1 Extend the validation failure classifier with normalized batch-oriented validation failure classes and an invocation-local stable failure signature.
- [ ] 1.2 Define the first-rollout closed class set as `timeout`, `service-readiness`, `contract`, and `solver`; leave prompt-input failures unclassified until prompt capture points and diagnostics are added.
- [ ] 1.3 Define the runner phase / validation status / `validation_failure_details` diagnostic-code mapping, including precedence rules and explicit `no normalized validation class` outcomes for non-validation runner phases.
- [ ] 1.4 Define a small policy object for class-specific retry ceilings, route types (`deterministic`, `hermes`, `escalate`), and stop conditions.
- [ ] 1.5 Thread the derived normalized validation failure class through runner progress messages and attempt summaries as `validation_failure_class` without adding a new durable source-of-truth table.
- [ ] 1.6 Preserve `validation_failure_details` in validation report merge output or explicitly use `validation-history.json` as the primary structured source for API derivation.
- [ ] 1.7 Add exp-specific solver diagnostics for missing dependencies, hardcoded Web/Pwn service targets, unbounded Pwn solver I/O, flag mismatch, prompt EOF, offset/payload, leak parsing, and remote/local mismatch evidence.
- [ ] 1.8 Ensure repeated-failure signatures include class plus diagnostic detail such as missing module, detail code, path, traceback frame, or concise stderr marker so distinct solver failures are not collapsed solely because they share class `solver`.
- [ ] 1.9 Add diagnostic classes or detail codes for insufficient validation evidence, guessed/stale Pwn payload constants, missing Pwn debug evidence, and menu synchronization evidence gaps.
- [ ] 1.10 Normalize volatile values out of failure signatures, including elapsed time, container ids, random ports, execution workspace prefixes, and non-address-specific memory noise.
- [ ] 1.11 Define simple/intermediate/advanced Pwn solver evidence profiles and map common techniques to the minimum required evidence profile.

## 2. Attempt-scoped repair behavior

- [ ] 2.1 Split the current deterministic auto-repair entrypoint behind a class-aware policy router instead of wrapping the existing all-repairs function with ad hoc call-site checks.
- [ ] 2.2 Make the policy router choose bounded route types from the normalized validation failure class: deterministic mechanical repair for known safe fixes, Hermes repair for solver/runtime tuning, or no-op/escalation when automatic repair is unsafe.
- [ ] 2.3 Thread latest `validation_failure_details`, stdout/stderr tails, and concise `failure_summary` into `BuildAttemptRepairService` direct repair context while preserving `validation_contract_errors` / `contract_errors` compatibility.
- [ ] 2.4 Extend retry/repair submission diagnostics so `BuildOrchestrationService` carries `validation_failure_details` from the same latest failed validation result used by direct repair and attempt-detail APIs.
- [ ] 2.5 Stop automatic repair when the same validation failure class and structured-or-derived signature repeats within the same runner validation/repair invocation without progress.
- [ ] 2.6 Ensure timeout and service-readiness validation failures do not consume a shared batch-wide retry budget.
- [ ] 2.7 Add or tighten Web/Pwn solver stability contract checks: default path must use `CHAL_HOST`/`CHAL_PORT`, hardcoded loopback/container/fixed-port targets are allowed only in explicit local debug branches, and Pwn solver reads/process interactions must be bounded.
- [ ] 2.8 Route solver-class failures to Hermes repair with exp-specific context, including current `writenup/exp.py`, `validate.sh`, `writenup/pwn_debug_report.json` when present, structured failure details, stdout/stderr tails, and concise failure summary.
- [ ] 2.9 Keep deterministic repair limited to safe wrapper/diagnostic normalization for solver failures; do not treat arbitrary payload, ROP, offset, leak parsing, or flag extraction tuning as deterministic auto-repair.
- [ ] 2.10 Add first-pass reference-solver quality gates before document completion: static exp stability checks, bounded local/remote smoke evidence when practical, and explicit skip reasons when bounded smoke is unavailable.
- [ ] 2.11 Require or generate concise `writenup/pwn_debug_report.json` evidence for non-trivial Pwn exploits, covering mitigations, prompt/menu sync, offset source, libc/PIE/gadget source, leak parsing, local result, and remote/container result where available.
- [ ] 2.12 Normalize validation diagnostics so failed `validate.sh` captures service state, recent logs, readiness probe result, exact solver command, solver stdout/stderr tails, exit code, and final flag candidate without polluting stdout.
- [ ] 2.13 Add repair-context budgets for solver stdout/stderr, service logs, debug reports, and file context; include explicit truncation markers when caps are hit.
- [ ] 2.14 Stage enforcement so classification and diagnostic visibility ship before hard exp-stability blockers, and Pwn evidence profiles apply only after profile-specific tests are green.

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
- [ ] 4.9 Add exp-stability contract tests for hardcoded Web/Pwn host/port defaults, allowed explicit local debug branches, unbounded pwntools/socket/process reads, and missing solver dependencies.
- [ ] 4.10 Add solver repair-context tests proving Hermes repair receives `writenup/exp.py`, `validate.sh`, pwn debug report context, structured failure details, stdout/stderr tails, and failure summary.
- [ ] 4.11 Add repeated-signature tests proving `solver:missing_dependency:<module>` and `solver:flag_mismatch` or `solver:pwn_prompt_eof:<marker>` are treated as materially different failures within the same bounded budget.
- [ ] 4.12 Add initial solver-quality gate tests proving document completion is blocked when `exp.py` exists but lacks required stability/evidence contracts.
- [ ] 4.13 Add Pwn payload-evidence tests for guessed offsets, libc/PIE/gadget constants, leak parsing evidence, and menu synchronization evidence.
- [ ] 4.14 Add validation-diagnostic-envelope tests proving failed validation preserves solver stdout/stderr tails, service logs/readiness evidence, exact solver command, exit code, and structured failure details for the next repair prompt.
- [ ] 4.15 Add Pwn evidence-profile tests proving simple ret2text/ret2win cases use a lightweight evidence profile while canary/PIE/libc/ROP cases require richer evidence.
- [ ] 4.16 Add signature-normalization tests proving volatile fields do not create fake new failures and stable diagnostic markers still distinguish materially different failures.
- [ ] 4.17 Add repair-context budget tests proving oversized logs/debug reports are truncated with explicit markers and useful tails/summaries remain present.
- [ ] 4.18 Add staged-rollout tests proving legacy attempt details remain readable when new diagnostic/evidence fields are unavailable.
