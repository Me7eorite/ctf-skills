## Context

The existing system separates Design from Build. Design produces structured JSON and, in newer governed flows, DesignEvidence plus a build contract. Build/Hermes then creates source, deployment files, solver artifacts, and binaries. Host validation and ArtifactObservation are the first places where the system can inspect real source and compiled output.

The earlier version of this proposal blurred that boundary by asking Design to inspect generated source or IR before source exists. This revision keeps the phase boundary intact: Design declares a pwn primitive contract; Build implements it; host validation proves or rejects it against the produced artifacts.

This proposal is layered on the still-active `add-evidence-backed-design-and-implementation-diversity` change. It uses that change's DesignEvidence/build-contract versioning and ArtifactObservation binding model. If implemented first, this change must create only the minimal compatible binding fields it needs, then converge on the shared model when the prerequisite lands.

## Goals / Non-Goals

**Goals:**
- Make pwn primitive intent explicit in the structured Design/build contract.
- Verify primitive realization after Build using real source, binary metadata, solver evidence, and safe-pattern disqualifiers.
- Fail closed when implementation collapses into a non-pwn logic check, realizes a weaker/non-equivalent primitive, or lacks artifact-derived proof for the declared primitive.
- Preserve the current challenge JSON shape, build-attempt model, retry/revalidate flow, and host-owned validation boundary.
- Reuse the existing DesignEvidence/build-contract/ArtifactObservation direction instead of creating a second contract system.
- Keep first-version primitive coverage intentionally small and testable, then add more primitive definitions as separately versioned library updates.

**Non-Goals:**
- No source or binary generation during structured Design.
- No release-stage recompilation or shadow-build toolchain.
- No LLM-supplied raw offsets, gadgets, libc bases, or final exploit facts.
- No attempt to prove every possible unintended exploit path; this verifies the declared pwn primitive, known disqualifiers, and challenge-type escape hatches.
- No new `design_tasks.status` enum for `NO_VALID_PWN_PRIMITIVE`.
- No first-version support for complex heap/OOB/integer primitives unless their evidence rules are defined with the same precision as the common subset.
- No manual-review override for hard semantic failures such as unsupported primitive ids, disqualifier hits, challenge escapes, non-realized primitives, or stale evidence.

## First-Version Primitive Scope

Ship only primitives whose positive and negative evidence can be checked deterministically enough for governed builds:

1. `stack_overflow_ret2win_basic`: attacker-controlled input reaches a fixed-size stack object through an unsafe or inadequately bounded write, and the binary exposes a concrete control-flow target such as a symbolized or host-locatable `win`/`print_flag`/`read_flag` function, or a host-generated dynamic control-flow observation bound to the accepted artifact.
2. `format_string_controlled_printf_basic`: attacker-controlled bytes reach a format-string sink as the format argument, and the solve path uses an artifact-derived leak/write plan rather than a hardcoded secret.
3. `stack_overflow_with_leak_ret2libc`: attacker-controlled overflow plus artifact-derived leak, libc/base, gadget, and offset evidence from the accepted binary and shipped libraries.

Heap, integer/OOB, SROP, JOP, and custom allocator primitives remain unsupported until their `requires[]`, `disqualifiers[]`, and evidence schemas are versioned and regression-tested.

Unsupported primitive ids may be routed to human triage so an operator can decide whether to add a new primitive definition or move the task out of governed pwn automation. That triage is not a governed pass.

For the first version, `stack_overflow_ret2win_basic` requires a concrete control-flow target: a symbolized or otherwise host-locatable `win`/`print_flag`/`read_flag`-style function, or a host-generated dynamic control-flow observation bound to the accepted artifact. A generic hidden flag path is not enough unless the observation proves player-controlled control-flow transfer; fixed secret checks and debug commands are challenge escapes, not ret2win evidence.

Each primitive definition carries a minimum evidence envelope:

- `source_evidence`: source path, rule id, line or span when available, player-controlled input source, sink, target object, and bound analysis result.
- `binary_evidence`: final player artifact path, artifact sha256, relevant symbols, checksec/mitigation facts, and any shipped library identity required by the primitive.
- `solver_evidence`: solver path, final artifact sha256 used by the solver, observed flag capture, and a statement that solver facts came from artifact-derived evidence.
- `dynamic_evidence`: required only when the primitive's `offset_policy` or leak policy needs it; includes probe command or report path, derived offset/leak/gadget/base facts, and bounded execution result.

`difficulty_controls` are player-experience constraints, not proof. They describe intended mitigations, intended solve steps, and forbidden shortcuts such as fixed passwords, plaintext flag exposure, debug/backdoor commands, solver reads from organizer files, or artifact/source mismatch.

Stronger-than-declared primitive supersets, such as arbitrary write in a declared ret2win challenge, are treated as policy-sensitive semantic mismatches. The first version SHALL mark them `pwn_primitive_inconclusive` with a difficulty-policy detail unless the primitive definition explicitly lists that superset as allowed or as a hard disqualifier. A bound manual review may accept only this inconclusive supported-primitive case; it may not accept challenge escapes or unsupported primitive ids.

