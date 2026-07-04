## Milestone A acceptance checklist: validation failure governance

These top-level items are milestone acceptance checks. The detailed implementation tasks below track partial progress; do not mark an acceptance check complete until all referenced detailed tasks and tests are green. Keep this milestone focused on the governance loop: classification, shared derivation, API/repair-context exposure, class-aware routing, and invocation-local repeated-signature stops. Pre-validation normalization, Pwn scaffold systemization, and solver-quality hard enforcement are intentionally deferred to Milestone B or a follow-up change so the first rollout does not mix generation-contract, validation-framework, runner-loop, repair-prompt, and UI/API changes into one blast radius.

Status convention: checked items mean the current branch claims the Milestone A item has been implemented and verified, not merely proposed. A fully checked Milestone A does not mean the broader roadmap is complete: pre-validation normalization, Pwn scaffold ownership, hard solver-quality gates, broad solver-style scanners, and full Pwn evidence-profile enforcement remain deferred follow-up work unless a later OpenSpec change promotes them into acceptance tasks. If this change is reused as a pre-implementation proposal, reset the checkboxes before applying it.

Current implementation audit, 2026-07-05:

- Implemented Milestone A evidence: `src/domain/validation_failure_governance.py` owns normalized class/signature derivation and latest failed validation source precedence; `src/domain/validation_repair_policy.py` owns class-aware route selection; `src/hermes/validation.py` annotates validator and validation-gate failures; `src/hermes/runner.py` writes `validation-history.json`, records route/progress summaries, and stops on repeated invocation-local signatures; `src/services/build_attempt_repair_service.py`, `src/services/build_orchestration_service.py`, and `src/web/build_attempts_endpoints.py` consume the same latest-failed-validation helper for repair/retry/API context.
- Implemented verification evidence: focused tests cover classifier precedence, three-state readiness and `pwn_prompt_eof`, non-validation phase guards, history-over-report fallback, multi-challenge no-guess guard, API list/detail exposure, repair prompt evidence, retry/revalidate context, class-aware repair policy, repeated-signature stops after deterministic and Hermes reruns, sibling/lost-worker isolation, and existing `consecutive_infra` behavior.
- Deferred by design, not incomplete Milestone A work: broad pre-validation normalization, default Pwn xinetd/chroot scaffold overwrite, challenge-specific Dockerfile/scaffold rewriting, hard solver-quality gates, broad solver-style static scanners, and Pwn evidence-profile enforcement. These must become a follow-up OpenSpec change before they receive task checkboxes.
- Verification commands used for this audit: `uv run openspec validate batch-failure-governance --strict`; `uv run pytest tests/app/test_validation_failure_governance.py tests/app/test_validation_repair_policy.py tests/app/test_build_attempts_api.py tests/app/test_build_attempt_repair_prompt.py tests/app/test_build_attempt_revalidation_service.py tests/app/test_runner_resume.py`; `uv run pytest tests/app/test_build_orchestration_service.py tests/app/test_sequential_queue_failfast.py tests/app/test_hermes_validation.py tests/app/test_validate_challenge.py tests/app/test_validation.py tests/app/test_build_attempt_auto_repair_service.py`.

- [x] 0.1 Finish normalized validation failure classification and shared latest-failed-validation derivation, including `validation-history.json` precedence, runner validation-gate structured records, and non-validation runner-phase guards.
- [x] 0.2 Correct context-sensitive `pwn_prompt_eof` semantics: generic EOF without fresh-connection readiness evidence must preserve missing-readiness evidence and must not default to `service-readiness`, including the guard that a bare `readiness_established=false` value is not failed fresh-connection evidence.
- [x] 0.3 Route validation failures by class: service-readiness to readiness/probe/diagnostic evidence repair when evidence supports it, contract to deterministic repair first, solver to Hermes repair with `validate.sh`, `writenup/exp.py`, structured details, tails, and `pwn_debug_report.json` when present, timeout to its bounded route.
- [x] 0.4 Stop auto-repair for the current attempt when the same normalized class/signature repeats after deterministic or Hermes repair validation reruns; keep sibling attempts running and do not suppress manual retry/repair/revalidate across requests.
- [x] 0.5 Expose `validation_failure_class`, normalized signature, and concise diagnostic summary in attempt list/detail; expose the selected route in repair context/progress summaries using the same latest-failed-validation derivation helper while keeping attempt-list derivation bounded to returned rows.
- [x] 0.6 Add focused Milestone A tests for classification precedence, readiness-vs-solver EOF semantics, shared derivation, API/repair context exposure, repeated-signature stop after deterministic and Hermes repair, sibling continuation, and no `consecutive_infra` increment for validation failures.
- [x] 0.7 Keep pre-validation normalization, Pwn scaffold overwrite, hard solver-quality gates, broad solver-style scanners, and full Pwn evidence-profile enforcement out of Milestone A.
- [x] 0.8 Keep Phase 2+ solver-quality and Pwn evidence-profile language as design/deferred context unless a follow-up change promotes it with explicit tests and acceptance criteria.

