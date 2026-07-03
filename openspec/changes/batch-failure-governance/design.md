## Context

Batch build attempts currently inherit a mostly single-attempt validation repair model: validation failures are classified, but the retry loop does not strongly distinguish timeout from service-readiness problems, and repeated validation failures can keep consuming repair budget without improving the outcome. The batch operator needs throughput, not endless optimism.

## Goals / Non-Goals

**Goals:**
- Make validation failure handling class-aware and attempt-local.
- Stop repeated no-progress validation repair loops from consuming batch capacity.
- Preserve independent validation/repair progress for sibling attempts in the same batch.
- Reuse existing persistence and progress-event infrastructure.

**Non-Goals:**
- No new database tables.
- No wholesale redesign of the build queue model.
- No attempt to make every broken challenge auto-fixable.
- No replacement of the existing runner-phase taxonomy (`hermes_auth`, `hermes_rate_limit`, `hermes_timeout`, `terminal_workspace`, `materialize`, `contract_prepare`, `validation`, etc.).
- No change to the sequential driver's existing consecutive infrastructure fail-fast behavior.
- No hard solver-quality gate in Phase 1. Phase 1 may classify and expose solver-quality diagnostics, but documentation-completion blockers and Pwn evidence-profile enforcement are staged behind visible diagnostics and dedicated tests.

## Decisions

1. Use a normalized validation failure classification layer instead of ad hoc log parsing in each service.
   The first rollout classifies only attempts whose terminal runner phase is `validation`. The closed API/repair-level class set for validation failures is `timeout`, `service-readiness`, `contract`, and `solver`. These slugs are the canonical wire values; lower-level validation statuses and diagnostic codes remain input evidence, not separate members of this set. Prompt-input failures are reserved until prompt rendering has stable capture points and diagnostic fields.

2. Keep runner-phase failures outside this normalized validation class unless an explicit mapping exists.
   Existing runner-phase categories remain authoritative for non-validation failures. API responses should not invent a validation failure class for phases such as `hermes_auth`, `hermes_rate_limit`, `hermes_timeout`, `terminal_workspace`, `materialize`, or `contract_prepare`.

   Classification should use this precedence: timeout status or timeout diagnostics first; readiness-specific `validation_failure_details[].code` next; contract/gate diagnostics next; solver/runtime statuses last. This matters because readiness problems may currently surface as `contract_failed` with readiness-specific detail codes. Menu/prompt EOF evidence is context-sensitive: it is `service-readiness` only when readiness evidence shows the service did not expose a real application prompt on a fresh connection; it is `solver` when readiness is established and the reference solver later loses synchronization.

   | Evidence source | Example status or diagnostic | Normalized validation class |
   | --- | --- | --- |
   | validation status or detail code | `timeout` | `timeout` |
   | validation detail code | `pwn_service_readiness_failed`, `pwn_port_only_readiness`, `pwn_bad_readiness_probe`, readiness-probe `pwn_prompt_eof` | `service-readiness` |
   | validation status, phase, or detail code | `contract_failed`, `missing_validation`, `invalid_metadata`, `phase=contract`, `phase=gate`, missing required file/field/metadata/attachment/validation script/evidence contract | `contract` |
   | validation status or detail code | `nonzero_exit`, `flag_mismatch`, `missing_dependency`, solver-sync `pwn_prompt_eof`, exploit/runtime failure after required contracts and readiness are established | `solver` |
   | runner phase | `hermes_auth`, `hermes_rate_limit`, `hermes_timeout`, `terminal_workspace`, `materialize`, `contract_prepare` | no normalized validation class |
   | prompt rendering | missing `shard.json`, manifest, resume context, or other render input before validation starts | no class in first rollout; future prompt diagnostic work |

3. Keep repair policy attempt-scoped, not batch-scoped.
   A batch may contain many failures, but each attempt gets its own repair budget and exhaustion state. This prevents one pathological challenge from draining unrelated attempts.

