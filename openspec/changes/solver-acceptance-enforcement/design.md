## Context

`batch-failure-governance` makes validation failures class-aware and stops blind repeated repair loops, but it deliberately does not make reference solver quality a hard release gate. The remaining operator pain is sharper: Web/Pwn artifacts can carry a weak `writenup/exp.py`, validation may fail in ways that are correctly classified as `solver`, and repair can still fail to converge. The system needs to guarantee the release boundary: a challenge is not built unless the reference solver passes against the host validation service path.

Current validation already runs `validate.sh`, records stdout/stderr tails, stores validation history, and routes solver failures to Hermes repair with context. This change builds on that path. It should not reclassify failure governance or replace the runner; it should add hard solver acceptance, repair-progress checks, and safe escalation/regeneration decisions.

## Current Implementation Fit

The implementation already has several enforcement boundaries that this change must extend instead of replacing:

- `src/domain/validation.py::ChallengeValidator.validate_one()` is the authoritative per-challenge validation entrypoint. Solver acceptance fields should be added to its result dictionaries and to `failure_details`/`validation_failure_details`; `contract_errors` must remain as a compatibility surface for existing tests, reports, and callers.
- `ChallengeValidator.contract_errors()` and `_solver_integrity_errors()` already perform deterministic solver/validator anti-cheat checks. The new Web/Pwn static preflight should live beside those checks or be called from the same validation path, while preserving older RE solver-integrity behavior.
- `src/hermes/runner.py` already appends `state/validation-history.json`, records first failure evidence, compares validation failure fingerprints, and blocks publication when the output manifest changes after validation. Solver acceptance must enrich those existing records and final-publish checks rather than adding an independent success marker.
- `src/domain/validation_repair_policy.py::validation_failure_fingerprints()` is the existing no-progress guard. Solver acceptance fingerprints should either extend this function or feed it a solver-specific sub-fingerprint so repeated solver failures are detected consistently with Phase 1 governance.
- Same-attempt repair and revalidation are already attempt-scoped through `BuildAttemptRepairService` and `BuildAttemptRevalidationService`, with challenge roots normalized under the current attempt workspace. This change must not scan unrelated `work/executions/*` trees to infer solver acceptance.
- `src/web/build_attempts_endpoints.py` already derives attempt-detail validation fields from latest validation history. Solver acceptance API fields should be derived for the current returned rows/details only, not by global workspace scans.

## Goals / Non-Goals

**Goals:**
- Make Web/Pwn solver acceptance a hard pre-publish condition.
- Reject or repair default solver paths that do not use the validation target through `CHAL_HOST` and `CHAL_PORT`.
- Prevent unbounded solver I/O, missing helper modules, hardcoded flags, and organizer-only file reads from reaching successful build completion.
- Require repair rounds to prove progress before consuming more budget.
- Allow bounded solver regeneration or challenge regeneration when repair cannot make progress and evidence supports it.
- Preserve clean validation evidence for the final successful artifact.

**Non-Goals:**
- No guarantee that every arbitrary challenge is solvable by the model.
- No infinite repair loops or unbounded validation retries.
- No blanket rewrite of Pwn scaffold, Dockerfile, xinetd, service source, or payload logic without evidence.
- No historical backfill for already-built artifacts.
- No acceptance bypass for local-only solver smoke tests.
- No second repair page, global repair resource, or cross-attempt workspace scan.

## Decisions

1. Treat solver acceptance as a release fence, not only a repair hint.
   A Web/Pwn build can succeed only after host validation runs the current `validate.sh` and `writenup/exp.py` against the service path and returns passed. Metadata or document claims are not enough. Local `process()` smoke tests may support debugging, but they are not the authoritative acceptance path.

2. Add a static solver preflight before spending repair budget.
   The preflight should inspect `writenup/exp.py`, `validate.sh`, and declared helper files for deterministic defects: default-path hardcoded hosts/ports, organizer-file flag reads, missing local helper modules, unbounded pwntools/socket reads, unbounded brute-force/leak loops, and missing `CHAL_HOST` / `CHAL_PORT` wiring. Findings become structured validation details. Blocking should be scoped to Web/Pwn reference solvers and should allow explicit local debug branches guarded by `LOCAL=1` or equivalent.

3. Keep runtime validation authoritative.
   Static preflight catches obvious bad solvers, but a solver is accepted only by runtime validation. Runtime diagnostics must preserve command, exit code, stdout/stderr tails, final flag candidate, readiness evidence, service logs when available, and structured solver-quality findings.

