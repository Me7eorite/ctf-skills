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
- No hard solver-quality gate in Phase 1. Phase 1 may preserve and expose solver-quality diagnostics already visible in validation evidence, but broad static solver-quality scanning, documentation-completion blockers, default solver-target enforcement, dependency enforcement, unbounded-I/O enforcement, and Pwn evidence-profile enforcement are staged behind visible diagnostics and dedicated tests.

## Recent Batch Evidence

The 2026-07-04 remote queue on `192.168.6.233:/root/ctf-skills` provides the concrete failure surface this change should optimize for. The latest full queue (`iter-009`) processed 8 Pwn build attempts across four dashboard lanes and all 8 failed in `hermes_phase=validation`. The lane summaries showed no Hermes auth/rate-limit/queue-dispatch bottleneck. A broader recent sample of 80 reports contained 24 passes and 56 `nonzero_exit` failures; 49 of the failures were solver-class and 7 were service-readiness. The repeated evidence classes were:

- Service readiness: containers were `Up` and xinetd logged `...done`, but application-level banner/menu probes returned nothing or timed out.
- Solver runtime: `writenup/exp.py` connected to the service but did not recover a flag, exited non-zero, lost prompt synchronization, or relied on brittle payload constants.
- Deterministic contract/layout defects: nested `output/challenges` trees, `docker-compose.yml.yml` path construction, missing scaffold files, missing or weak diagnostics, and Compose commands without an isolated project.
- Repair-loop waste: reports often claimed validation was prepared or locally passing, but host validation still failed and the shard remained in `failed/`.

The design should therefore not spend its first implementation round on historical analytics, broad UI reporting, or full artifact normalization. It should first make validation evidence trustworthy across API, repair, and runner paths, then stop identical no-progress repair loops. Deterministic pre-validation normalization remains valuable, but should land after the failure-governance source chain is stable.

## One-Round Solution

The first implementation slice is intentionally narrow enough to ship in one pass:

1. Finish the normalized failure taxonomy and shared derivation helper so attempt-detail APIs, retry, repair, and runner summaries all classify the same latest failed validation evidence.
2. Fix context-sensitive Pwn EOF classification with an explicit three-state readiness observation: `established`, `failed-fresh-connection`, or `unavailable`. `pwn_prompt_eof` is `service-readiness` only when a fresh readiness probe explicitly failed to observe an application prompt/menu before solver payloads; it is `solver` when readiness is established; generic EOF without freshness/readiness evidence records missing-readiness evidence and should prefer the solver route after required contracts pass.
3. Preserve validation diagnostics and repair context: `validation_failure_details`, stdout/stderr tails, failure summary, class, signature, and legacy contract-error compatibility.
4. Route validation failures by class: service-readiness goes to readiness/probe/diagnostic evidence repair when evidence supports it, contract goes to deterministic repair first, solver goes to Hermes repair with `validate.sh`, `writenup/exp.py`, structured details, tails, and debug evidence, and timeout uses its own bounded route.
5. Compare normalized failure signatures after every deterministic and Hermes repair validation rerun. If the same class/signature repeats inside the invocation, stop auto-repair for that attempt and continue sibling attempts.
6. Preserve sibling independence and existing non-validation infrastructure fail-fast behavior.

This shifts the system from "prompt harder, retry more" to "classify the actual validation failure, pass the right evidence to the right route, and stop when the same attempt is not making progress."

## Spec Ownership Boundary

The delta specs intentionally split ownership:

- `batch-failure-governance` owns the normalized validation failure class set, readiness interpretation, latest-failed-validation derivation, signatures, diagnostic preservation, and attempt-local stop rule.
- `build-orchestration` owns how orchestration consumes that derived class/signature for repair routing, progress/API visibility, and sibling-attempt isolation.

If both specs mention the same behavior, `batch-failure-governance` is the source of truth for what a class or signature means, while `build-orchestration` is the source of truth for when orchestration calls a route or continues a sibling attempt. Future solver-quality gates, scaffold overwrite, and Pwn evidence profiles should stay in design notes or a follow-up change until they become current acceptance criteria.

## Decisions