4. Persist only stable outcomes, not a new retry-state table.
   The normalized class is derived from the latest validation result and existing diagnostics for the current attempt. Phase 1 SHALL use the latest failed result in `work/executions/<attempt_id>/current/state/validation-history.json` as the primary structured source, because artifact `metadata.json` and the current report merge path do not preserve `validation_failure_details` reliably. If history is unavailable, derivation may fall back to report challenge entries that preserve `validation_failure_details`, then to `validation_status`, `validation_contract_errors`, progress-event terminal messages, and artifact metadata. The shared derivation helper should be used by attempt-detail/list API payloads, retry context, and manual repair context so those paths classify the same failure consistently. It may copy `validation_failure_class` into existing progress-event or attempt-summary payloads for operator visibility, but those copies are not the durable source of truth and no new table or required schema field is introduced.

5. Treat repeated identical failure signatures as an invocation-local stop signal.
   When an attempt fails with the same class and effectively the same signature across repair rounds inside the same active runner validation/repair invocation, the runner should stop automatic repair and hand control back to the operator instead of looping. This stop rule applies to the runner's in-process automatic validation repair loop. Dashboard manual repair, retry, and revalidate calls remain separate operator-triggered invocations; they should receive the latest signature and class for context, but Phase 1 does not suppress them across requests.

6. Add a policy router in front of deterministic repair.
   The existing deterministic repair service applies a bundle of mechanical repairs to a challenge directory. This change should introduce a small class-aware repair policy/router before or inside that service so `timeout`, `service-readiness`, `contract`, and `solver` can select a bounded route without hard-coding class checks at unrelated call sites. A route may run deterministic mechanical repair, invoke Hermes repair with structured diagnostics, or stop/escalate when deterministic repair would be unsafe. Contract and service-readiness classes may use deterministic repairs when the detail code maps to an existing safe mechanic; solver failures should normally go to Hermes repair with file context and diagnostics rather than pretending deterministic auto-repair can tune exploit logic.

7. Keep sibling attempts independent during validation and repair.
   The orchestration path should continue processing other attempts in the batch when one attempt has a validation/repair failure or exhausts validation repair budget. Validation-phase failures must remain `failure_type=validation` and must not increment the sequential infrastructure streak. This does not override the sequential driver's existing consecutive infrastructure fail-fast behavior, which may still abort tail attempts for repeated non-validation infrastructure failures.

8. Populate retry and repair context with structured validation evidence.
   `BuildAttemptRepairService` already renders a `Structured failure details` prompt section, but the current helper can be empty. This change should thread latest `validation_failure_details`, stdout/stderr tails, and the concise `failure_summary` into both retry context and direct/manual repair context while preserving `validation_contract_errors` / `contract_errors` compatibility for existing callers and prompts. Retry diagnostics and manual repair should use the same latest-failed-result source so `/retry`, `/repair`, and attempt-detail API summaries classify the same failure consistently.

9. Treat exp stability as a staged validation-contract concern, not only as a repair-time suggestion.
   Web/Pwn reference solvers should ultimately target the running validation service through `CHAL_HOST` and `CHAL_PORT` in the default path. They should not hardcode `127.0.0.1`, `localhost`, container names, or fixed challenge ports except in explicit local debug paths such as `LOCAL=1`. In Phase 1 this is diagnostic-first: violations are recorded as validation diagnostics and repair context rather than new document-completion blockers. Pwn solvers should use bounded reads and short pwntools timeouts for prompt synchronization, leaks, and shell/flag reads so a bad menu sync becomes a classified validation failure instead of a worker hang. Dependency problems such as `ModuleNotFoundError`, undeclared helper modules, or missing vendored code should remain `solver` failures with a dependency-oriented signature and repair hint.

10. Make solver repair evidence-rich before spending Hermes budget.
    Solver-class repair should include the latest `validate.sh`, `writenup/exp.py`, relevant `writenup/pwn_debug_report.json` when present, `validation_failure_details`, stdout/stderr tails, and concise failure summary in the repair prompt. The repair route should tell Hermes whether the failure looks like dependency, synchronization, wrong flag, offset/payload, leak parsing, or remote/local mismatch. Deterministic repair may normalize wrappers or add missing diagnostic plumbing, but it should not claim to tune arbitrary exploit payload logic.

