## 1. Primitive contract model

- [ ] 1.1 Define the host-owned versioned primitive library schema, initially in `src/domain/pwn_primitives.py`, with `primitive_id`, `primitive_version`, `requires[]`, `disqualifiers[]`, field-level `evidence_schema`, `offset_policy`, `difficulty_controls`, and `diagnostic_precedence`.
- [ ] 1.2 Add first-version primitive definitions for `stack_overflow_ret2win_basic`, `format_string_controlled_printf_basic`, and `stack_overflow_with_leak_ret2libc` only, with ret2win requiring a concrete host-locatable control-flow target or bound dynamic control-flow observation.
- [ ] 1.3 Add negative safe-pattern and challenge-escape cases, including bounded reads, fixed secret checks, plaintext flag exposure, and debug/backdoor paths.
- [ ] 1.3a Add stronger-than-declared primitive superset handling: explicit primitive-level allow/disqualify rules when defined, otherwise `pwn_primitive_inconclusive` with difficulty-policy review details.
- [ ] 1.4 Add diagnostic codes for `pwn_primitive_not_realized`, `pwn_primitive_disqualified`, `pwn_primitive_evidence_missing`, `pwn_primitive_unsupported`, `pwn_challenge_escape`, `pwn_primitive_inconclusive`, and stale-artifact evidence diagnostics.
- [ ] 1.5 Define deterministic diagnostic precedence: unsupported id, primitive-specific disqualifier hit, challenge escape, missing required evidence, non-equivalent primitive realization, inconclusive evidence, then generic not-realized fallback.
- [ ] 1.6 Render supported primitive ids, versions, and intent/evidence field names into Design prompts from the same host-owned primitive library used by validation.

## 2. Design contract integration

- [ ] 2.1 Extend pwn Design validation to require `build_contract.pwn_primitive_contract` while preserving the existing challenge JSON shape.
- [ ] 2.2 Use Design-stage field names that describe intent, such as `intended_implementation_features`, and reject Design-stage claims that depend on source, binary, debugger, runtime, or offset proof before Build exists.
- [ ] 2.3 Record no-valid-pwn-primitive outcomes as attempt diagnostics or review routing without adding a new `design_tasks.status` value.
- [ ] 2.4 Render primitive-contract fields into attributed build shards as part of the governed build contract, without adding a separate primitive-contract hash.
- [ ] 2.5 Ensure primitive contract changes require a new DesignEvidence/build-contract version and cannot be hidden inside retry or repair context.
- [ ] 2.6 Validate `difficulty_controls` as player-experience constraints, including intended mitigations, intended solve steps, and forbidden shortcuts, without treating them as source or binary proof.
- [ ] 2.7 Ensure the canonical build-contract hash changes when primitive id, primitive version, evidence requirements, or difficulty controls change.

## 3. Build and validation audit

