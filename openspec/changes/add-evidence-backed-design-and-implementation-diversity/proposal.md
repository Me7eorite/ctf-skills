## Why

The current pipeline has improved semantic diversity at research/planning time,
but it still does not govern **how a challenge is solved or implemented**.
Different labels can converge during authoring/build into the same practical
shape (for example C + ELF + XOR-hidden flag + one conditional branch + a
Python byte scanner). The current boundaries allow that convergence:

- `design_tasks.diversity_flags` records family/sub-technique, but no governed
  solve or implementation profile.
- each Design attempt receives research evidence for one task, but not a
  machine-readable ledger of sibling/historical designs it must differ from;
- important implementation choices remain optional in `ChallengeDesign`, and
  `BuildOrchestrationService._matrix_values` falls back to defaults such as
  `language = c` and `target_format = elf`;
- `quality_gate_passed` is persisted and displayed but does not prevent build
  submission;
- Build receives the full design, but host validation only proves artifact
  shape, solver integrity, and flag recovery. It does not prove that the
  declared asset flow is necessary, that declared shortcuts are blocked, or
  that the built artifact matches the intended implementation profile;
- there is no batch/history corpus gate for source, solver, solve-profile, or
  implementation-profile repetition.

This makes the current system suitable for controlled trial production, but
not for unattended expansion toward hundreds of challenges.

## What Changes

- **Modify `research-planning`** so the diversity floor counts only designable
  findings (`kind in {technique, variant}`). Scenario/prerequisite findings
  remain evidence, but cannot satisfy the sub-technique diversity floor.
- **Modify `design-task-planning`** to reserve a deterministic, structured
  design profile before Design runs. The profile covers four axes:
  `semantic`, `solve`, `implementation`, and `presentation`. Reservation is
  batch-aware and history-aware, uses quotas/cooldowns, and is persisted so
  parallel Design workers consume non-overlapping design space.
- **Modify `structured-challenge-designs`** so Design receives current research
  evidence plus a bounded ledger summary of sibling and historically similar
  designs. A valid Design must produce:
  - evidence linking claims to real research findings;
  - a distinctness claim comparing the design with real ledger entries;
  - a structured `build_contract`;
  - executable acceptance and negative-test declarations.
- **Modify `build-orchestration`** so Build acts as a construction layer:
  it consumes the committed `build_contract`, cannot change governed fields,
  cannot silently fall back to generic language/format/flag mechanisms, and
  returns `design_unbuildable` when the contract cannot be implemented.
  Build submission is blocked unless the latest design passed its quality gate
  and has committed design evidence.
- **Modify host validation** to persist an `artifact_observation`, compare
  observed implementation facts with the build contract, run declared negative
  tests, and reject artifacts whose intended asset/capability chain is
  bypassable.
- **Modify `hermes-execution-protocol`** so validation and reconciliation
  require an observation bound to the exact BuildAttempt, DesignEvidence,
  contract hash, and artifact hash. Resume/all-skipped paths may reuse only a
  matching observation.
- **Add `challenge-corpus-governance`**: a batch/history quality gate using
  semantic, solve, implementation, source, solver, and intended-path
  fingerprints. Exact governed-profile duplicates are blocked; configurable
  similarity thresholds produce review-required or blocking outcomes.
- **Modify `delivery-bundle`** so production packing requires a passing
  ArtifactObservation and corpus-admission decision; passed metadata alone is
  insufficient. Shadow/trial bundles are explicit and marked non-production.
- **Add dashboard/API visibility** for reservations, design evidence, contract
  checks, observed profiles, and corpus-gate findings. The UI displays
  authoritative server results and does not recompute fingerprints or policy.

## Capabilities

### Modified Capabilities

- `research-planning`
- `design-task-planning`
- `structured-challenge-designs`
- `build-orchestration`
- `hermes-execution-protocol`
- `delivery-bundle`

### New Capabilities

- `challenge-corpus-governance`

## Impact

- **Database**:
  - new `design_profile_reservations`;
  - new `design_evidence`;
  - new `artifact_observations`;
  - new `design_profile_ledgers`;
  - new corpus batch, membership, decision, match, and review tables;
  - new append-only `corpus_history_entries` projection for published/retired
    challenge fingerprints;
  - nullable references from `design_tasks`, `challenge_designs`, and
    `build_attempts` where required;
  - additive indexes for profile signatures, ledger lookup, and gate status.
- **Services**:
  - profile allocator/reservation service;
  - design evidence validation/commit;
  - build-contract enforcement;
  - artifact observation and corpus comparison.
- **Prompts**:
  - Design receives reservation plus bounded ledger context;
  - shard prompt treats `build_contract` as authoritative and instructs Build
    to fail rather than redesign.
- **Validation**:
  - declared-vs-observed profile checks;
  - executable negative tests;
  - source/solver/path fingerprints;
  - batch/history corpus decision.
- **Compatibility**:
  historical tasks/designs/builds remain readable. They are marked `legacy`
  and are not retroactively required to have reservations/evidence, but they
  may be fingerprinted as comparison candidates. New production submissions
  cannot use the legacy exemption.
- **Operational change**:
  the production release unit becomes a corpus-gated batch, not merely a set of
  individually passing build attempts.

## Out of Scope

- Automatically adding unsupported categories. A category must already have
  complete design, build, validation, and delivery contracts.
- Proving mathematically that no unintended solution exists. The system proves
  declared negative cases and records residual review risk.
- Letting Build negotiate or rewrite the design contract. Contract revision is
  a Design-stage operation.