11. Use signatures fine-grained enough to avoid false stop conditions.
    The repeated-failure stop rule should compare normalized class plus a compact diagnostic signature such as `solver:missing_dependency:pwn`, `solver:pwn_prompt_eof:recvuntil Choice`, `solver:flag_mismatch`, or `service-readiness:pwn_bad_readiness_probe`. A second solver failure with a materially different detail code, traceback frame, missing module, or flag mismatch evidence should be eligible for its own bounded repair round instead of being suppressed as "same class again."

12. Stage reference solver quality gates after diagnostic visibility.
    The generation flow should not ultimately treat a freshly written `writenup/exp.py` as acceptable merely because the file exists, but Phase 1 should not introduce new document-completion blockers. Phase 1 records Web/Pwn solver-quality gaps as validation diagnostics and repair context. Phase 2 may hard-block deterministic stability failures such as default-path hardcoded service targets, missing imported helper modules, and unbounded solver I/O. Phase 3 may require Pwn evidence profiles for new generation paths after profile-specific tests are green.

13. Require Pwn payload assumptions to be evidence-backed.
    Pwn solver generation and repair should distinguish payload-quality bugs from service-readiness bugs. Offsets should come from cyclic/core/headless gdb or a clearly documented source; libc and ld assumptions should come from shipped attachments or container/chroot evidence; ROP gadgets should be discovered from the actual ELF/libc rather than handwritten guesses; menu synchronization should be verified against the same prompt path used by validation. Missing or stale evidence should produce a contract/solver diagnostic that asks for evidence-backed recalculation instead of another blind payload edit.

14. Make solver dependencies explicit and reproducible.
    The default validation environment should not depend on undeclared Python packages or ungenerated helper modules. Phase 1 can allow known runtime-provided tools such as pwntools/requests where the environment already supports them, and should record missing non-standard helper modules as solver dependency diagnostics with the missing module name, import location when available, and a repair hint to vendor the helper, switch to the standard library, or declare the supported runtime dependency. Later enforcement phases may require any non-standard helper module imported by `writenup/exp.py` to be present under `writenup/` or otherwise declared by the challenge.

15. Define a minimum validation diagnostic envelope.
    `validate.sh` failures should emit enough bounded evidence for Hermes to repair without guessing: compose/service state, recent container logs, readiness probe result, exact solver command, solver stdout/stderr tails, exit code, and final stdout flag candidate when present. All diagnostics emitted from traps should go to stderr so stdout remains reserved for the recovered flag. If the validation framework captures fewer fields than this envelope, the repair context should synthesize missing fields explicitly as unavailable rather than silently omitting the section.

16. Use graduated enforcement to avoid false rejects.
    The quality gate should distinguish hard blockers from advisory evidence gaps. Hard blockers are deterministic stability failures such as default-path hardcoded service targets, missing imported helper modules, unbounded solver I/O that can hang validation, hardcoded flags, forbidden organizer-file reads, and absent validation diagnostics after a validation failure. Advisory gaps include incomplete rich debug notes for otherwise simple or already-passing exploits. Phase 1 should expose these as diagnostics and repair hints only; later phases can hard-fail deterministic blockers and then require richer Pwn evidence according to exploit complexity.

17. Introduce solver evidence profiles by exploit complexity.
    Pwn evidence should be tiered:
    - `simple`: ret2text/ret2win/no-libc-leak/no-PIE payloads need binary path, mitigation summary, offset source or direct source-derived offset, menu token, and local or container smoke result.
    - `intermediate`: canary, PIE, GOT leak, or ret2libc tasks additionally need leak parsing evidence, base calculations, gadget source, and libc/ld source.
    - `advanced`: multi-stage ROP, heap, custom protocol, or unstable timing tasks need full `pwn_debug_report.json` with local and remote/container observations.
    This keeps the gate useful without forcing heavyweight reports onto intentionally easy challenges.

