## Context

The existing system separates Design from Build. Design produces structured JSON and, in newer governed flows, DesignEvidence plus a build contract. Build/Hermes then creates source, deployment files, solver artifacts, and binaries. Host validation and ArtifactObservation are the first places where the system can inspect real source and compiled output.

The earlier version of this proposal blurred that boundary by asking Design to inspect generated source or IR before source exists. This revision keeps the phase boundary intact: Design declares a pwn primitive contract; Build implements it; host validation proves or rejects it against the produced artifacts.

## Goals / Non-Goals

**Goals:**
- Make pwn primitive intent explicit in the structured Design/build contract.
- Verify primitive realization after Build using real source, binary metadata, solver evidence, and safe-pattern disqualifiers.
- Fail closed when implementation collapses into a non-pwn logic check or otherwise misses the declared primitive.
- Preserve the current challenge JSON shape, build-attempt model, retry/revalidate flow, and host-owned validation boundary.
- Reuse the existing DesignEvidence/build-contract/ArtifactObservation direction instead of creating a second contract system.

**Non-Goals:**
- No source or binary generation during structured Design.
- No release-stage recompilation or shadow-build toolchain.
- No LLM-supplied raw offsets, gadgets, libc bases, or final exploit facts.
- No attempt to prove every possible unintended exploit path; this verifies the declared pwn primitive and known disqualifiers.
- No new `design_tasks.status` enum for `NO_VALID_PWN_PRIMITIVE`.

## Decisions

1. Put primitive declaration in Design, proof in Build validation.
   Alternative: run semantic admissibility before primitive selection in Design. Rejected because current Design has no source or binary to inspect.

2. Extend the build contract instead of creating an unrelated locked-binary contract.
   Alternative: introduce a separate signed contract around a design-time build. Rejected because current governed work already binds DesignEvidence, contract hash, artifact manifest, and ArtifactObservation.

3. Treat bounded safe patterns as disqualifiers for specific primitives, not as global proof that no pwn exists.
   Alternative: any bounded read produces `NO_VALID_PWN_PRIMITIVE`. Rejected because the same program may still contain another valid primitive.

4. Use failure codes instead of a new design-task lifecycle state.
   Alternative: add `NO_VALID_PWN_PRIMITIVE` as a persistent task status. Rejected because current lifecycle already separates design failure, build failure, and human review through attempt diagnostics.

5. Keep solver facts artifact-derived.
   Alternative: let Design or LLM output offsets and exploit details. Rejected because this recreates the original hallucination vector.

## Risks / Trade-offs

- [Risk] Primitive verification may initially cover only common pwn patterns. -> Mitigation: version primitive definitions and fail inconclusive cases closed for governed pwn builds.
- [Risk] A valid but unusual exploit may be rejected. -> Mitigation: route unsupported primitive contracts to human review or add a new primitive definition with tests.
- [Risk] Host validation may need richer source/binary discovery. -> Mitigation: start with declared source paths, final attachment metadata, existing pwn debug reports, and bounded negative tests.
- [Risk] Build repair may try to rewrite the design intent. -> Mitigation: require a new DesignEvidence version for primitive-contract changes.
- [Risk] Existing legacy pwn shards may not carry a primitive contract. -> Mitigation: keep legacy behavior for unguided shards and apply this gate only to governed pwn BuildAttempts.

## Migration Plan

1. Add primitive-contract fields to pwn Design/build contract validation while preserving the existing JSON shape.
2. Render primitive-contract fields into attributed build shards.
3. Add host validation semantic audit for governed pwn attempts.
4. Emit structured failure codes for disqualifier hits, missing evidence, and primitive mismatch.
5. Add regression tests for bounded-read false positives, accepted unsafe primitive evidence, retry/revalidate behavior, and legacy compatibility.

## Open Questions

- Which pwn primitive definitions should ship in the first version: stack overflow, format string, integer/OOB, heap, ret2win, or only the common low-risk subset?
- Should inconclusive primitive evidence always fail governed pwn builds, or can an explicit observation review accept rare cases?
- Where should the versioned primitive library live so Design, Build, validation, and tests consume the same definitions?