## Decisions

1. Put primitive declaration in Design, proof in Build validation.
   Alternative: run semantic admissibility before primitive selection in Design. Rejected because current Design has no source or binary to inspect.

2. Extend the build contract instead of creating an unrelated locked-binary contract.
   Alternative: introduce a separate signed contract around a design-time build. Rejected because current governed work already binds DesignEvidence/build-contract version, primitive contract hash, artifact manifest hash, and ArtifactObservation.

3. Treat bounded safe patterns as disqualifiers for specific primitives, not as global proof that no pwn exists.
   Alternative: any bounded read produces `NO_VALID_PWN_PRIMITIVE`. Rejected because the same program may still contain another valid primitive.

4. Use failure codes instead of a new design-task lifecycle state.
   Alternative: add `NO_VALID_PWN_PRIMITIVE` as a persistent task status. Rejected because current lifecycle already separates design failure, build failure, and human review through attempt diagnostics.

5. Keep solver facts artifact-derived.
   Alternative: let Design or LLM output offsets and exploit details. Rejected because this recreates the original hallucination vector.

6. Make diagnostic precedence explicit.
   Unsupported ids produce `pwn_primitive_unsupported`; primitive-specific disqualifier hits produce `pwn_primitive_disqualified`; challenge escapes such as fixed secrets, plaintext flags, debug/backdoors, or organizer-file solver paths produce `pwn_challenge_escape`; missing required evidence produces `pwn_primitive_evidence_missing`; realized non-equivalent or non-pwn challenge types produce `pwn_primitive_not_realized`; true uncertainty produces `pwn_primitive_inconclusive`.

7. Keep manual acceptance outside the normal pass path.
   If inconclusive evidence can be accepted by a human, it must create an explicit review observation bound to the BuildAttempt, DesignEvidence/build-contract version, primitive contract hash, artifact manifest hash, reviewer, and rationale. Without that observation, governed pwn builds fail closed. Review may accept only inconclusive evidence; it may not override unsupported primitives, disqualifiers, non-realized primitives, challenge escapes, or stale artifact evidence.

8. Route unsupported primitives to triage, not governed acceptance.
   Unsupported ids produce `pwn_primitive_unsupported`. A reviewer may decide to author a new versioned primitive definition or reclassify the task outside governed pwn automation, but may not mark the existing unsupported contract successful.

## Semantic Audit Flow

```text
Design
  declares primitive contract and intended implementation features
  cannot provide source, binary, debugger, or offset proof

Build
  creates implementation artifacts, binaries, solver, and metadata
  preserves the original primitive contract for retry/repair

Host validation
  checks source-level requirements and disqualifiers
  checks binary metadata, final attachments, and pwn debug evidence
  checks solver facts only from artifact-derived evidence paths
  writes structured diagnostics to validation results/history
  preserves legacy contract-error compatibility while adding structured validation failure details

Release packaging
  bundles only the accepted BuildAttempt artifacts
  requires matching primitive contract hash, artifact manifest hash, and validation/review observation
  never recompiles or relinks the binary
```

## Risks / Trade-offs

- [Risk] Primitive verification may initially cover only common pwn patterns. -> Mitigation: version primitive definitions, ship a small first-version subset, and fail inconclusive governed builds closed unless a bound manual review observation exists.
- [Risk] A valid but unusual exploit may be rejected. -> Mitigation: route unsupported primitive contracts to human triage and add a new primitive definition with tests before accepting it in governed automation.
- [Risk] Host validation may need richer source/binary discovery. -> Mitigation: start with declared source paths, final attachment metadata, existing pwn debug reports, and bounded negative tests.
- [Risk] Build repair may try to rewrite the design intent. -> Mitigation: require a new DesignEvidence version for primitive-contract changes and verify retries keep the original contract.
- [Risk] Existing legacy pwn shards may not carry a primitive contract. -> Mitigation: keep legacy behavior for unguided shards and apply this gate only to governed pwn BuildAttempts.
- [Risk] Primitive-gating can overfit to signature checks and miss challenge escapes. -> Mitigation: explicitly audit fixed secret checks, plaintext flag exposure, and debug/backdoor paths as challenge-escape diagnostics; report stronger-than-declared primitive supersets as difficulty-policy inconclusive unless a primitive definition says otherwise.

## Migration Plan

1. Add primitive-contract fields to pwn Design/build contract validation while preserving the existing JSON shape.
2. Render primitive-contract fields into attributed build shards.
3. Add host validation semantic audit for governed pwn attempts.
4. Emit structured failure codes for disqualifier hits, missing evidence, unsupported primitives, primitive mismatch, and challenge-type escape paths.
5. Bind accepted observations to BuildAttempt, DesignEvidence/build-contract version, primitive contract hash, and artifact manifest hash.
6. Add regression tests for bounded-read false positives, accepted unsafe primitive evidence, retry/revalidate behavior, manual review binding, and legacy compatibility.

## Remaining Open Question

- Where should the versioned primitive library live so Design, Build, validation, and tests consume the same definitions?
