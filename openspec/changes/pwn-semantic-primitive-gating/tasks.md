## 1. Primitive contract model

- [ ] 1.1 Define the versioned primitive library schema with `primitive_id`, `requires[]`, `disqualifiers[]`, `evidence_schema`, and `offset_policy`.
- [ ] 1.2 Add first-version primitive definitions and negative safe-pattern cases for the supported pwn subset.
- [ ] 1.3 Add diagnostic codes for `pwn_primitive_not_realized`, `pwn_primitive_disqualified`, `pwn_primitive_evidence_missing`, and `pwn_primitive_unsupported`.

## 2. Design contract integration

- [ ] 2.1 Extend pwn Design validation to require a primitive contract while preserving the existing challenge JSON shape.
- [ ] 2.2 Reject Design-stage claims that depend on source, binary, debugger, or runtime proof before Build exists.
- [ ] 2.3 Record no-valid-pwn-primitive outcomes as attempt diagnostics or review routing without adding a new `design_tasks.status` value.
- [ ] 2.4 Render primitive-contract fields into attributed build shards and governed build contracts.

## 3. Build and validation audit

- [ ] 3.1 Implement host validation semantic audit for governed pwn BuildAttempts after source and binaries exist.
- [ ] 3.2 Verify declared primitive requirements and disqualifiers against generated source, final attachments, binary metadata, and pwn debug/solver evidence.
- [ ] 3.3 Ensure offsets, gadgets, libc facts, and other exploit constants are accepted only from artifact-derived evidence paths.
- [ ] 3.4 Fail primitive mismatch as validation/build failure without rewriting the primitive contract during retry or repair.

## 4. Release and regression coverage

- [ ] 4.1 Bind release packaging to accepted BuildAttempt evidence, contract hash, artifact manifest, and validation observation.
- [ ] 4.2 Add regression tests for bounded-read false positives against stack-overflow primitives.
- [ ] 4.3 Add regression tests proving another valid primitive can still pass when a bounded read disqualifies only the overflow primitive.
- [ ] 4.4 Add tests proving Design output keeps the existing JSON fields and cannot replace them with only `primitive_id`.
- [ ] 4.5 Add retry/revalidate tests proving primitive contract changes require new DesignEvidence/build-contract versions.
- [ ] 4.6 Add legacy compatibility tests proving unguided hand-written shards without primitive contracts keep existing behavior.