1. Use a normalized validation failure classification layer instead of ad hoc log parsing in each service.
   The first rollout classifies only attempts whose terminal runner phase is `validation`. The closed API/repair-level class set for validation failures is `timeout`, `service-readiness`, `contract`, and `solver`. These slugs are the canonical wire values; lower-level validation statuses and diagnostic codes remain input evidence, not separate members of this set. Prompt-input failures are reserved until prompt rendering has stable capture points and diagnostic fields.

2. Keep runner-phase failures outside this normalized validation class unless an explicit mapping exists.
   Existing runner-phase categories remain authoritative for non-validation failures. API responses should not invent a validation failure class for phases such as `hermes_auth`, `hermes_rate_limit`, `hermes_timeout`, `terminal_workspace`, `materialize`, or `contract_prepare`.

   Classification should use this precedence: timeout status or timeout diagnostics first; explicit readiness-failure diagnostics next; contract/gate diagnostics next; solver/runtime statuses last. This matters because readiness problems may currently surface as `contract_failed` with readiness-specific detail codes. Menu/prompt EOF evidence is context-sensitive and must not be modeled as a boolean. The classifier needs a three-state readiness observation:

   - `established`: a fresh readiness probe observed an application banner, menu, prompt, or protocol token before solver payloads.
   - `failed-fresh-connection`: a fresh readiness probe connected but did not observe a real application prompt/menu, or proved only a port/xinetd/container state without application-level readiness.
   - `unavailable`: no fresh readiness observation is present in the latest failed validation result.

   `pwn_prompt_eof` is `service-readiness` only for `failed-fresh-connection`, `solver` for `established`, and solver-routed with a missing-readiness-evidence diagnostic for `unavailable` after required contracts have passed. The implementation should avoid treating "not established" as equivalent to "explicitly failed". A bare boolean such as `readiness_established=false` is only evidence that readiness was not proven; it is not by itself a failed fresh-connection observation unless paired with an explicit readiness-observation status or readiness-failure diagnostic code.

   | Evidence source | Example status or diagnostic | Normalized validation class |
   | --- | --- | --- |
   | validation status or detail code | `timeout` | `timeout` |
   | validation detail code | `pwn_service_readiness_failed`, `pwn_port_only_readiness`, `pwn_bad_readiness_probe`, readiness-probe `pwn_prompt_eof` with explicit failed fresh-connection evidence | `service-readiness` |
   | validation status, phase, or detail code | `contract_failed`, `missing_validation`, `invalid_metadata`, `phase=contract`, `phase=gate`, missing required file/field/metadata/attachment/validation script/evidence contract | `contract` |
   | validation status or detail code | `nonzero_exit`, `flag_mismatch`, `missing_dependency`, solver-sync `pwn_prompt_eof`, exploit/runtime failure after required contracts and readiness are established | `solver` |
   | runner phase | `hermes_auth`, `hermes_rate_limit`, `hermes_timeout`, `terminal_workspace`, `materialize`, `contract_prepare` | no normalized validation class |
   | prompt rendering | missing `shard.json`, manifest, resume context, or other render input before validation starts | no class in first rollout; future prompt diagnostic work |

3. Keep repair policy attempt-scoped, not batch-scoped.
   A batch may contain many failures, but each attempt gets its own repair budget and exhaustion state. This prevents one pathological challenge from draining unrelated attempts.