- [ ] 3.1 Implement a host-owned pre-build source semantic gate for governed pwn BuildAttempts after implementation source exists and before image build.
- [ ] 3.1a Ensure the source semantic gate checks source-level primitive requirements and disqualifiers, including unsafe input paths, target functions/sinks, bounded reads, fixed secret checks, plaintext flag paths, and debug/backdoor paths.
- [ ] 3.1b Ensure source semantic gate pass is necessary but not sufficient: it may allow image build to continue, but cannot create accepted validation evidence or skip post-build artifact audit.
- [ ] 3.2 Implement post-build host validation semantic audit after source, final attachments, binaries, and solver/debug evidence exist.
- [ ] 3.2a Verify declared primitive requirements and disqualifiers against generated source, final attachments, binary metadata, and pwn debug/solver evidence using field-level `source_evidence`, `binary_evidence`, `solver_evidence`, and `dynamic_evidence`.
- [ ] 3.3 Ensure offsets, gadgets, libc facts, and other exploit constants are accepted only from artifact-derived evidence paths.
- [ ] 3.4 Fail primitive mismatch as validation/build failure without rewriting the primitive contract during retry or repair.
- [ ] 3.5 Write semantic-audit diagnostics into existing validation results/history with `primitive_id`, `primitive_version`, rule id, evidence path, source/artifact location when available, and a concise legacy-compatible `contract_errors` / `validation_contract_errors` string.
- [ ] 3.6 Persist semantic-audit outcome and primitive fingerprint material in ArtifactObservation `contract_checks` / `fingerprints`, bound to BuildAttempt, DesignEvidence/build-contract version, canonical `contract_sha256`, artifact manifest hash, and current host validation observation.
- [ ] 3.7 Ensure repair prompts and attempt-detail API surfaces expose semantic-audit failure details without replacing existing pwn debug and validation failure fields.
- [ ] 3.8 Add scoped observation-review policy for `pwn_primitive_inconclusive` supported-primitive cases, and prevent review acceptance for unsupported, disqualified, not-realized, challenge-escape, or stale observations.
- [ ] 3.9 Ensure semantic-audit failures block BuildAttempt success and production eligibility but still allow retry, revalidation, and repair that preserves the same primitive contract.
- [ ] 3.10 Add repair guidance that classifies the next action as implementation repair, artifact-derived evidence repair, revalidation, unsupported-primitive triage, or Design revision.

## 4. Release and regression coverage

- [ ] 4.1 Keep production release packaging behind the existing corpus-admission gate: pwn semantic acceptance must produce validation-layer effective acceptance, and corpus membership/member/aggregate decisions must still pass.
- [ ] 4.2 Add regression tests for bounded-read false positives against stack-overflow primitives.
- [ ] 4.2a Add pre-build source-gate tests proving bounded-read ret2win mismatches, fixed secret checks, plaintext flag paths, missing ret2win targets, and debug/backdoor paths fail before image build.
- [ ] 4.2b Add tests proving source-gate pass does not mark validation passed and still requires post-build binary/solver/debug evidence.
- [ ] 4.3 Add regression tests proving another valid primitive can still pass when a bounded read disqualifies only the overflow primitive.
- [ ] 4.4 Add tests proving Design output keeps the existing JSON fields and cannot replace them with only `primitive_id`.
- [ ] 4.5 Add retry/revalidate tests proving primitive contract changes require new DesignEvidence/build-contract versions.
- [ ] 4.5a Add retry/repair tests proving `pwn_primitive_evidence_missing` and `pwn_primitive_not_realized` can iterate under the same primitive contract without marking the failed observation successful.
- [ ] 4.6 Add legacy compatibility tests proving unguided hand-written shards without primitive contracts keep existing behavior.
- [ ] 4.7 Add tests proving fixed secret checks, plaintext flag exposure, debug/backdoor paths, and solver reads from organizer files produce `pwn_challenge_escape` and cannot satisfy memory-corruption primitive contracts.
- [ ] 4.8 Add tests proving governed inconclusive evidence fails closed unless an accepted scoped `observation_review_decision` is bound to the current ArtifactObservation for the same BuildAttempt, DesignEvidence/build-contract version, canonical `contract_sha256`, and artifact manifest hash.
- [ ] 4.8a Add tests proving observation review cannot override unsupported, disqualified, not-realized, challenge-escape, or stale-artifact evidence observations.
- [ ] 4.8b Add tests proving unsupported primitives may be triaged but cannot become a governed pass without a new supported primitive definition and new primitive contract.
- [ ] 4.9 Add release tests proving production packaging rejects stale validation evidence, missing corpus acceptance, and governed pwn binaries that would require recompilation or relinking.
- [ ] 4.10 Add regression tests for positive unsafe `read` overflow evidence, negative `fgets(sizeof(buf))` evidence, format-string sink argument control, ret2libc missing libc/base/gadget/offset evidence, and LLM/design-text offsets being ignored.
