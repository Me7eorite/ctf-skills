## Phase 1: Classification, diagnostics, and attempt-local repair governance

## 1. Taxonomy and shared derivation

- [x] 1.1 Extend the validation failure classifier with normalized batch-oriented validation failure classes and an invocation-local stable failure signature.
- [x] 1.2 Define the first-rollout closed class set as `timeout`, `service-readiness`, `contract`, and `solver`; leave prompt-input failures unclassified until prompt capture points and diagnostics are added.
- [x] 1.3 Define the runner phase / validation status / `validation_failure_details` diagnostic-code mapping, including precedence rules and explicit `no normalized validation class` outcomes for non-validation runner phases.
- [x] 1.4 Keep existing detailed validation diagnostic codes as signature inputs and repair context; do not replace them with only the normalized class.
- [x] 1.5 Implement a shared latest-failed-validation derivation helper that reads `work/executions/<attempt_id>/current/state/validation-history.json` first, then falls back to report entries, validation status, contract errors, progress messages, and artifact metadata.
- [x] 1.6 Expose a single attempt-level `validation_failure_class` only for the current one-build-attempt-to-one-challenge flow; return no class or per-challenge data rather than guessing if a multi-challenge attempt is encountered.
- [x] 1.7 Thread the derived normalized validation failure class through runner progress messages and attempt summaries as `validation_failure_class` without adding a new durable source-of-truth table.
- [x] 1.8 Ensure repeated-failure signatures include class plus diagnostic detail such as missing module, detail code, path, traceback frame, prompt marker, validation status, or concise stderr marker so distinct solver failures are not collapsed solely because they share class `solver`.
- [x] 1.9 Normalize volatile values out of failure signatures, including elapsed time, container ids, random ports, execution workspace prefixes, and non-address-specific memory noise.
- [x] 1.10 Preserve `validation_failure_details` in validation report merge output as a compatibility fallback, while keeping `validation-history.json` as the primary structured derivation source.

## 2. Attempt-scoped repair behavior

- [x] 2.1 Define a small policy object for class-specific retry ceilings, route types (`deterministic`, `hermes`, `escalate`), and stop conditions, including the evidence rule that prompt/menu EOF is `service-readiness` only when readiness is not established and `solver` once readiness evidence exists.
- [x] 2.2 Split the current deterministic auto-repair entrypoint behind a class-aware policy router instead of wrapping the existing all-repairs function with ad hoc call-site checks.
- [x] 2.3 Make the policy router choose bounded route types from the normalized validation failure class: deterministic mechanical repair for known safe fixes, Hermes repair for solver/runtime tuning, or no-op/escalation when automatic repair is unsafe.
- [x] 2.4 Keep deterministic repair limited to safe wrapper/diagnostic normalization for solver failures; do not treat arbitrary payload, ROP, offset, leak parsing, or flag extraction tuning as deterministic auto-repair.
- [x] 2.5 Thread latest `validation_failure_details`, stdout/stderr tails, concise `failure_summary`, normalized class, and signature into `BuildAttemptRepairService` direct repair context while preserving `validation_contract_errors` / `contract_errors` compatibility.
- [x] 2.6 Extend retry/repair submission diagnostics so `BuildOrchestrationService` carries `validation_failure_details`, normalized class, and signature from the same latest failed validation result used by direct repair and attempt-detail APIs.
- [x] 2.7 Stop runner automatic repair when the same validation failure class and structured-or-derived signature repeats within the same runner validation/repair invocation without progress.
- [x] 2.8 Do not suppress dashboard manual repair, retry, or revalidate across invocations in Phase 1; pass the latest class/signature as context only.
- [x] 2.9 Route solver-class failures to Hermes repair with exp-specific context, including current `writenup/exp.py`, `validate.sh`, `writenup/pwn_debug_report.json` when present, structured failure details, stdout/stderr tails, concise failure summary, class, and signature.
- [x] 2.10 Normalize validation diagnostics so failed `validate.sh` captures service state, recent logs, readiness probe result, exact solver command, solver stdout/stderr tails, exit code, and final flag candidate without polluting stdout when those fields are available.
- [x] 2.11 Add repair-context budgets for solver stdout/stderr, service logs, debug reports, and file context; include explicit truncation markers when caps are hit.

## 3. Batch isolation and orchestration

- [ ] 3.1 Verify build orchestration so one attempt's validation/repair failure cannot block sibling attempts in the same batch.
- [x] 3.2 Preserve per-attempt failure summaries and expose `validation_failure_class` in build-attempt API list/detail responses only for validation-phase failures, deriving from the shared latest-failed-validation helper.
- [ ] 3.3 Keep retry and revalidate flows bounded by the latest attempt and its own invocation-local diagnostic history.
- [ ] 3.4 Preserve existing sequential consecutive-infrastructure fail-fast behavior.
- [ ] 3.5 Ensure validation-phase failures remain `failure_type=validation` and do not increment the sequential `consecutive_infra` streak.