4. Persist only stable outcomes, not a new retry-state table.
   The normalized class is derived from the latest validation result and existing diagnostics for the current attempt. Phase 1 SHALL use the latest failed result in `work/executions/<attempt_id>/current/state/validation-history.json` as the primary structured source, because artifact `metadata.json` and the current report merge path do not preserve `validation_failure_details` reliably. If history is unavailable, derivation may fall back to report challenge entries that preserve `validation_failure_details`, then to `validation_status`, `validation_contract_errors`, progress-event terminal messages, and artifact metadata. The shared derivation helper should be used by attempt-detail/list API payloads, retry context, and manual repair context so those paths classify the same failure consistently. It may copy `validation_failure_class` into existing progress-event or attempt-summary payloads for operator visibility, but those copies are not the durable source of truth and no new table or required schema field is introduced.

   Runner-owned validation gates that are already part of the mandatory validation phase are in scope for Phase 1. For example, if the runner rejects an attempt because design, implement, build, document, `validate.sh`, or `writenup/exp.py` prerequisites are incomplete, that gate should append a failed validation-history round before API, repair context, or repeated-signature logic runs. This closes the gap between the existing runner behavior that writes `validate/failed` without calling `ChallengeValidator` and the new derivation helper. Broader follow-up pre-validation normalization gates outside the current validation phase must not bypass this source chain; if they fail before `ChallengeValidator.validate_one`, they should use the same synthetic-history pattern before implementation promotes them.

   Attempt-detail, retry, revalidate, and manual repair paths may read the complete latest failed validation record. Attempt-list payloads should stay bounded: they may use the copied class/signature/summary already present in progress snapshots or attempt summaries, or perform a bounded read for only the returned folded rows. They must not scan arbitrary execution-history files outside the returned attempt set or make the list query proportional to the global execution population.

5. Treat repeated identical failure signatures as an invocation-local stop signal.
   When an attempt fails with the same class and effectively the same signature across repair rounds inside the same active runner validation/repair invocation, the runner should stop automatic repair and hand control back to the operator instead of looping. This stop rule applies after every validation rerun caused by deterministic repair and after every Hermes repair round, not only after AI repair. Dashboard manual repair, retry, and revalidate calls remain separate operator-triggered invocations; they should receive the latest signature and class for context, but Phase 1 does not suppress them across requests.

6. Add a policy router in front of deterministic repair.
   The existing deterministic repair service applies a bundle of mechanical repairs to a challenge directory. This change should introduce a small class-aware repair policy/router before or inside that service so `timeout`, `service-readiness`, `contract`, and `solver` can select a bounded route without hard-coding class checks at unrelated call sites. A route may run deterministic mechanical repair, invoke Hermes repair with structured diagnostics, or stop/escalate when deterministic repair would be unsafe. Contract and service-readiness classes may use deterministic repairs when the detail code maps to an existing safe mechanic; solver failures should normally go to Hermes repair with file context and diagnostics rather than pretending deterministic auto-repair can tune exploit logic. Timeout routes should preserve the timeout evidence, apply at most safe wrapper/diagnostic normalization when the missing bound is obvious, and otherwise stop/escalate instead of blindly increasing timeouts or looping Hermes repair.

   A diagnostic-normalization step selected inside the solver route does not reclassify the failure as `service-readiness` or `contract`. For example, generic `pwn_prompt_eof` with unavailable readiness evidence remains solver-routed after required contracts pass; the solver route may first improve readiness/log/stdout/stderr capture before asking Hermes to tune payload logic.

   Timeout remains a top-level normalized class, but the timeout route should preserve a compact subreason in the signature when diagnostics point to solver I/O, service readiness, wrapper bounds, or missing diagnostics. For example, `timeout:solver_io:recvuntil menu` can stay API-classified as `timeout` while selecting a bounded solver-context route after diagnostic normalization, whereas `timeout:wrapper_no_bound` may only normalize wrapper limits. This prevents unbounded solver reads from being stranded in a generic no-op timeout path while preserving the closed API class set.

7. Keep sibling attempts independent during validation and repair.
   The orchestration path should continue processing other attempts in the batch when one attempt has a validation/repair failure or exhausts validation repair budget. Validation-phase failures must remain `failure_type=validation` and must not increment the sequential infrastructure streak. This does not override the sequential driver's existing consecutive infrastructure fail-fast behavior, which may still abort tail attempts for repeated non-validation infrastructure failures.

8. Populate retry and repair context with structured validation evidence.
   `BuildAttemptRepairService` already renders a `Structured failure details` prompt section, but the current helper can be empty. This change should thread latest `validation_failure_details`, stdout/stderr tails, and the concise `failure_summary` into both retry context and direct/manual repair context while preserving `validation_contract_errors` / `contract_errors` compatibility for existing callers and prompts. Retry diagnostics and manual repair should use the same latest-failed-result source so `/retry`, `/repair`, and attempt-detail API summaries classify the same failure consistently.

