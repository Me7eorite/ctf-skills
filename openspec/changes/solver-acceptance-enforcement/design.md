## Context

`batch-failure-governance` makes validation failures class-aware and stops blind repeated repair loops, but it deliberately does not make reference solver quality a hard release gate. The remaining operator pain is sharper: Web/Pwn artifacts can carry a weak `writenup/exp.py`, validation may fail in ways that are correctly classified as `solver`, and repair can still fail to converge. The system needs to guarantee the release boundary: a challenge is not built unless the reference solver passes against the host validation service path.

Current validation already runs `validate.sh`, records stdout/stderr tails, stores validation history, and routes solver failures to Hermes repair with context. This change builds on that path. It should not reclassify failure governance or replace the runner; it should add hard solver acceptance, repair-progress checks, and safe escalation/regeneration decisions.

## Current Implementation Fit

The implementation already has several enforcement boundaries that this change must extend instead of replacing:

- `src/domain/validation.py::ChallengeValidator.validate_one()` is the authoritative per-challenge validation entrypoint. Solver acceptance fields should be added to its result dictionaries and to `failure_details`/`validation_failure_details`; `contract_errors` must remain as a compatibility surface for existing tests, reports, and callers.
- `ChallengeValidator.contract_errors()` and `_solver_integrity_errors()` already perform deterministic solver/validator anti-cheat checks, including hardcoded flag and organizer-file leakage guards. The new Web/Pwn static preflight should fill the remaining default-target, helper, and bounded-I/O gaps beside those checks, while preserving older RE solver-integrity behavior.
- `src/domain/pwn_debug.py` plus `src/domain/validation_failure_governance.py` already classify Pwn failures by stage and record classification conflicts such as readiness established but classified as service-readiness. This change should add tests and display coverage for those boundaries, not replace the classifier.
- `src/hermes/runner.py` already appends `state/validation-history.json`, records `state/first-validation-failure.json`, compares validation failure fingerprints, and blocks publication when the output manifest changes after validation. Solver acceptance must enrich those existing records and final-publish checks rather than adding an independent success marker.
- `src/domain/validation_repair_policy.py::validation_failure_fingerprints()` is the existing repeated-failure guard. Solver acceptance fingerprints should either extend this function or feed it a solver-specific sub-fingerprint so repeated solver failures are detected consistently with Phase 1 governance.
- `src/services/build_orchestration_service.py` already passes retry context with first/latest failure diagnostics for same-attempt retries. This change should preserve that binding and add solver acceptance/root-current blocked evidence to the existing retry context where available.
- Same-attempt repair and revalidation are already attempt-scoped through `BuildAttemptRepairService` and `BuildAttemptRevalidationService`, with challenge roots normalized under the current attempt workspace. This change must not scan unrelated `work/executions/*` trees to infer solver acceptance.
- `src/web/build_attempts_endpoints.py` already derives attempt-detail validation fields from latest validation history. Solver acceptance, root failure, current blocker, blocked reason, and route fields should be derived for the current returned rows/details only, not by global workspace scans.
- Pwn host-build evidence refresh already exists through `ensure_pwn_solver_evidence()`. This change treats `metadata.artifact` as the authoritative final player attachment, with `attachments/vuln` retained only as a compatibility fallback when metadata is missing or invalid. Evidence refresh, report generation, and exploit SHA stamping must not special-case any one challenge binary name.
- Current blocker routing must be fact-first. If validation logs show `Service ready, running exploit` or exploit-stage markers such as stages, heap operations, leaks, payloads, shells, or flag reads, then readiness has already succeeded for routing purposes. Readiness probe noise may remain as `classification_conflicts`, but it must not override a current solver/leak blocker.

## Gap Audit Summary

The current codebase covers more than the original sketch assumed:

