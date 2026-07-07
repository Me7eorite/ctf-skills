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

- **Modify `research-planning`** so design readiness is based on designable
  evidence capacity (`kind in {technique, variant}` plus usable solve/profile
  variation), not on scenario/prerequisite volume and not on sub-technique names
  alone.
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
- **Modify `delivery-bundle`** so production packing requires an effectively
  accepted ArtifactObservation and an effectively accepted corpus-admission
  decision; passed metadata alone is insufficient. Shadow/trial bundles are
  explicit and marked non-production.
- **Add dashboard/API visibility** for reservations, design evidence, contract
  checks, observed profiles, and corpus-gate findings. The UI displays
  authoritative server results and does not recompute fingerprints or policy.

## Sequencing and Relationship to Existing Diversity Work

This change is a governed-production layer. It intentionally supersedes the
earlier advisory-only diversity behavior for new production work:

- `add-design-technique-diversity` remains useful for coarse research/planning
  visibility, but its soft warnings are not sufficient for production
  admission under this change.
- This change assumes the normalized `asset_flow` / `required_asset_flow`
  schema already exists. If it does not, that prerequisite must be completed
  before this change can be implemented.
- `add-challenge-pattern-library` or equivalent pattern/fingerprint work may
  feed the ledger and corpus comparison, but this change owns the hard
  production gates over reservations, evidence, observations, and corpus
  decisions.

Implementation should land behind explicit `shadow`, `trial`, and `production`
modes. `shadow` mode records reservations, evidence, observations, and corpus
findings where possible, but it must not make legacy builds production-eligible,
must not publish production releases, and must mark all outputs non-production.
`trial` mode enforces governed Design and Build admission for trial batches
while keeping release manual and non-production. `production` mode uses this
change's hard admission, observation, corpus, and release gates. New governed
production submissions cannot fall back to the legacy build path.
The separate `legacy_trial` rebuild path remains operator-only, non-production,
and reserved for grandfathered artifacts without committed governance; it is
not the same as governed `trial`.
`research_runs.trial_only` is a source marker for research that was soft-passed
below the production readiness floor. It is not a build mode, and it can flow
into governed trial work only when the downstream Design and Build gates also
pass.

## Capabilities

### Modified Capabilities

- `research-planning`
- `design-task-planning`
- `structured-challenge-designs`
- `build-orchestration`
- `hermes-execution-protocol`
- `delivery-bundle`
- `postgres-persistence`
- `resource-deletion`

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
  - `research_runs.trial_only` to mark diversity-soft-passed research that can
    feed trial/shadow work but cannot pass production corpus admission;
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
  historical tasks/designs/builds remain readable as legacy historical data.
  They are not retroactively required to have reservations/evidence, but they
  may be fingerprinted as comparison candidates. New production submissions
  cannot use the legacy exemption.
- **API/dashboard**:
  operator surfaces expose the current governance chain and raw/effective
  review states from server DTOs. Delivery eligibility is reported only for an
  explicitly selected corpus batch. Surfaces do not infer build eligibility,
  rewrite stored review decisions, or collapse historical/superseded evidence
  into the current chain.
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