9. Treat exp stability as a staged validation-contract concern, not only as a repair-time suggestion.
   Web/Pwn reference solvers should ultimately target the running validation service through `CHAL_HOST` and `CHAL_PORT` in the default path. They should not hardcode `127.0.0.1`, `localhost`, container names, or fixed challenge ports except in explicit local debug paths such as `LOCAL=1`. In Phase 1 this is diagnostic-first: when violations are already visible in validation output, structured details, or repair context, they are preserved and routed rather than promoted to document-completion blockers or generation-quality gates. Pwn solvers should use bounded reads and short pwntools timeouts for prompt synchronization, leaks, and shell/flag reads so a bad menu sync becomes a classified validation failure instead of a worker hang; Phase 1 may route such failures through bounded timeout handling, but it should not reject otherwise inspectable artifacts solely for solver-style violations before enforcement tests exist. Dependency problems such as `ModuleNotFoundError`, undeclared helper modules, or missing vendored code should remain `solver` failures with a dependency-oriented signature and repair hint.

10. Make solver repair evidence-rich before spending Hermes budget.
    Solver-class repair should include the latest `validate.sh`, `writenup/exp.py`, relevant `writenup/pwn_debug_report.json` when present, `validation_failure_details`, stdout/stderr tails, and concise failure summary in the repair prompt. The repair route should tell Hermes whether the failure looks like dependency, synchronization, wrong flag, offset/payload, leak parsing, or remote/local mismatch. Deterministic repair may normalize wrappers or add missing diagnostic plumbing, but it should not claim to tune arbitrary exploit payload logic.

    Prompt-input/render failures remain outside this route unless the terminal runner phase is validation and the latest validation result supplies stable validation evidence. Call sites that derive retry, repair, or API summaries must pass or preserve the terminal runner phase when the source failure is not validation, so fallback report/progress text cannot accidentally become a solver-class validation failure.

**Deferred Scaffold Normalization Boundary**

This boundary is not a Phase 1 requirement. The system-owned Pwn xinetd/chroot scaffold should be normalized only when the attempt declares or clearly implies the default scaffold model and the challenge-specific fields can be preserved mechanically. Follow-up work should use this safe overwrite matrix:

| Area | Safe action | Unsafe without proof |
| --- | --- | --- |
| `deploy/Dockerfile` | Add a missing canonical file or update a generated-template file with a known marker/hash while preserving required packages and copied challenge files. | Overwrite custom build steps, package installs, users, chroot layout, or copied paths. |
| `deploy/docker-compose.yml` | Add missing project isolation, service name, and port wiring for a single unambiguous default service. | Replace multiple services, custom networks/volumes, custom healthchecks, or ambiguous port mappings. |
| `deploy/_files/start.sh` | Add a missing canonical launcher or update a generated-template launcher with a known marker/hash. | Rewrite custom startup logic, flag placement, environment setup, permissions, or pre-run initialization. |
| xinetd service file | Add a missing default service file or update a generated-template file while preserving binary path, server args, user, port, env, and flag path. | Rewrite server args, privilege model, chroot path, or protocol wrapper without a marker/hash proving template drift. |
| challenge binary/source/metadata/attachments/solver | Read for validation and context only. | Overwrite or relocate challenge-specific assets. |

If any area lacks a marker/hash or has ambiguous custom semantics, the follow-up gate should fail with structured contract or service-readiness diagnostics instead of overwriting custom logic.

11. Use signatures fine-grained enough to avoid false stop conditions.
    The repeated-failure stop rule should compare normalized class plus a compact diagnostic signature such as `solver:missing_dependency:pwn`, `solver:pwn_prompt_eof:recvuntil Choice`, `solver:flag_mismatch`, or `service-readiness:pwn_bad_readiness_probe`. A second solver failure with a materially different detail code, traceback frame, missing module, or flag mismatch evidence should be eligible for its own bounded repair round instead of being suppressed as "same class again."