- Root failure and current blocker do not need new top-level concepts. `first-validation-failure.json` captures the root validation failure, while `validation-history.json` plus latest failed validation fields capture the current blocker. The remaining gap is surfacing both clearly in API/dashboard and repair prompts, especially when the current blocker is repair infrastructure.
- `repair_invocation_failed` can replace `per_results` after a Hermes repair invocation fails. The root validation failure remains available in `first-validation-failure.json`, but current API/detail/prompt surfaces must keep both the original host-validation root cause and the repair-invocation blocker visible.
- Pwn readiness-vs-solver classification is mostly implemented through `pwn_failure_stage`, `readiness_established`, `_exploit_stage_started()`, and `classification_conflicts`. This change should add boundary tests and expose conflicts, rather than redesigning stage classification.
- Retry/resume already have safe `resume_from_shard_basename` validation, explicit clean/resume separation, and retry context with `first_failure`/`latest_failure`. The remaining work is acceptance-aware resume skip and tests proving latest finalized failed-attempt diagnostics are used.
- Contract, service-readiness, solver, timeout, validation-capture, and inconclusive routes already exist in `validation_repair_policy.py`. The remaining route gap is solver-acceptance blocked/regeneration diagnostics, not a new routing framework.
- Publication already has output-manifest consistency via `validated-output.json`, `publish-status.json`, and manifest hashing. The missing piece is requiring solver acceptance passed for that same manifest.
- Revalidation currently promotes on host validation success for the selected current directory. It must add solver acceptance passed for Web/Pwn before promoting.

## Goals / Non-Goals

**Goals:**
- Make Web/Pwn solver acceptance a hard pre-publish condition.
- Reject or repair default solver paths that do not use the validation target through `CHAL_HOST` and `CHAL_PORT`.
- Prevent unbounded solver I/O, missing helper modules, hardcoded flags, and organizer-only file reads from reaching successful build completion.
- Require repair rounds to prove progress before consuming more budget.
- Record bounded solver-focused blocked routes when repair cannot make progress, leaving any solver-only regeneration implementation behind the existing repair abstraction for a later slice.
- Preserve clean validation evidence for the final successful artifact.

**Non-Goals:**
- No guarantee that every arbitrary challenge is solvable by the model.
- No infinite repair loops or unbounded validation retries.
- No blanket rewrite of Pwn scaffold, Dockerfile, xinetd, service source, or payload logic without evidence.
- No historical backfill for already-built artifacts.
- No acceptance bypass for local-only solver smoke tests.
- No second repair page, global repair resource, cross-attempt workspace scan, or automated challenge regeneration implementation.

## Decisions

1. Treat solver acceptance as a release fence, not only a repair hint.
   A Web/Pwn build can succeed only after host validation runs the current `validate.sh` and `writenup/exp.py` against the service path and returns passed. Metadata or document claims are not enough. Local `process()` smoke tests may support debugging, but they are not the authoritative acceptance path.

2. Add a static solver preflight before spending repair budget.
   The preflight should inspect `writenup/exp.py`, `validate.sh`, and declared helper files for deterministic defects: default-path hardcoded hosts/ports, organizer-file flag reads, missing local helper modules, unbounded pwntools/socket reads, unbounded brute-force/leak loops, and missing `CHAL_HOST` / `CHAL_PORT` wiring. Findings become structured validation details. Blocking should be scoped to Web/Pwn reference solvers and should allow explicit local debug branches guarded by `LOCAL=1` or equivalent.

3. Keep runtime validation authoritative.
   Static preflight catches obvious bad solvers, but a solver is accepted only by runtime validation. Runtime diagnostics must preserve command, exit code, stdout/stderr tails, final flag candidate, readiness evidence, service logs when available, and structured solver-quality findings.

4. Require repair progress to be measurable.
   Each deterministic or Hermes repair round should record a compact progress fingerprint derived from the solver file hash, validate wrapper hash, debug report hash, validation failure class/signature, structured solver-quality detail codes, and solver acceptance status/fingerprint. If a round changes none of these, or repeats the same failure without improving diagnostics, the runner should stop that repair path and record a blocked reason. This extends the existing validation failure fingerprint guard.

5. Keep regeneration explicit and bounded.
   If validation proves the service is reachable and the solver is bad, a later implementation may regenerate or rewrite only `writenup/exp.py` and supporting debug evidence inside the existing repair abstraction. Automated challenge implementation regeneration is out of scope for this change; when diagnostics show a target/solver contradiction such as missing shipped binary, impossible flag path, or service behavior inconsistent with metadata, the attempt should fail with a human-action reason such as `challenge_regeneration_required`.

6. Make blocked states explicit.
   When solver repair cannot prove progress within budget, the attempt should fail with a clear reason such as `solver_unrepairable`, `solver_quality_blocked`, `solver_regeneration_failed`, or `challenge_regeneration_required`. These reasons can live in existing report/progress/attempt summary fields in the first slice.

