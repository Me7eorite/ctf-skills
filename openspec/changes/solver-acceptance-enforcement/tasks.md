## 1. Solver Acceptance and Static Preflight

- [ ] 1.1 Add a Web/Pwn solver acceptance model that records status, fingerprint, structured diagnostics, blocked reason, route, output manifest hash, and unavailable fields in validation result dictionaries.
- [ ] 1.2 Implement solver static preflight from the existing validation path for `writenup/exp.py` and `validate.sh` default paths, covering `CHAL_HOST` / `CHAL_PORT`, hardcoded host/port, local-only default execution, hardcoded flags, organizer-only file reads, missing helper modules, and unbounded Pwn reads/loops.
- [ ] 1.3 Preserve `contract_errors`, `failure_details`, `validation_failure_details`, and older validation-history compatibility while adding solver acceptance fields.
- [ ] 1.4 Add explicit allowances for local debug branches such as `LOCAL=1` when the default validation path remains service-bound and bounded.
- [ ] 1.5 Thread solver static preflight failures into `validation_failure_details`, `validation-history.json`, report merge output, and failure signatures without bypassing existing validation failure governance.
- [ ] 1.6 Add unit tests for every static diagnostic code, compatibility with `contract_errors`, older-history unavailable fields, and local-debug branch allowance.

## 2. Runtime Validation and Final Publish Fence

- [ ] 2.1 Extend host validation so Web/Pwn runtime validation records solver acceptance passed/failed/unavailable, validation command, return code, stdout/stderr tails, final flag candidate, output manifest hash, and acceptance fingerprint.
- [ ] 2.2 Require final Web/Pwn publication to use a passed validation round with solver acceptance passed for the exact output tree being published.
- [ ] 2.3 Invalidate the publish candidate and rerun validation after any repair, regeneration, metadata stamping, or file mutation that changes the output manifest.
- [ ] 2.4 Keep older validation histories readable when solver acceptance fields are missing.
- [ ] 2.5 Add runner and validator tests proving failed exp blocks publish, local-only smoke does not pass, final clean validation publishes, and post-validation mutation forces revalidation.

## 3. Repair Progress Enforcement

- [ ] 3.1 Extend `validation_failure_fingerprints()` or its inputs with solver acceptance progress fingerprints using solver hash, validate wrapper hash, debug report hash, validation failure class/signature, solver-quality detail codes, output manifest hash, and concise runtime evidence.
- [ ] 3.2 Compare fingerprints after deterministic repair, Hermes repair, and solver-only regeneration within one runner invocation.
- [ ] 3.3 Stop automatic solver repair with an explicit blocked reason when a round changes no relevant file/evidence and repeats the same acceptance fingerprint.
- [ ] 3.4 Treat materially different solver failures, such as missing helper becoming flag mismatch, as progress within the bounded budget.
- [ ] 3.5 Add tests for no-progress stop, changed-solver continuation, changed-diagnostic continuation, same-signature blocked outcomes, and compatibility with existing repeated-failure tests.

## 4. Solver Regeneration and Human-Action Blocked Routes

- [ ] 4.1 Add a bounded solver-only regeneration route inside the existing current-attempt repair workflow that rewrites `writenup/exp.py` and supporting debug evidence while preserving challenge implementation/deployment files.
- [ ] 4.2 Feed solver regeneration prompts with validation history, static diagnostics, runtime tails, current `exp.py`, `validate.sh`, debug report, shipped binaries/attachments summary, service-readiness evidence, and original design metadata when available.
- [ ] 4.3 Do not implement automated challenge regeneration in this change; when evidence proves solver-only repair cannot resolve an artifact contradiction, record `challenge_regeneration_required` as a human-action blocked reason.
- [ ] 4.4 Record route decisions and outcomes as `solver_regenerated`, `challenge_regeneration_required`, `solver_regeneration_failed`, or equivalent failed-attempt diagnostic evidence.
- [ ] 4.5 Define bounded default budgets before implementation and add tests proving solver-only regeneration is preferred for reachable-service solver failures while artifact contradictions fail with `challenge_regeneration_required`.

## 5. Orchestration, API, and Dashboard Visibility

- [ ] 5.1 Update build orchestration, retry, repair, and revalidate flows so Web/Pwn success requires final solver acceptance passed.
- [ ] 5.2 Expose solver acceptance status, fingerprint, diagnostic summary, regeneration route, output manifest hash, and blocked reason in attempt detail responses.
- [ ] 5.3 Expose bounded solver acceptance summaries in attempt list responses only for returned rows or copied summaries, without scanning unrelated execution histories.
- [ ] 5.4 Preserve sibling attempt independence so one solver-blocked attempt does not consume another attempt's repair or regeneration budget.
- [ ] 5.5 Update dashboard rendering to show solver acceptance blocked/regenerated diagnostics alongside existing validation failure class/signature without adding a new attempt status.
- [ ] 5.6 Add API/orchestration tests for failed solver acceptance, retry, manual repair, revalidate, sibling independence, bounded list derivation, older-history compatibility, and blocked reason exposure.

## 6. Verification and Rollout

- [ ] 6.1 Add fixture Web/Pwn challenges for passing solver, hardcoded target, local-only default, missing helper, unbounded read, hardcoded flag, wrong flag, prompt-sync failure, and older validation history without solver acceptance fields.
- [ ] 6.2 Run focused unit and integration tests for validation, runner resume/repair, build orchestration, build-attempt APIs, dashboard serialization, and `tests/app/test_dependency_direction.py` if package boundaries are touched.
- [ ] 6.3 Run `uv run ruff`, `uv run mypy` for touched modules, and `uv run openspec validate solver-acceptance-enforcement --strict` when the OpenSpec CLI is available; if unavailable, record it as a tooling blocker and run repo-native static checks instead.
- [ ] 6.4 Document rollout behavior: Web/Pwn enforcement enabled for new attempts, older attempts readable with unavailable solver acceptance fields, Reverse unchanged.