18. Cap repair context size while preserving the useful signal.
    Solver repair prompts should include bounded tails and structured summaries, not entire logs by default. Suggested defaults are latest `writenup/exp.py`, latest `validate.sh`, `pwn_debug_report.json` when present, the top structured failure details, solver stdout/stderr tails capped by lines and bytes, recent service logs capped by lines and bytes, and file hashes or mtimes for omitted large artifacts. If the cap is hit, the prompt should say what was truncated so Hermes does not assume evidence is complete.

19. Normalize signatures before comparison.
    Failure signatures should strip volatile fields such as elapsed time, container IDs, random ports, memory addresses unless the diagnostic class is address-specific, and absolute execution workspace prefixes. They should retain stable detail code, path, missing module, traceback frame/function, prompt marker, validation status, and concise stderr marker. This reduces false "same failure" stops while still preventing loops on identical failures.

20. Roll out in phases.
    Phase 1 should add classification, signature derivation, diagnostic preservation, API exposure, repair context, class-aware deterministic repair routing, and runner invocation-local repeated-signature stops without changing solver generation strictness. Phase 2 should enable hard blockers for exp stability and diagnostics. Phase 3 should enforce Pwn evidence profiles for new generation paths. This staged rollout lowers risk for existing artifacts while still improving new batch throughput.

## Risks / Trade-offs

- [Risk] Early stop rules may leave some solvable attempts unrepaired. -> Mitigation: keep the policy class-specific and conservative for first rollout.
- [Risk] Failure signatures may be noisy. -> Mitigation: compare both normalized class and a trimmed invocation-local signature derived from the latest diagnostic text.
- [Risk] Operators may want more visibility into why a retry stopped. -> Mitigation: keep the class and summary in the existing failure summary fields and progress events.
- [Risk] Changing repair flow could regress valid retries. -> Mitigation: add coverage for the first-rollout validation classes, repair routing, structured repair context, API summaries, repeated-same-signature cases, and existing infra fail-fast behavior before rollout.
- [Risk] Exp stability contract checks may reject locally convenient debug scripts. -> Mitigation: allow explicit local modes such as `LOCAL=1`, while keeping the default validation path bound to `CHAL_HOST`/`CHAL_PORT` and bounded solver I/O.
- [Risk] Requiring Pwn debug evidence may slow generation. -> Mitigation: make rich `pwn_debug_report.json` required for non-trivial exploits and allow concise evidence for simple ret2text/ret2win cases, while still requiring bounded smoke or clear skip reasons.
- [Risk] Context-heavy repair prompts may crowd out the actual fix. -> Mitigation: cap logs by line/byte count, include structured summaries first, and mark truncation explicitly.
- [Risk] Repeated-failure signatures may stop too early or too late. -> Mitigation: normalize volatile values out of signatures while retaining stable diagnostic markers such as detail code, path, missing module, traceback frame, and prompt marker.
- [Risk] Enforcing all gates at once could disrupt existing batches. -> Mitigation: ship classification and diagnostics first, then hard blockers, then complexity-tiered Pwn evidence for new generation.

## Migration Plan

1. Add the normalized validation failure classification and policy routing.
2. Thread the class through validation, repair, and API summaries.
3. Add bounded invocation-local stop conditions for repeated identical validation failures.
4. Verify validation/repair batch isolation so sibling attempts keep progressing.
5. Enable hard exp-stability blockers only after diagnostic capture is visible in attempt detail and repair prompts.
6. Roll out Pwn evidence profiles for new generation paths after simple/intermediate/advanced tests are green.
7. Roll out behind existing batch submission paths; no schema migration is required.
8. Treat one build attempt to one challenge as a Phase 1 precondition for exposing a single attempt-level `validation_failure_class`. If multi-challenge shards return, expose per-challenge classes or add an explicit aggregation rule before emitting a single attempt-level field.

## Open Questions

- Should the system eventually persist failure signatures for historical reporting and cross-request repair suppression?
- Should the UI expose a batch-level failure histogram, or is per-attempt detail enough for the first release?
- Should prompt rendering errors become a fifth normalized class after prompt capture points and diagnostics are defined?