7. Final validation must be clean.
   After any deterministic or Hermes repair, the runner must rebuild or rebind the execution workspace as needed and run final host validation from the exact output tree that will be published. Only that final validation, with solver acceptance passed for Web/Pwn, can promote the attempt to done/succeeded.

8. Keep solver acceptance schema-compatible.
   The first slice should add a nested `solver_acceptance` object and mirrored summary fields where useful, but must keep existing keys such as `status`, `solve_status`, `contract_errors`, `failure_details`, `validation_failure_details`, and `validation_failure_signature` readable. Older validation histories without solver fields are `unavailable`, not failed by reinterpretation.

9. Bind acceptance to output-manifest identity.
   A passed solver acceptance round is valid only for the same `output_manifest_hash` that the runner is about to publish or that revalidation is about to promote. Any mutation that changes the publish manifest invalidates both validation passed and solver acceptance passed.

10. Treat `validate.sh` as the integration boundary.
    The host runs `validate.sh`; the solver preflight should verify that the default validation path wires `CHAL_HOST` and `CHAL_PORT` through to `writenup/exp.py` or an equivalent solver invocation. It should not require a single Python API shape when shell wrappers pass environment correctly.

11. Keep regeneration behind the existing repair abstraction.
    Solver-only regeneration should be modeled as a bounded repair route for the current attempt workspace, recorded in validation history/progress, and followed by host revalidation. It should not create a new top-level repair page, bypass `BuildAttemptRepairService`, or mutate sibling attempts. Challenge regeneration, if enabled, must either remain an explicit retry/new lineage entry or be recorded as part of the current attempt context before any success transition.

12. Separate solver-quality blocking from service-readiness blocking.
    Static solver defects can block solver acceptance before runtime solve, but runtime failures must keep the existing failure-governance classification. For example, missing readiness evidence can remain `service-readiness`, while readiness established plus prompt EOF can remain `solver`. Solver acceptance adds release gating and details; it should not collapse all validation failures into `solver`.

13. Preserve root failure when current blocker changes.
    Repair-infrastructure failures, including `repair_invocation_failed`, are current blockers. They must not erase the root host-validation failure from first-failure/history evidence, retry context, repair prompts, or attempt detail responses.

14. Keep solver evidence and current blocker system-level.
    Fixes for stale Pwn evidence, bad artifact paths, leak failures, and no-change repair outcomes must be implemented through metadata/artifact, validation governance, repair policy, and API/report fields. They must not patch a single generated `writenup/exp.py` or encode challenge-specific filenames such as `taskqueue` as control-flow exceptions.

## Risks / Trade-offs

- [Risk] Static gates may reject valid unusual solvers. -> Mitigation: scope checks to default validation paths, allow explicit local debug branches, and make every blocked code visible in structured details.
- [Risk] Regeneration may churn working challenge source. -> Mitigation: this change records blocked/future route decisions only; automated challenge regeneration is deferred.
- [Risk] More validation rounds increase latency. -> Mitigation: keep budgets bounded and stop on no-progress fingerprints.
- [Risk] Heuristic static analysis can miss dynamic bugs. -> Mitigation: keep runtime validation as the only acceptance source.
- [Risk] Hard acceptance can reduce apparent batch success rate at first. -> Mitigation: failures become honest blocked attempts instead of false successes, and repair evidence becomes actionable.
- [Risk] New solver fields can break older dashboard/report consumers. -> Mitigation: make fields additive, keep existing keys, and emit `unavailable` markers for older histories.

## Migration Plan

1. Add solver preflight diagnostics and tests without changing publish behavior.
2. Wire solver acceptance into runner final-validation publish fences.
3. Add repair-progress fingerprints and blocked reasons.
4. Surface blocked reasons, root/current blocker lineage, route decisions, and solver-quality details in attempt list/detail.
5. Record `challenge_regeneration_required` as a human-action blocked reason when solver-only routes cannot resolve artifact contradictions; implementing challenge regeneration remains a later change.
6. Roll out for Web/Pwn first; leave Reverse unchanged unless a future change defines comparable solver acceptance rules.

## Open Questions

- Should blocked reasons become a durable enum on `build_attempts`, or remain report/progress-derived in the first implementation? Recommendation for this change: keep them report/progress/validation-history derived first, then add a migration only if UI filtering needs indexed blocked states.
- What exact maximum budgets should apply to solver repair progress checks? Recommendation for this change: keep existing repair budgets and add a no-progress stop condition keyed by solver/evidence fingerprints. Challenge regeneration budget is out of scope.
- If a later slice implements solver-only regeneration, should it reuse the full original design prompt or a narrower solver-only prompt assembled from validation/debug evidence? Recommendation: use a narrower solver-only prompt by default, with original design metadata as context only.

