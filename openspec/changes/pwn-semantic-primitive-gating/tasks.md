## 1. Primitive contract model

- [ ] 1.1 Define the versioned primitive library schema with `primitive_id`, `primitive_version`, `requires[]`, `disqualifiers[]`, field-level `evidence_schema`, `offset_policy`, `difficulty_controls`, and `diagnostic_precedence`.
- [ ] 1.2 Add first-version primitive definitions for `stack_overflow_ret2win_basic`, `format_string_controlled_printf_basic`, and `stack_overflow_with_leak_ret2libc` only, with ret2win requiring a concrete host-locatable control-flow target or bound dynamic control-flow observation.
- [ ] 1.3 Add negative safe-pattern and challenge-escape cases, including bounded reads, fixed secret checks, plaintext flag exposure, debug/backdoor paths, and stronger-than-declared primitive supersets.
- [ ] 1.4 Add diagnostic codes for `pwn_primitive_not_realized`, `pwn_primitive_disqualified`, `pwn_primitive_evidence_missing`, `pwn_primitive_unsupported`, `pwn_challenge_escape`, `pwn_primitive_inconclusive`, and stale-artifact evidence diagnostics.
- [ ] 1.5 Define deterministic diagnostic precedence: unsupported id, primitive-specific disqualifier hit, challenge escape, missing required evidence, non-equivalent primitive realization, inconclusive evidence, then generic not-realized fallback.

## 2. Design contract integration

- [ ] 2.1 Extend pwn Design validation to require a primitive contract while preserving the existing challenge JSON shape.
- [ ] 2.2 Use Design-stage field names that describe intent, such as `intended_implementation_features`, and reject Design-stage claims that depend on source, binary, debugger, runtime, or offset proof before Build exists.
- [ ] 2.3 Record no-valid-pwn-primitive outcomes as attempt diagnostics or review routing without adding a new `design_tasks.status` value.
- [ ] 2.4 Render primitive-contract fields into attributed build shards and governed build contracts.
- [ ] 2.5 Ensure primitive contract changes require a new DesignEvidence/build-contract version and cannot be hidden inside retry or repair context.
- [ ] 2.6 Validate `difficulty_controls` as player-experience constraints, including intended mitigations, intended solve steps, and forbidden shortcuts, without treating them as source or binary proof.

## 3. Build and validation audit

- [ ] 3.1 Implement host validation semantic audit for governed pwn BuildAttempts after source and binaries exist.
- [ ] 3.2 Verify declared primitive requirements and disqualifiers against generated source, final attachments, binary metadata, and pwn debug/solver evidence using field-level `source_evidence`, `binary_evidence`, `solver_evidence`, and `dynamic_evidence`.
- [ ] 3.3 Ensure offsets, gadgets, libc facts, and other exploit constants are accepted only from artifact-derived evidence paths.
- [ ] 3.4 Fail primitive mismatch as validation/build failure without rewriting the primitive contract during retry or repair.
- [ ] 3.5 Write semantic-audit diagnostics into existing validation results/history with `primitive_id`, `primitive_version`, rule id, evidence path, source/artifact location when available, and a concise legacy-compatible `contract_errors` / `validation_contract_errors` string.
- [ ] 3.6 Bind semantic-audit observations to BuildAttempt, DesignEvidence/build-contract version, primitive contract hash, artifact manifest hash, and host validation observation.
- [ ] 3.7 Ensure repair prompts and attempt-detail API surfaces expose semantic-audit failure details without replacing existing pwn debug and validation failure fields.

## 4. Release and regression coverage

- [ ] 4.1 Bind release packaging to accepted BuildAttempt evidence, primitive contract hash, artifact manifest hash, and validation or manual-review observation.
- [ ] 4.2 Add regression tests for bounded-read false positives against stack-overflow primitives.
- [ ] 4.3 Add regression tests proving another valid primitive can still pass when a bounded read disqualifies only the overflow primitive.
- [ ] 4.4 Add tests proving Design output keeps the existing JSON fields and cannot replace them with only `primitive_id`.
- [ ] 4.5 Add retry/revalidate tests proving primitive contract changes require new DesignEvidence/build-contract versions.
- [ ] 4.6 Add legacy compatibility tests proving unguided hand-written shards without primitive contracts keep existing behavior.
- [ ] 4.7 Add tests proving fixed secret checks, plaintext flag exposure, debug/backdoor paths, and solver reads from organizer files produce `pwn_challenge_escape` and cannot satisfy memory-corruption primitive contracts.
- [ ] 4.8 Add tests proving governed inconclusive evidence fails closed unless an explicit manual review observation is bound to the same BuildAttempt, DesignEvidence/build-contract version, primitive contract hash, and artifact manifest hash.
- [ ] 4.8a Add tests proving manual review cannot override unsupported, disqualified, not-realized, challenge-escape, or stale-artifact evidence observations.
- [ ] 4.9 Add release tests proving packaging rejects stale validation evidence and never recompiles or relinks governed pwn binaries.
- [ ] 4.10 Add regression tests for positive unsafe `read` overflow evidence, negative `fgets(sizeof(buf))` evidence, format-string sink argument control, ret2libc missing libc/base/gadget/offset evidence, and LLM/design-text offsets being ignored.