## Phase 1: Classification, diagnostics, and attempt-local repair governance

## 1. Taxonomy and shared derivation

- [x] 1.1 Finish the validation failure classifier with normalized batch-oriented validation failure classes, three-state Pwn readiness evidence, and an invocation-local stable failure signature.
- [x] 1.2 Define the first-rollout closed class set as `timeout`, `service-readiness`, `contract`, and `solver`; leave prompt-input failures unclassified until prompt capture points and diagnostics are added.
- [x] 1.2a Update classifier and tests so Pwn readiness evidence is modeled as `established`, `failed-fresh-connection`, or `unavailable`; generic `pwn_prompt_eof` with unavailable freshness/readiness evidence records missing readiness evidence and routes as `solver` after required contracts pass, rather than defaulting to `service-readiness`.
- [x] 1.2b Add an explicit guard that a missing readiness field, absent probe, or bare `readiness_established=false` value means not-established/unavailable evidence, not failed fresh-connection evidence, unless an explicit readiness-failure diagnostic or `failed-fresh-connection` observation is present.
- [x] 1.2c Preserve timeout subreasons in signatures, such as solver I/O, service readiness, wrapper bounds, or missing diagnostic capture, while keeping `timeout` as the normalized API class.
- [x] 1.3 Tighten the runner phase / validation status / `validation_failure_details` diagnostic-code mapping so fallback report/progress evidence cannot emit a validation class unless the terminal source phase is validation.
- [x] 1.4 Keep existing detailed validation diagnostic codes as signature inputs and repair context; do not replace them with only the normalized class.
- [x] 1.5 Implement a shared latest-failed-validation derivation helper that reads `work/executions/<attempt_id>/current/state/validation-history.json` first, then falls back to report entries, validation status, contract errors, progress messages, and artifact metadata.
- [x] 1.6 Expose a single attempt-level `validation_failure_class` only for the current one-build-attempt-to-one-challenge flow; return no class or per-challenge data rather than guessing if a multi-challenge attempt is encountered.
- [x] 1.7 Thread the derived normalized validation failure class through runner progress messages and attempt summaries as `validation_failure_class` without adding a new durable source-of-truth table.
- [x] 1.8 Ensure repeated-failure signatures include class plus diagnostic detail such as missing module, detail code, path, traceback frame, prompt marker, validation status, or concise stderr marker so distinct solver failures are not collapsed solely because they share class `solver`.
- [x] 1.9 Normalize volatile values out of failure signatures, including elapsed time, container ids, random ports, execution workspace prefixes, and non-address-specific memory noise.
- [x] 1.10 Preserve `validation_failure_details` in validation report merge output as a compatibility fallback, while keeping `validation-history.json` as the primary structured derivation source.
- [x] 1.11 Ensure runner-owned validation gates that fail before `ChallengeValidator` write a failed validation-history round consumed by the shared derivation helper.

## 2. Attempt-scoped repair behavior