## 20-Round Optimization Log

This section records the requested repeated review loop. Each round re-read the proposal surface against the current implementation boundaries above, identified one issue, applied the corresponding optimization to this change set, and re-evaluated the result.

| Round | Read Focus | Problem Found | Optimization Applied | Re-evaluation |
| --- | --- | --- | --- | --- |
| 1 | Proposal release fence | Success gating was strong but not tied to the existing validator entrypoint. | Bound solver acceptance to `ChallengeValidator.validate_one()` result dictionaries. | The gate now has a concrete implementation owner. |
| 2 | Validation compatibility | New diagnostics could replace `contract_errors` accidentally. | Required additive `failure_details`/`validation_failure_details` while preserving `contract_errors`. | Existing callers can remain readable. |
| 3 | Runner history | Acceptance evidence could be stored outside existing validation history. | Required enrichment of `state/validation-history.json` and first-failure records. | Evidence remains in the current repair/report path. |
| 4 | Publish fence | Passed solver evidence could be stale after output mutation. | Bound acceptance to the same `output_manifest_hash` used by final publication. | Post-validation mutation cannot reuse stale acceptance. |
| 5 | Fingerprints | The proposal introduced progress fingerprints without using existing no-progress logic. | Directed solver fingerprints to extend `validation_failure_fingerprints()`. | No-progress behavior stays consistent with Phase 1 governance. |
| 6 | Attempt scoping | API/list derivation could scan all execution histories. | Required current-attempt derivation only. | Avoids stale workspace selection and list-path blowups. |
| 7 | Revalidation | Same-attempt revalidation could promote without solver acceptance. | Made revalidation use the same final acceptance requirement. | Retry and revalidate now share the success predicate. |
| 8 | Repair service | Solver regeneration could become a new top-level flow. | Kept regeneration inside the current repair abstraction/workspace. | The existing detail page and repair runs remain the operator surface. |
| 9 | Classification | Solver acceptance could override failure governance classes. | Required acceptance to add release gating without collapsing classifications. | `service-readiness`, `timeout`, `contract`, and `solver` remain useful. |
| 10 | Static preflight | Host/port checks could reject valid shell wrappers. | Clarified that `validate.sh` is the integration boundary and env wiring is sufficient. | Legitimate wrappers are allowed when the default path is service-bound. |
| 11 | Local debug | Local branches could be banned too broadly. | Kept explicit `LOCAL=1`-style allowances when default validation remains bounded. | Debug ergonomics do not weaken acceptance. |
| 12 | Missing helpers | Helper-module checks lacked dependency allowance wording. | Scoped blocking to undeclared local helpers not present or supported at runtime. | Vendored/declared helpers can pass. |
| 13 | Unbounded I/O | Static I/O checks could become heuristic-only blockers. | Required structured codes and runtime validation as authority. | Operators see why a solver is blocked. |
| 14 | Regeneration scope | Challenge regeneration could churn working sources and budgets were undefined. | Deferred automated challenge regeneration to a later change and kept this change to blocked route recording, with any solver-only regeneration left behind the existing repair abstraction for a later slice. | Scope is now bounded. |
| 15 | Older attempts | Historical validation histories might be reinterpreted as failed. | Added unavailable semantics for missing solver acceptance fields. | Backward compatibility is explicit. |
| 16 | API fields | Attempt detail/list fields were underspecified. | Added bounded derivation and detail/list exposure requirements. | Dashboard can display acceptance without expensive scans. |
| 17 | Budgets | Repair/regeneration budget defaults were open-ended. | Converted budget question into an implementation-task decision with bounded defaults. | No infinite repair path remains in scope. |
| 18 | Dashboard | UI wording could hide blocked/regenerated states under generic failure. | Required blocked/regeneration states alongside existing class/signature. | Operators get actionable solver-specific status. |
| 19 | Verification | Test plan did not call out dependency-direction and focused no-DB gates. | Added implementation-task validation for existing boundary tests and OpenSpec tooling blocker reporting. | Verification matches this repo's known constraints. |
| 20 | Overall consistency | The proposal could be read as a parallel subsystem. | Added this implementation-fit section and decisions 8-12. | The optimized proposal now extends current architecture instead of bypassing it. |