4. Require repair progress to be measurable.
   Each repair or regeneration round should record a compact progress fingerprint derived from the solver file hash, validate wrapper hash, debug report hash, validation failure class/signature, and structured solver-quality detail codes. If a round changes none of these, or repeats the same failure without improving diagnostics, the runner should stop that repair path and escalate.

5. Prefer solver regeneration before challenge regeneration.
   If validation proves the service is reachable and the solver is bad, the first escalation should regenerate or rewrite only `writenup/exp.py` and supporting debug evidence. Challenge implementation regeneration is allowed only when diagnostics show a target/solver contradiction that cannot be resolved by solver-only repair, such as missing shipped binary, impossible flag path, or service behavior inconsistent with metadata.

6. Make blocked states explicit.
   When solver repair cannot prove progress within budget, the attempt should fail with a clear reason such as `solver_unrepairable`, `solver_quality_blocked`, `solver_regeneration_failed`, or `challenge_regeneration_required`. These reasons can live in existing report/progress/attempt summary fields in the first slice.

7. Final validation must be clean.
   After any deterministic repair, Hermes repair, solver regeneration, or challenge regeneration, the runner must rebuild or rebind the execution workspace as needed and run final host validation from the exact output tree that will be published. Only that final validation can promote the attempt to done/succeeded.

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

## Risks / Trade-offs

- [Risk] Static gates may reject valid unusual solvers. -> Mitigation: scope checks to default validation paths, allow explicit local debug branches, and make every blocked code visible in structured details.
- [Risk] Regeneration may churn working challenge source. -> Mitigation: default to solver-only regeneration; require evidence before challenge regeneration.
- [Risk] More validation rounds increase latency. -> Mitigation: keep budgets bounded and stop on no-progress fingerprints.
- [Risk] Heuristic static analysis can miss dynamic bugs. -> Mitigation: keep runtime validation as the only acceptance source.
- [Risk] Hard acceptance can reduce apparent batch success rate at first. -> Mitigation: failures become honest blocked attempts instead of false successes, and repair evidence becomes actionable.
- [Risk] New solver fields can break older dashboard/report consumers. -> Mitigation: make fields additive, keep existing keys, and emit `unavailable` markers for older histories.

## Migration Plan

1. Add solver preflight diagnostics and tests without changing publish behavior.
2. Wire solver acceptance into runner final-validation publish fences.
3. Add repair-progress fingerprints and blocked reasons.
4. Add solver-regeneration route and tests.
5. Add challenge-regeneration route only after solver-only regeneration is proven bounded.
6. Surface blocked reasons and solver-quality details in attempt list/detail.
7. Roll out for Web/Pwn first; leave Reverse unchanged unless a future change defines comparable solver acceptance rules.

## Open Questions

- Should blocked reasons become a durable enum on `build_attempts`, or remain report/progress-derived in the first implementation? Recommendation for this change: keep them report/progress/validation-history derived first, then add a migration only if UI filtering needs indexed blocked states.
- What exact maximum budgets should apply to solver repair, solver regeneration, and challenge regeneration? Recommendation for this change: define explicit config defaults in implementation tasks before coding, with solver-only regeneration at most once per runner invocation unless the solver fingerprint changes.
- Should solver regeneration reuse the full original design prompt, or a narrower solver-only prompt assembled from validation/debug evidence? Recommendation for this change: use a narrower solver-only prompt by default, with original design metadata as context only.

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
| 14 | Regeneration evidence | Challenge regeneration could churn working sources. | Required artifact contradiction evidence before challenge regeneration. | Solver-only repair remains preferred. |
| 15 | Older attempts | Historical validation histories might be reinterpreted as failed. | Added unavailable semantics for missing solver acceptance fields. | Backward compatibility is explicit. |
| 16 | API fields | Attempt detail/list fields were underspecified. | Added bounded derivation and detail/list exposure requirements. | Dashboard can display acceptance without expensive scans. |
| 17 | Budgets | Repair/regeneration budget defaults were open-ended. | Converted budget question into an implementation-task decision with bounded defaults. | No infinite repair path remains in scope. |
| 18 | Dashboard | UI wording could hide blocked/regenerated states under generic failure. | Required blocked/regeneration states alongside existing class/signature. | Operators get actionable solver-specific status. |
| 19 | Verification | Test plan did not call out dependency-direction and focused no-DB gates. | Added implementation-task validation for existing boundary tests and OpenSpec tooling blocker reporting. | Verification matches this repo's known constraints. |
| 20 | Overall consistency | The proposal could be read as a parallel subsystem. | Added this implementation-fit section and decisions 8-12. | The optimized proposal now extends current architecture instead of bypassing it. |