- [x] 2.1 Tighten the policy object for class-specific retry ceilings, route types (`deterministic`, `hermes`, `escalate`), and stop conditions so prompt/menu EOF is `service-readiness` only with `failed-fresh-connection` readiness evidence, `solver` once readiness is `established`, and solver-routed with a missing-readiness diagnostic when readiness evidence is `unavailable`.
- [x] 2.2 Split the current deterministic auto-repair entrypoint behind a class-aware policy router instead of wrapping the existing all-repairs function with ad hoc call-site checks.
- [x] 2.3 Make the policy router choose bounded route types from the normalized validation failure class: deterministic mechanical repair for known safe fixes, Hermes repair for solver/runtime tuning, or no-op/escalation when automatic repair is unsafe.
- [x] 2.4 Keep deterministic repair limited to safe wrapper/diagnostic normalization for solver failures; do not treat arbitrary payload, ROP, offset, leak parsing, or flag extraction tuning as deterministic auto-repair.
- [x] 2.5 Thread latest `validation_failure_details`, stdout/stderr tails, concise `failure_summary`, normalized class, and signature into `BuildAttemptRepairService` direct repair context while preserving `validation_contract_errors` / `contract_errors` compatibility.
- [x] 2.6 Extend retry/repair submission diagnostics so `BuildOrchestrationService` carries `validation_failure_details`, normalized class, and signature from the same latest failed validation result used by direct repair and attempt-detail APIs.
- [x] 2.7 Stop runner automatic repair when the same validation failure class and structured-or-derived signature repeats within the same runner validation/repair invocation without progress, including repeats observed after deterministic repair reruns as well as Hermes repair reruns.
- [x] 2.8 Do not suppress dashboard manual repair, retry, or revalidate across invocations in Phase 1; pass the latest class/signature as context only.
- [x] 2.9 Route solver-class failures to Hermes repair with exp-specific context, including current `writenup/exp.py`, `validate.sh`, `writenup/pwn_debug_report.json` when present, structured failure details, stdout/stderr tails, concise failure summary, class, and signature.
- [x] 2.10 Preserve available validation diagnostics from failed `validate.sh` runs, including service state, recent logs, readiness probe result, exact solver command, solver stdout/stderr tails, exit code, and final flag candidate when present; synthesize explicit unavailable markers when validation scripts omit fields needed for repair.
- [x] 2.11 Add repair-context budgets for solver stdout/stderr, service logs, debug reports, and file context; include explicit truncation markers when caps are hit.
- [x] 2.12 Route timeout failures with stable subreasons through bounded diagnostic, solver-context, or escalation behavior without blindly increasing timeouts or adding a new normalized class.

## 3. Batch isolation and orchestration

- [x] 3.1 Verify build orchestration so one attempt's validation/repair failure cannot block sibling attempts in the same batch.
- [x] 3.2 Preserve per-attempt failure summaries and expose `validation_failure_class` in build-attempt API list/detail responses only for validation-phase failures, deriving from the shared latest-failed-validation helper.
- [x] 3.3 Keep retry and revalidate flows bounded by the latest attempt and its own invocation-local diagnostic history.
- [x] 3.4 Preserve existing sequential consecutive-infrastructure fail-fast behavior.
- [x] 3.5 Ensure validation-phase failures remain `failure_type=validation` and do not increment the sequential `consecutive_infra` streak.
- [x] 3.6 Keep attempt-list class/signature derivation bounded to the returned folded rows, using copied progress/summary fields or bounded per-row history reads rather than global execution-history scans.

## 4. Phase 1 verification

