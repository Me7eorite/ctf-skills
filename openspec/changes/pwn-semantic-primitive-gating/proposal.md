## Why

Current pwn challenge generation can still let a declared exploit path drift away from the actual implementation. A design may intend stack overflow or ret2win, while the built source only contains bounded input or a fixed secret check, causing downstream repair to hallucinate offsets and exploits that the binary cannot support.

This change makes pwn exploitability a contract that Design declares and Build/host validation proves against real source and binary evidence. The system fails closed when the built artifact does not realize the declared pwn primitive.

The governed DesignEvidence/build-contract, ArtifactObservation, observation-review, and production corpus-admission chain is now the base layer for this work. This change extends that chain for pwn-specific primitive realization instead of adding a second binding, review, or release system.

Failing closed does not mean the task stops iterating. It means the current BuildAttempt cannot be marked successful, reused as accepted validation evidence, or published until the primitive is proven. Repair and retry may continue against the same primitive contract; if the intended primitive itself is wrong, the operator must create a new DesignEvidence/build-contract version instead of silently rewriting the BuildAttempt.

## Problem This Solves

Pwn tasks are uniquely vulnerable to semantic collapse during Build: a valid-looking design can say "ret2win" or "ret2libc", while the delivered binary is only a password check, a bounded read, a debug backdoor, or a solver that relies on constants invented by an LLM. Existing artifact and flag validation can prove that a solver prints the flag, but not that the intended memory-corruption primitive actually exists in the accepted artifact.

This proposal closes that gap by making the exploit primitive a verifiable build contract field. The intended primitive is declared before source exists; after implementation writes source such as `xx.c`, a pre-build source semantic gate checks that the source still matches the declared vulnerability design before spending time on image build. Final proof still comes after Build produces binaries and solver/debug evidence.

## What Changes

- Add a pwn primitive contract model under the existing build contract with declared `primitive_id`, positive `requires[]`, negative `disqualifiers[]`, field-level evidence schemas, explicit diagnostic precedence, difficulty controls, and evidence requirements.
- Extend structured pwn Design so it declares the intended primitive contract without claiming source-level proof before source exists.
- Add a host-owned pre-build source semantic gate after implementation source exists and before image build, so obvious primitive mismatches are rejected early.
- Add host-owned artifact semantic audit during Build validation that inspects generated source, compiled artifacts, and solver evidence after implementation.
- Add explicit failure outcomes for non-realized pwn primitives, including `pwn_primitive_disqualified`, `pwn_primitive_evidence_missing`, `pwn_primitive_not_realized`, `pwn_primitive_unsupported`, `pwn_challenge_escape`, and `pwn_primitive_inconclusive`, without forcing a non-pwn logic challenge into a pwn solve path.
- Keep semantic-audit failures repairable: automatic repair may fix implementation artifacts, solver/debug evidence, or missing artifact-derived proof while preserving the original primitive contract.
- Keep exploit offsets and final solver facts derived from artifact evidence, not from LLM design text.
- Bind primitive acceptance to the existing `contract_sha256`, artifact manifest hash, current ArtifactObservation, and scoped observation-review decision rather than adding a separate primitive hash or release rebuild.
- Preserve production release behavior through the existing corpus-admission gate; pwn primitive acceptance is a validation-layer prerequisite, not an alternate production gate.
- Preserve existing validation result/history and contract-error compatibility while adding structured semantic-audit diagnostics for operators, repair, and revalidation.
- Treat unsupported primitive ids as review/triage inputs only; a scoped observation-review decision can accept inconclusive supported-primitive evidence, but it cannot convert unsupported, disqualified, non-realized, challenge-escape, or stale evidence into a governed pass.

## Capabilities

### New Capabilities
- `pwn-semantic-primitive-gating`: shared primitive-contract semantics, semantic-audit outcomes, and fail-closed pwn primitive realization rules.

### Modified Capabilities
- `structured-challenge-designs`: pwn Design output must declare a verifiable primitive contract as part of the build contract, while preserving the existing challenge JSON shape.
- `build-orchestration`: Build/host validation must prove the declared pwn primitive against actual implementation artifacts before a governed pwn BuildAttempt can succeed.

## Impact

Affected areas include pwn design validation, build contract rendering, shard payload construction, host validation, pwn debug/evidence collection, ArtifactObservation contract checks/fingerprints, build-attempt diagnostics, observation-review policy, production corpus eligibility, and tests around pwn bounded-read false positives. This change intentionally does not move source generation into the Design stage and does not introduce a release-stage compiler path.
