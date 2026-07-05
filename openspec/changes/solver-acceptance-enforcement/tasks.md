## 0. Baseline Audit Already Covered

- [x] 0.1 Confirm root failure/current blocker have existing storage: `state/first-validation-failure.json`, `state/validation-history.json`, latest failed validation fields, and retry `first_failure`/`latest_failure`.
- [x] 0.2 Confirm Pwn failure-stage governance already separates service-readiness, solver, validation-capture, inconclusive, and classification-conflict cases.
- [x] 0.3 Confirm retry/resume already validates `resume_from_shard_basename`, separates clean/resume execution modes, and binds retry context to the same attempt's first/latest diagnostics.
- [x] 0.4 Confirm contract, service-readiness, solver, timeout, validate-capture, and inconclusive repair routes already exist in `validation_repair_policy.py`.
- [x] 0.5 Confirm publish consistency already records and compares `output_manifest_hash` through `validated-output.json` and `publish-status.json`.

## 1. Solver Acceptance Model and Preflight Gaps

- [ ] 1.1 Add a Web/Pwn solver acceptance model that records status, fingerprint, structured diagnostics, blocked reason, route, output manifest hash, and unavailable fields in validation result dictionaries.
- [ ] 1.2 Extend the existing `ChallengeValidator.contract_errors()` / `_solver_integrity_errors()` path only for missing solver-quality gaps: default-path `CHAL_HOST` / `CHAL_PORT` wiring, hardcoded service target, local-only default execution, missing local helper modules, and unbounded Pwn reads/loops.
- [ ] 1.3 Preserve existing hardcoded-flag, organizer-file, destructive-cleanup, compose-isolation, Pwn evidence freshness, `contract_errors`, `failure_details`, and older validation-history behavior while adding solver acceptance fields.
- [ ] 1.4 Add explicit allowances for local debug branches such as `LOCAL=1` when the default validation path remains service-bound and bounded.
- [ ] 1.5 Thread static preflight failures into `validation_failure_details`, `validation-history.json`, report merge output, and failure signatures without bypassing existing validation failure governance.
- [ ] 1.6 Add focused tests for newly added static diagnostic codes, plus regression tests proving existing contract checks and local-debug allowances still behave.

## 2. Runtime Acceptance and Manifest-Bound Success

- [ ] 2.1 Extend host validation so Web/Pwn runtime validation records solver acceptance passed/failed/unavailable, validation command, return code, stdout/stderr tails, final flag candidate, output manifest hash, and acceptance fingerprint.
- [ ] 2.2 Update `authoritative_validation_pass()`, report merge, `validated-output.json`, runner publication, and `validate/passed` event writing so Web/Pwn success requires solver acceptance passed for the current output manifest.
- [ ] 2.3 Update `validate_workspace_success_state()` so succeeded Web/Pwn attempts without manifest-bound solver acceptance are considered inconsistent rather than publishable.
- [ ] 2.4 Update same-attempt revalidation so Web/Pwn promotion requires solver acceptance passed for the selected current attempt directory; preserve current workspace resolution and latest-attempt locking.
- [ ] 2.5 Add runner, output-consistency, and revalidation tests proving host validation alone is insufficient, final accepted validation publishes/promotes, older history reports unavailable, and post-validation mutation forces revalidation.

## 3. Repair Progress and Blocked Reasons

- [ ] 3.1 Extend `validation_failure_fingerprints()` or its inputs with solver acceptance progress fingerprints using solver hash, validate wrapper hash, debug report hash, validation failure class/signature, solver-quality detail codes, output manifest hash, and concise runtime evidence.
- [ ] 3.2 Compare fingerprints after deterministic and Hermes repair rounds within one runner invocation.
- [ ] 3.3 Stop automatic solver repair with an explicit blocked reason when a round changes no relevant file/evidence and repeats the same acceptance fingerprint.
- [ ] 3.4 Treat materially different solver failures, such as missing helper becoming flag mismatch or prompt-sync evidence improving, as progress within the bounded budget.
- [ ] 3.5 Preserve the original host-validation root failure when the current blocker becomes `repair_invocation_failed`, repair timeout, or another repair-infrastructure failure.
- [ ] 3.6 Add tests for no-progress stop, changed-solver continuation, changed-diagnostic continuation, same-signature blocked outcomes, and root/current blocker preservation.

## 4. Route Decisions and Human-Action Outcomes

- [ ] 4.1 Reuse the existing repair policy routes for contract, service-readiness, solver, timeout, validate-capture, and inconclusive failures; add only solver-acceptance blocked/route fields needed by this change.
- [ ] 4.2 Record route decisions and outcomes such as `solver_unrepairable`, `solver_quality_blocked`, `challenge_regeneration_required`, or future `solver_regeneration_requested` as failed-attempt diagnostic evidence, not new attempt statuses.
- [ ] 4.3 Do not implement automated challenge regeneration in this change; when evidence proves solver-only repair cannot resolve an artifact contradiction, record `challenge_regeneration_required` as a human-action blocked reason.
- [ ] 4.4 Keep any future solver-only regeneration behind the existing current-attempt repair abstraction; do not add a global repair page, scan sibling attempts, or mutate challenge implementation/deployment files without evidence.
- [ ] 4.5 Add policy and repair-service tests proving route decisions stay attempt-scoped and existing route classifications are not regressed.

## 5. API and Dashboard Visibility

- [ ] 5.1 Expose solver acceptance status, fingerprint, diagnostic summary, output manifest hash, route, and blocked reason in attempt detail responses.
- [ ] 5.2 Expose root failure and current blocker lineage in attempt detail responses, preserving first host-validation failure when current blocker is repair infrastructure.
- [ ] 5.3 Expose bounded solver acceptance and blocked summaries in attempt list responses only for returned rows or copied summaries, without scanning unrelated execution histories.
- [ ] 5.4 Preserve sibling attempt independence so one solver-blocked attempt does not consume another attempt's repair budget, retry budget, or status.
- [ ] 5.5 Update dashboard rendering to show solver acceptance, blocked reason, route, and classification conflicts alongside existing validation failure class/signature without adding a new attempt status.
- [ ] 5.6 Add API/dashboard tests for solver acceptance failure, repair-invocation blocker over root failure, retry context, manual repair, revalidate, sibling independence, bounded list derivation, older-history compatibility, and blocked reason exposure.

## 6. Verification and Rollout

- [ ] 6.1 Add or update fixture Web/Pwn challenges for passing accepted solver, hardcoded target, local-only default, missing helper, unbounded read, hardcoded flag/organizer-file regression, wrong flag, prompt-sync failure, repair invocation failure, and older validation history without solver acceptance fields.
- [ ] 6.2 Run focused unit and integration tests for validation, Pwn debug/governance, runner resume/repair, build orchestration, build-attempt APIs, revalidation, dashboard serialization, and `tests/app/test_dependency_direction.py` if package boundaries are touched.
- [ ] 6.3 Run `uv run ruff`, `uv run mypy`, and `uv run openspec validate solver-acceptance-enforcement --strict` when the OpenSpec CLI is available; if unavailable, record it as a tooling blocker and run repo-native static checks instead.
- [ ] 6.4 Document rollout behavior: Web/Pwn enforcement enabled for new attempts, older attempts readable with unavailable solver acceptance fields, Reverse unchanged.