- [x] 4.1 Add classifier mapping tests for timeout, service-readiness, contract, solver, `validation_failure_details` precedence, context-sensitive prompt/menu EOF classification, and non-validation runner phases that should have no normalized validation class.
- [x] 4.2 Add shared derivation tests proving `validation-history.json` is preferred over report/progress/metadata fallbacks and legacy attempts remain readable when history is missing.
- [x] 4.3 Add API response field tests proving failed validation attempts expose `validation_failure_class` from the shared derivation helper and non-validation failures do not include it.
- [x] 4.4 Add a multi-challenge guard test proving an attempt-level class is not guessed when a build attempt contains multiple failed challenge results.
- [x] 4.5 Add repair-policy router tests proving each first-rollout class selects the intended route type and does not route solver failures through deterministic mechanical repair.
- [x] 4.6 Add repair-context tests proving direct repair and retry/repair submission both expose `validation_failure_details`, `validation_contract_errors`, stdout/stderr tails, `failure_summary`, normalized class, and signature to Hermes repair prompts without replacing legacy contract-error fields.
- [x] 4.7 Add report merge/API-derivation tests proving `validation_failure_details` are not lost between validation, attempt-detail API, retry context, and manual repair context.
- [x] 4.8 Add blocking tests proving repeated identical validation failures stop runner auto-repair for one attempt only within a single invocation.
- [x] 4.9 Add tests proving dashboard manual repair, retry, and revalidate are not cross-request suppressed by invocation-local repeated-signature state.
- [x] 4.10 Add tests proving sibling attempts continue independently for validation/repair failures inside the same batch.
- [x] 4.11 Add a no-regression test proving `consecutive_infra` can still abort sequential tail attempts for non-validation infrastructure failures.
- [x] 4.12 Add a no-regression test proving validation failures do not increment the `consecutive_infra` streak.
- [x] 4.13 Add solver repair-context tests proving Hermes repair receives `writenup/exp.py`, `validate.sh`, pwn debug report context, structured failure details, stdout/stderr tails, failure summary, class, and signature.
- [x] 4.14 Add repeated-signature tests proving `solver:missing_dependency:<module>`, `solver:flag_mismatch`, `solver:pwn_prompt_eof:<marker>` after readiness is established, and `service-readiness:pwn_prompt_eof:<marker>` before readiness is established are treated as materially different failures within the same bounded budget.
- [x] 4.14a Add classification tests proving generic `pwn_prompt_eof` without fresh-connection readiness evidence records missing readiness evidence and does not default to `service-readiness`.
- [x] 4.14b Add blocking runner tests proving repeated signatures are checked after deterministic validation repair reruns, not only after Hermes repair rounds.
- [x] 4.14c Add readiness-observation tests proving `readiness_established=false` alone does not classify `pwn_prompt_eof` as `service-readiness`.
- [x] 4.15 Add validation-diagnostic-envelope tests proving failed validation preserves available solver stdout/stderr tails, service logs/readiness evidence, exact solver command, exit code, and structured failure details for the next repair prompt, and marks missing fields unavailable.
- [x] 4.16 Add signature-normalization tests proving volatile fields do not create fake new failures and stable diagnostic markers still distinguish materially different failures.
- [x] 4.17 Add repair-context budget tests proving oversized logs/debug reports are truncated with explicit markers and useful tails/summaries remain present.
- [x] 4.18 Add staged-rollout tests proving legacy attempt details remain readable when new diagnostic/evidence fields are unavailable.
- [x] 4.19 Add route-boundary tests proving diagnostic normalization inside the solver route does not reclassify missing readiness evidence as `service-readiness` unless a later validation result records explicit failed-fresh-connection evidence.
- [x] 4.20 Add Hermes execution protocol tests proving fresh validator failures and runner validation-gate failures preserve structured latest-failed-validation evidence, and older attempts remain readable through fallbacks.
- [x] 4.21 Add timeout-subreason tests proving solver-I/O timeouts can select bounded solver-context repair while still exposing `validation_failure_class=timeout`.
- [x] 4.22 Add attempt-list performance/bounds tests proving class/signature derivation only reads evidence for returned folded rows and does not scan unrelated execution histories.

## Deferred follow-up notes, not Milestone A tasks

These notes intentionally do not use task checkboxes. If the team decides to implement them, create a follow-up OpenSpec change or convert the selected slice into a new milestone after Milestone A is complete.

- Pre-validation normalization and Pwn scaffold safety: decide whether this belongs in a second milestone or a separate OpenSpec change; define the source chain for gate failures; add an attempt-scoped normalization entrypoint; normalize or reject deterministic Web/Pwn validation-safety blockers; promote the default Pwn xinetd/chroot scaffold only with an explicit safe overwrite matrix.
- Solver-quality enforcement and evidence profiles: promote Web/Pwn solver stability diagnostics into hard enforcement only after Phase 1 visibility/tests are in place; add diagnostic codes for insufficient validation evidence and stale Pwn payload assumptions; define simple/intermediate/advanced Pwn evidence profiles; add document-completion gates and enforcement tests in a later change.