12. Stage reference solver quality gates after diagnostic visibility.
    The generation flow should not ultimately treat a freshly written `writenup/exp.py` as acceptable merely because the file exists, but Phase 1 should not introduce new document-completion blockers, broad static solver-quality scanners, or solver-quality hard gates. Phase 1 preserves Web/Pwn solver-quality gaps when they are already present in validation diagnostics and repair context, and ensures any repair attempt is bounded. Phase 2 may hard-block deterministic solver-stability failures such as default-path hardcoded service targets, missing imported helper modules, and unbounded solver I/O after the diagnostic surface and enforcement tests are in place. Phase 3 may require Pwn evidence profiles for new generation paths after profile-specific tests are green.

13. Require Pwn payload assumptions to be evidence-backed.
    Pwn solver generation and repair should distinguish payload-quality bugs from service-readiness bugs. Offsets should come from cyclic/core/headless gdb or a clearly documented source; libc and ld assumptions should come from shipped attachments or container/chroot evidence; ROP gadgets should be discovered from the actual ELF/libc rather than handwritten guesses; menu synchronization should be verified against the same prompt path used by validation. In Phase 1, the system preserves evidence and diagnostics that are already visible in validation or debug output; later enforcement can require complete evidence before accepting new artifacts.

14. Make solver dependencies explicit and reproducible.
    The default validation environment should not depend on undeclared Python packages or ungenerated helper modules. Phase 1 can allow known runtime-provided tools such as pwntools/requests where the environment already supports them, and should record missing non-standard helper modules as solver dependency diagnostics with the missing module name, import location when available, and a repair hint to vendor the helper, switch to the standard library, or declare the supported runtime dependency. Later enforcement phases may require any non-standard helper module imported by `writenup/exp.py` to be present under `writenup/` or otherwise declared by the challenge.

15. Define a minimum validation diagnostic envelope.
    `validate.sh` failures should preserve enough bounded evidence for Hermes to repair without guessing: compose/service state, recent container logs, readiness probe result, exact solver command, solver stdout/stderr tails, exit code, and final stdout flag candidate when present. Phase 1 should not require rewriting every existing wrapper to emit every field; it should carry available evidence consistently, keep diagnostics off stdout when wrappers already separate streams, and synthesize missing fields explicitly as unavailable rather than silently omitting the section.

16. Use graduated enforcement to avoid false rejects.
    The quality gate should distinguish existing validation hard failures from new pre-validation structural gates and later solver-quality blockers. Phase 1 may still classify failures already produced by the current validator, such as missing metadata or missing validation entrypoints, but it should not add a broad new pre-validation structural gate in the first milestone. Solver-quality blockers such as default-path hardcoded service targets, missing imported helper modules, unbounded solver I/O, hardcoded flags, forbidden organizer-file reads, and absent rich exploit evidence remain diagnostics and repair hints in Phase 1; later phases can hard-fail deterministic structural or solver blockers and then require richer Pwn evidence according to exploit complexity.

17. Defer solver evidence profiles by exploit complexity.
    Pwn evidence should be tiered:
    - `simple`: ret2text/ret2win/no-libc-leak/no-PIE payloads need binary path, mitigation summary, offset source or direct source-derived offset, menu token, and local or container smoke result.
    - `intermediate`: canary, PIE, GOT leak, or ret2libc tasks additionally need leak parsing evidence, base calculations, gadget source, and libc/ld source.
    - `advanced`: multi-stage ROP, heap, custom protocol, or unstable timing tasks need full `pwn_debug_report.json` with local and remote/container observations.
    This keeps the future gate useful without forcing heavyweight reports onto intentionally easy challenges. These profiles are design notes for later enforcement and are not Milestone A acceptance criteria.

18. Cap repair context size while preserving the useful signal.
    Solver repair prompts should include bounded tails and structured summaries, not entire logs by default. Suggested defaults are latest `writenup/exp.py`, latest `validate.sh`, `pwn_debug_report.json` when present, the top structured failure details, solver stdout/stderr tails capped by lines and bytes, recent service logs capped by lines and bytes, and file hashes or mtimes for omitted large artifacts. If the cap is hit, the prompt should say what was truncated so Hermes does not assume evidence is complete.