## 4. Phase 1 verification

- [ ] 4.1 Add classifier mapping tests for timeout, service-readiness, contract, solver, `validation_failure_details` precedence, context-sensitive prompt/menu EOF classification, and non-validation runner phases that should have no normalized validation class.
- [ ] 4.2 Add shared derivation tests proving `validation-history.json` is preferred over report/progress/metadata fallbacks and legacy attempts remain readable when history is missing.
- [ ] 4.3 Add API response field tests proving failed validation attempts expose `validation_failure_class` from the shared derivation helper and non-validation failures do not include it.
- [ ] 4.4 Add a multi-challenge guard test proving an attempt-level class is not guessed when a build attempt contains multiple failed challenge results.
- [ ] 4.5 Add repair-policy router tests proving each first-rollout class selects the intended route type and does not route solver failures through deterministic mechanical repair.
- [ ] 4.6 Add repair-context tests proving direct repair and retry/repair submission both expose `validation_failure_details`, `validation_contract_errors`, stdout/stderr tails, `failure_summary`, normalized class, and signature to Hermes repair prompts without replacing legacy contract-error fields.
- [ ] 4.7 Add report merge/API-derivation tests proving `validation_failure_details` are not lost between validation, attempt-detail API, retry context, and manual repair context.
- [ ] 4.8 Add tests proving repeated identical validation failures stop runner auto-repair for one attempt only within a single invocation.
- [ ] 4.9 Add tests proving dashboard manual repair, retry, and revalidate are not cross-request suppressed by invocation-local repeated-signature state.
- [ ] 4.10 Add tests proving sibling attempts continue independently for validation/repair failures inside the same batch.
- [ ] 4.11 Add a no-regression test proving `consecutive_infra` can still abort sequential tail attempts for non-validation infrastructure failures.
- [ ] 4.12 Add a no-regression test proving validation failures do not increment the `consecutive_infra` streak.
- [ ] 4.13 Add solver repair-context tests proving Hermes repair receives `writenup/exp.py`, `validate.sh`, pwn debug report context, structured failure details, stdout/stderr tails, failure summary, class, and signature.
- [ ] 4.14 Add repeated-signature tests proving `solver:missing_dependency:<module>`, `solver:flag_mismatch`, `solver:pwn_prompt_eof:<marker>` after readiness is established, and `service-readiness:pwn_prompt_eof:<marker>` before readiness is established are treated as materially different failures within the same bounded budget.
- [ ] 4.15 Add validation-diagnostic-envelope tests proving failed validation preserves solver stdout/stderr tails, service logs/readiness evidence, exact solver command, exit code, and structured failure details for the next repair prompt when those fields are available.
- [ ] 4.16 Add signature-normalization tests proving volatile fields do not create fake new failures and stable diagnostic markers still distinguish materially different failures.
- [ ] 4.17 Add repair-context budget tests proving oversized logs/debug reports are truncated with explicit markers and useful tails/summaries remain present.
- [ ] 4.18 Add staged-rollout tests proving legacy attempt details remain readable when new diagnostic/evidence fields are unavailable.

## Later phases: Solver-quality enforcement and evidence profiles

- [ ] 5.1 Add or tighten Web/Pwn solver stability contract checks: default path must use `CHAL_HOST`/`CHAL_PORT`, hardcoded loopback/container/fixed-port targets are allowed only in explicit local debug branches, and Pwn solver reads/process interactions must be bounded.
- [ ] 5.2 Add diagnostic classes or detail codes for insufficient validation evidence, guessed/stale Pwn payload constants, missing Pwn debug evidence, and menu synchronization evidence gaps.
- [ ] 5.3 Define simple/intermediate/advanced Pwn solver evidence profiles and map common techniques to the minimum required evidence profile.
- [ ] 5.4 Add first-pass reference-solver quality gates before document completion after Phase 1 diagnostic visibility is shipped: static exp stability checks, bounded local/remote smoke evidence when practical, and explicit skip reasons when bounded smoke is unavailable.
- [ ] 5.5 Require or generate concise `writenup/pwn_debug_report.json` evidence for non-trivial Pwn exploits, covering mitigations, prompt/menu sync, offset source, libc/PIE/gadget source, leak parsing, local result, and remote/container result where available.
- [ ] 5.6 Add exp-stability contract tests for hardcoded Web/Pwn host/port defaults, allowed explicit local debug branches, unbounded pwntools/socket/process reads, and missing solver dependencies.
- [ ] 5.7 Add initial solver-quality gate tests proving document completion is blocked when `exp.py` exists but lacks required stability/evidence contracts.
- [ ] 5.8 Add Pwn payload-evidence tests for guessed offsets, libc/PIE/gadget constants, leak parsing evidence, and menu synchronization evidence.
- [ ] 5.9 Add Pwn evidence-profile tests proving simple ret2text/ret2win cases use a lightweight evidence profile while canary/PIE/libc/ROP cases require richer evidence.
