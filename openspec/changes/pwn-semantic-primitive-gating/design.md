## Context

The existing system separates Design from Build. Design produces structured JSON, committed DesignEvidence, and a build contract. Build/Hermes then creates source, deployment files, solver artifacts, and binaries. Host validation and ArtifactObservation are the first places where the system can inspect real source and compiled output.

The earlier version of this proposal blurred that boundary by asking Design to inspect generated source or IR before source exists. This revision keeps the phase boundary intact: Design declares a pwn primitive contract; Build implements it; host validation proves or rejects it against the produced artifacts.

This proposal is layered on the now-existing governed DesignEvidence/build-contract, ArtifactObservation, observation-review, and production corpus-admission chain. It adds pwn primitive realization as a category-specific semantic contract within that chain; it does not introduce a parallel evidence store or release path.

## Goals / Non-Goals

**Goals:**
- Make pwn primitive intent explicit in the structured Design/build contract.
- Verify primitive realization in two stages: a pre-build source semantic gate after implementation writes source, then a post-build artifact semantic audit using binary metadata, solver evidence, and safe-pattern disqualifiers.
- Fail closed when implementation collapses into a non-pwn logic check, realizes a weaker/non-equivalent primitive, or lacks artifact-derived proof for the declared primitive.
- Allow repair/retry/revalidation to continue after semantic-audit failure, while preventing the failed observation from becoming governed success or production evidence.
- Preserve the current challenge JSON shape, build-attempt model, retry/revalidate flow, and host-owned validation boundary.
- Reuse the existing DesignEvidence/build-contract/ArtifactObservation direction instead of creating a second contract system.
- Keep the pwn primitive contract inside the existing canonical build contract so `contract_sha256` already covers primitive intent, difficulty controls, and evidence requirements.
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

Unsupported primitive ids are rejected by Design validation before governed BuildAttempt creation when the current host primitive library is available. Human triage may decide whether to add a new primitive definition or move the task out of governed pwn automation. That triage is not a governed pass. Build/host semantic audit still needs a `pwn_primitive_unsupported` outcome for stale, legacy, imported, or externally submitted governed attempts that carry an unsupported primitive id.

For the first version, `stack_overflow_ret2win_basic` requires a concrete control-flow target: a symbolized or otherwise host-locatable `win`/`print_flag`/`read_flag`-style function, or a host-generated dynamic control-flow observation bound to the accepted artifact. A generic hidden flag path is not enough unless the observation proves player-controlled control-flow transfer; fixed secret checks and debug commands are challenge escapes, not ret2win evidence.

Each primitive definition carries a minimum evidence envelope:

- `source_evidence`: source path, rule id, line or span when available, player-controlled input source, sink, target object, and bound analysis result.
- `binary_evidence`: final player artifact path, artifact sha256, relevant symbols, checksec/mitigation facts, and any shipped library identity required by the primitive.
- `solver_evidence`: solver path, final artifact sha256 used by the solver, observed flag capture, and a statement that solver facts came from artifact-derived evidence.
- `dynamic_evidence`: required only when the primitive's `offset_policy` or leak policy needs it; includes probe command or report path, derived offset/leak/gadget/base facts, and bounded execution result.

`difficulty_controls` are player-experience constraints, not proof. They describe intended mitigations, intended solve steps, and forbidden shortcuts such as fixed passwords, plaintext flag exposure, debug/backdoor commands, solver reads from organizer files, or artifact/source mismatch.

Stronger-than-declared primitive supersets, such as arbitrary write in a declared ret2win challenge, are treated as policy-sensitive semantic mismatches. The first version SHALL mark them `pwn_primitive_inconclusive` with a difficulty-policy detail unless the primitive definition explicitly lists that superset as allowed or as a hard disqualifier. A pwn-semantic scoped observation review may accept only this inconclusive supported-primitive case; it may not accept challenge escapes, unsupported primitive ids, disqualifier hits, non-realized primitives, or stale evidence.

## Contract Placement and Versioning

The pwn primitive contract lives under the governed build contract, for example as `build_contract.pwn_primitive_contract`. Contract fields such as `evidence_requirements`, `offset_policy`, and primitive version are a host-rendered snapshot or reference to the versioned primitive definition; Design may choose the supported primitive and describe intent, but it cannot redefine evidence semantics. Validation rejects contracts whose copied evidence/offset fields disagree with the host-owned primitive definition. The canonical build-contract hash includes this nested object, so the first version SHALL NOT add a separate database-level `primitive_contract_sha256`. If the primitive intent changes, the build contract changes and the system requires a new DesignEvidence/build-contract version.

