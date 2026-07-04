## Context

`batch-failure-governance` makes validation failures class-aware and stops blind repeated repair loops, but it deliberately does not make reference solver quality a hard release gate. The remaining operator pain is sharper: Web/Pwn artifacts can carry a weak `writenup/exp.py`, validation may fail in ways that are correctly classified as `solver`, and repair can still fail to converge. The system needs to guarantee the release boundary: a challenge is not built unless the reference solver passes against the host validation service path.

Current validation already runs `validate.sh`, records stdout/stderr tails, stores validation history, and routes solver failures to Hermes repair with context. This change builds on that path. It should not reclassify failure governance or replace the runner; it should add hard solver acceptance, repair-progress checks, and safe escalation/regeneration decisions.

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

## Risks / Trade-offs

- [Risk] Static gates may reject valid unusual solvers. -> Mitigation: scope checks to default validation paths, allow explicit local debug branches, and make every blocked code visible in structured details.
- [Risk] Regeneration may churn working challenge source. -> Mitigation: default to solver-only regeneration; require evidence before challenge regeneration.
- [Risk] More validation rounds increase latency. -> Mitigation: keep budgets bounded and stop on no-progress fingerprints.
- [Risk] Heuristic static analysis can miss dynamic bugs. -> Mitigation: keep runtime validation as the only acceptance source.
- [Risk] Hard acceptance can reduce apparent batch success rate at first. -> Mitigation: failures become honest blocked attempts instead of false successes, and repair evidence becomes actionable.

## Migration Plan

1. Add solver preflight diagnostics and tests without changing publish behavior.
2. Wire solver acceptance into runner final-validation publish fences.
3. Add repair-progress fingerprints and blocked reasons.
4. Add solver-regeneration route and tests.
5. Add challenge-regeneration route only after solver-only regeneration is proven bounded.
6. Surface blocked reasons and solver-quality details in attempt list/detail.
7. Roll out for Web/Pwn first; leave Reverse unchanged unless a future change defines comparable solver acceptance rules.

## Open Questions

- Should blocked reasons become a durable enum on `build_attempts`, or remain report/progress-derived in the first implementation?
- What exact maximum budgets should apply to solver repair, solver regeneration, and challenge regeneration?
- Should solver regeneration reuse the full original design prompt, or a narrower solver-only prompt assembled from validation/debug evidence?