19. Normalize signatures before comparison.
    Failure signatures should strip volatile fields such as elapsed time, container IDs, random ports, memory addresses unless the diagnostic class is address-specific, and absolute execution workspace prefixes. They should retain stable detail code, path, missing module, traceback frame/function, prompt marker, validation status, and concise stderr marker. This reduces false "same failure" stops while still preventing loops on identical failures.

20. Roll out in phases.
    Phase 1 should add classification, signature derivation, diagnostic preservation, API exposure, repair context, class-aware deterministic repair routing, runner invocation-local repeated-signature stops, and the corrected `pwn_prompt_eof` semantics without changing solver generation strictness or adding pre-validation normalization. Phase 2 or a follow-up change should add pre-validation artifact normalization and scaffold safety with the synthetic-history source chain defined above. Phase 3 should enable hard blockers for exp stability and diagnostic-envelope quality after those diagnostics are visible and tested. Phase 4 should enforce Pwn evidence profiles for new generation paths. This staged rollout lowers risk for existing artifacts while still improving new batch throughput.

## Risks / Trade-offs

- [Risk] Early stop rules may leave some solvable attempts unrepaired. -> Mitigation: keep the policy class-specific and conservative for first rollout.
- [Risk] Failure signatures may be noisy. -> Mitigation: compare both normalized class and a trimmed invocation-local signature derived from the latest diagnostic text.
- [Risk] Operators may want more visibility into why a retry stopped. -> Mitigation: keep the class and summary in the existing failure summary fields and progress events.
- [Risk] Changing repair flow could regress valid retries. -> Mitigation: add coverage for the first-rollout validation classes, repair routing, structured repair context, API summaries, repeated-same-signature cases, and existing infra fail-fast behavior before rollout.
- [Risk] Exp stability contract checks may reject locally convenient debug scripts. -> Mitigation: allow explicit local modes such as `LOCAL=1`, while keeping the default validation path bound to `CHAL_HOST`/`CHAL_PORT` and bounded solver I/O.
- [Risk] Requiring Pwn debug evidence may slow generation. -> Mitigation: keep rich evidence diagnostic-first in Phase 1; in later enforcement phases, require `pwn_debug_report.json` for non-trivial exploits while allowing concise evidence for simple ret2text/ret2win cases and bounded smoke or clear skip reasons.
- [Risk] Context-heavy repair prompts may crowd out the actual fix. -> Mitigation: cap logs by line/byte count, include structured summaries first, and mark truncation explicitly.
- [Risk] Repeated-failure signatures may stop too early or too late. -> Mitigation: normalize volatile values out of signatures while retaining stable diagnostic markers such as detail code, path, missing module, traceback frame, and prompt marker.
- [Risk] Enforcing all gates at once could disrupt existing batches. -> Mitigation: keep Phase 1 to failure-governance and diagnostic source-of-truth work, then add pre-validation normalization, solver blockers, and complexity-tiered Pwn evidence after tests are green.

## Migration Plan

1. Add the normalized validation failure classification and policy routing.
2. Correct generic `pwn_prompt_eof` classification so missing readiness evidence does not default to `service-readiness`.
3. Thread the class through validation, repair, and API summaries.
4. Add bounded invocation-local stop conditions for repeated identical validation failures, including checks after deterministic validation repair reruns.
5. Verify validation/repair batch isolation so sibling attempts keep progressing.
6. Roll out behind existing batch submission paths; no schema migration is required.
7. Treat one build attempt to one challenge as a Phase 1 precondition for exposing a single attempt-level `validation_failure_class`. If multi-challenge shards return, expose per-challenge classes or add an explicit aggregation rule before emitting a single attempt-level field.
8. Add pre-validation normalization only after gate failures are wired into the same synthetic validation-history source chain.
9. Enable hard exp-stability blockers only after diagnostic capture is visible in attempt detail and repair prompts and enforcement tests cover the promoted blockers.
10. Roll out Pwn evidence profiles for new generation paths after simple/intermediate/advanced tests are green.

## Open Questions

- Should the system eventually persist failure signatures for historical reporting and cross-request repair suppression?
- Should the UI expose a batch-level failure histogram, or is per-attempt detail enough for the first release?
- Should prompt rendering errors become a fifth normalized class after prompt capture points and diagnostics are defined?