The versioned primitive library SHALL be host-owned code, with an initial module such as `src/domain/pwn_primitives.py`. Design prompt rendering may expose the supported primitive ids, versions, and intent/evidence field names from that host-owned library, but Design output cannot define or override primitive semantics. Build validation and tests consume the same host-owned definitions.

Semantic audit results are written into existing validation-layer surfaces:

- `validation_failure_details` and legacy-compatible `contract_errors` / `validation_contract_errors` for operator and repair visibility;
- ArtifactObservation `contract_checks` for the semantic-audit outcome, diagnostic code, primitive id/version, rule id, and evidence paths;
- ArtifactObservation `fingerprints` for primitive id/version and primitive-intent fingerprint material that participates in existing corpus comparison without becoming a separate acceptance key.

The binding hash is the existing ArtifactObservation `artifact_manifest_sha256`, produced from the output manifest hash machinery, not a new pwn-specific artifact hash.

The pwn-semantic review scope is separate from the existing production publication scope. A suggested scope name is `pwn-semantic-primitive`. This scope may contribute validation-layer effective acceptance only for `pwn_primitive_inconclusive` observations on supported primitives; production packaging still requires the existing corpus-admission and publication review gates.

## Two-Stage Semantic Gating

Pwn primitive validation has two host-owned stages:

1. `pre_build_source_semantic_gate` runs after Build/implement has produced source files such as `xx.c`, but before image build. It should extend or wrap the existing host pre-build contract gate rather than creating a parallel pre-build lifecycle. It checks source-level necessary conditions for the declared primitive and challenge-escape disqualifiers. Examples: attacker-controlled input reaches an unsafe stack sink, a ret2win target is present, a format-string sink uses player-controlled bytes as the format argument, and the implementation is not merely a fixed password, debug command, plaintext flag path, or safely bounded read. This stage can fail early with semantic diagnostics and repair guidance.
2. `post_build_artifact_semantic_audit` runs after binaries, attachments, and solver/debug evidence exist. It checks compiled artifact identity, symbols/mitigations, final attachment hashes, artifact-derived offsets/gadgets/libc facts when required, and solver evidence bound to the accepted artifact.

The pre-build source gate is a necessary-condition gate, not a final proof. Passing it does not make a BuildAttempt successful and does not replace binary/solver validation. Failing it blocks image build for that attempt until repair or Design revision resolves the mismatch.

## Decisions

1. Put primitive declaration in Design, proof in Build validation.
   Alternative: run semantic admissibility before primitive selection in Design. Rejected because current Design has no source or binary to inspect.

2. Extend the build contract instead of creating an unrelated locked-binary contract.
   Alternative: introduce a separate signed contract around a design-time build. Rejected because current governed work already binds DesignEvidence, canonical build-contract hash, ArtifactObservation, and its `artifact_manifest_sha256`. The primitive contract is part of the canonical build contract.

3. Treat bounded safe patterns as disqualifiers for specific primitives, not as global proof that no pwn exists.
   Alternative: any bounded read produces `NO_VALID_PWN_PRIMITIVE`. Rejected because the same program may still contain another valid primitive.

4. Use failure codes instead of a new design-task lifecycle state.
   Alternative: add `NO_VALID_PWN_PRIMITIVE` as a persistent task status. Rejected because current lifecycle already separates design failure, build failure, and human review through attempt diagnostics.

5. Keep solver facts artifact-derived.
   Alternative: let Design or LLM output offsets and exploit details. Rejected because this recreates the original hallucination vector.

6. Make diagnostic precedence explicit.
   Unsupported ids produce `pwn_primitive_unsupported`; stale or unbound artifact evidence produces `pwn_primitive_stale_evidence`; challenge escapes such as fixed secrets, plaintext flags, debug/backdoors, or organizer-file solver paths produce `pwn_challenge_escape`; primitive-specific safe-pattern disqualifier hits produce `pwn_primitive_disqualified`; missing required evidence produces `pwn_primitive_evidence_missing`; realized non-equivalent or non-pwn challenge types produce `pwn_primitive_not_realized`; true uncertainty produces `pwn_primitive_inconclusive`.

7. Keep manual acceptance outside the normal pass path.
   If inconclusive supported-primitive evidence can be accepted by a human, it must be recorded as an accepted `observation_review_decision` with a pwn-semantic scope such as `pwn-semantic-primitive`, bound to the current ArtifactObservation. The underlying observation remains `inconclusive`; effective acceptance is derived only when the BuildAttempt, DesignEvidence/build-contract version, canonical `contract_sha256`, ArtifactObservation `artifact_manifest_sha256`, reviewer, scope, and rationale all match policy. Review may accept only inconclusive evidence; it may not override unsupported primitives, challenge escapes, disqualifiers, non-realized primitives, or stale artifact evidence. The existing `production-publication` scope is not sufficient for pwn semantic acceptance.

