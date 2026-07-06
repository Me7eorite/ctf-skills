## Why

Current pwn challenge generation can still let a declared exploit path drift away from the actual implementation. A design may intend stack overflow or ret2win, while the built source only contains bounded input or a fixed secret check, causing downstream repair to hallucinate offsets and exploits that the binary cannot support.

This change makes pwn exploitability a contract that Design declares and Build/host validation proves against real source and binary evidence. The system fails closed when the built artifact does not realize the declared pwn primitive.

This change depends on the governed DesignEvidence/build-contract and ArtifactObservation model introduced by `add-evidence-backed-design-and-implementation-diversity`; if that change is not available, this change must first provide the minimal equivalent binding fields before implementation.

## What Changes

- Add a pwn primitive contract model with declared `primitive_id`, positive `requires[]`, negative `disqualifiers[]`, field-level evidence schemas, explicit diagnostic precedence, difficulty controls, and evidence requirements.
- Extend structured pwn Design so it declares the intended primitive contract without claiming source-level proof before source exists.
- Add host-owned semantic audit during Build validation that inspects generated source, compiled artifacts, and solver evidence after implementation.
- Add explicit failure outcomes for non-realized pwn primitives, including `pwn_primitive_disqualified`, `pwn_primitive_evidence_missing`, `pwn_primitive_not_realized`, `pwn_primitive_unsupported`, `pwn_challenge_escape`, and `pwn_primitive_inconclusive`, without forcing a non-pwn logic challenge into a pwn solve path.
- Keep exploit offsets and final solver facts derived from artifact evidence, not from LLM design text.
- Bind release eligibility to the existing built artifact, primitive contract hash, artifact manifest hash, and host validation observation rather than adding a separate release rebuild.
- Preserve existing validation result/history and contract-error compatibility while adding structured semantic-audit diagnostics for operators, repair, and revalidation.
- Treat unsupported primitive ids as review/triage inputs only; manual review can accept inconclusive supported-primitive evidence, but it cannot convert unsupported, disqualified, non-realized, challenge-escape, or stale evidence into a governed pass.

## Capabilities

### New Capabilities
- `pwn-semantic-primitive-gating`: shared primitive-contract semantics, semantic-audit outcomes, and fail-closed pwn primitive realization rules.

### Modified Capabilities
- `structured-challenge-designs`: pwn Design output must declare a verifiable primitive contract as part of the build contract, while preserving the existing challenge JSON shape.
- `build-orchestration`: Build/host validation must prove the declared pwn primitive against actual implementation artifacts before a governed pwn BuildAttempt can succeed.

## Impact

Affected areas include pwn design validation, build contract rendering, shard payload construction, host validation, pwn debug/evidence collection, build-attempt diagnostics, and tests around pwn bounded-read false positives. This change intentionally does not move source generation into the Design stage and does not introduce a release-stage compiler path.