8. Route unsupported primitives to triage, not governed acceptance.
   Unsupported ids produce `pwn_primitive_unsupported`. A reviewer may decide to author a new versioned primitive definition or reclassify the task outside governed pwn automation, but may not mark the existing unsupported contract successful.

9. Failure blocks acceptance, not iteration.
   Semantic-audit failures are normal validation/build failures. Retry, revalidation, and automatic repair may continue when they preserve the original primitive contract and try to add missing artifact-derived evidence or fix implementation drift. If the primitive intent must change, that is a Design revision with a new DesignEvidence/build-contract version, not a Build repair mutation.

10. Add source semantic gating before image build.
   Alternative: wait until final validation to discover all primitive mismatches. Rejected because source-only mismatches such as safe bounded reads, fixed secrets, missing ret2win targets, or plaintext flag paths are cheap to detect before building containers. The source gate remains limited to source-level necessary conditions; final exploitability still requires post-build artifact evidence.

## Semantic Audit Flow

```text
Design
  declares primitive contract and intended implementation features
  cannot provide source, binary, debugger, or offset proof

Build
  creates implementation source and deployment files
  runs pre-build source semantic gate before image build
  creates binaries, solver, and metadata only after the source gate passes
  preserves the original primitive contract for retry/repair

Host validation
  checks post-build artifact requirements and any source findings carried from the source gate
  checks binary metadata, final attachments, and pwn debug evidence
  checks solver facts only from artifact-derived evidence paths
  writes structured diagnostics to validation results/history
  preserves legacy contract-error compatibility while adding structured validation failure details

Release packaging
  bundles only the accepted BuildAttempt artifacts
  relies on the existing production corpus gate, which requires an effectively accepted ArtifactObservation, matching canonical contract hash, matching ArtifactObservation `artifact_manifest_sha256`, and accepted corpus membership/aggregate decisions
  never recompiles or relinks the binary
```

## Risks / Trade-offs

- [Risk] Primitive verification may initially cover only common pwn patterns. -> Mitigation: version primitive definitions, ship a small first-version subset, and fail inconclusive governed builds closed unless a pwn-semantic scoped observation-review decision exists.
- [Risk] A valid but unusual exploit may be rejected. -> Mitigation: route unsupported primitive contracts to human triage and add a new primitive definition with tests before accepting it in governed automation.
- [Risk] Host validation may need richer source/binary discovery. -> Mitigation: start with declared source paths, final attachment metadata, existing pwn debug reports, and bounded negative tests.
- [Risk] Build repair may try to rewrite the design intent. -> Mitigation: require a new DesignEvidence version for primitive-contract changes and verify retries keep the original contract.
- [Risk] Operators may read fail-closed diagnostics as "do not retry". -> Mitigation: diagnostics and repair prompts state whether the next action is implementation repair, evidence repair, revalidation, unsupported-primitive triage, or Design revision.
- [Risk] Existing legacy pwn shards may not carry a primitive contract. -> Mitigation: keep legacy behavior for unguided shards and apply this gate only to governed pwn BuildAttempts.
- [Risk] Primitive-gating can overfit to signature checks and miss challenge escapes. -> Mitigation: explicitly audit fixed secret checks, plaintext flag exposure, and debug/backdoor paths as challenge-escape diagnostics; report stronger-than-declared primitive supersets as difficulty-policy inconclusive unless a primitive definition says otherwise.

## Migration Plan

1. Add primitive-contract fields to pwn Design/build contract validation while preserving the existing JSON shape.
2. Render primitive-contract fields into attributed build shards.
3. Add host validation semantic audit for governed pwn attempts.
4. Emit structured failure codes for disqualifier hits, missing evidence, unsupported primitives, primitive mismatch, and challenge-type escape paths.
5. Bind accepted observations to BuildAttempt, DesignEvidence/build-contract version, canonical `contract_sha256`, current ArtifactObservation, and its `artifact_manifest_sha256`.
6. Use pwn-semantic scoped `observation_review_decision` rows only for inconclusive supported-primitive acceptance, and keep production packaging behind the existing corpus gate and publication scopes.
7. Preserve repair/retry/revalidation paths for semantic-audit failures while requiring Design revision for primitive intent changes.
8. Add regression tests for bounded-read false positives, accepted unsafe primitive evidence, retry/revalidate behavior, observation-review binding, production corpus eligibility, and legacy compatibility.
