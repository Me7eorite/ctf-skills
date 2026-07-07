## 1. Baseline correctness and admission

- [x] 1.1 Fix current lint violations in touched research/design modules and add
      the focused Ruff command to CI.
- [x] 1.2 Make `quality_gate_passed = false` a hard governed
      `BuildOrchestrationService` admission failure for trial/production builds
      with code `design_quality_gate_failed`; persistence of the Design remains
      allowed.
- [x] 1.3 Add tests proving a failed quality gate cannot emit a governed
      staged/pending shard or create a governed build attempt.

## 2. Profile vocabulary and allocator

- [x] 2.1 Add pure domain types/validators for semantic, solve,
      implementation, and presentation profiles with closed per-category
      vocabularies.
- [x] 2.2 Add `profile_taxonomy.py` as the vocabulary authority and tests that
      policy values cannot reference unknown vocabulary entries.
- [x] 2.3 Add versioned profile policy to `generation-profiles.json`, including
      quota ratios, cooldowns, compatible language/format/runtime combinations,
      and hard-forbidden combined signatures.
- [x] 2.4 Implement deterministic canonical profile signatures.
- [x] 2.5 Implement deterministic batch/history-aware profile allocation with
      stable tie-breaking and `design_diversity_exhausted` diagnostics.
- [x] 2.6 Distinguish hard occupancy (active/live/published) from advisory
      history (superseded/rejected/design_unbuildable).
- [x] 2.7 Expose a pure profile-capacity check that research readiness can call
      without persisting reservations.
- [x] 2.8 Tests: quotas, compatibility, exact-signature rejection,
      deterministic repeats, and no silent C/ELF fallback.

## 3. Research designable-mechanism readiness

- [x] 3.1 Change research readiness counting to use only
      `kind in {technique, variant}` as primary designable evidence and the
      profile-capacity check from section 2.
- [x] 3.2 Make DesignTask primary allocation consume only designable findings;
      keep scenario/prerequisite findings as supporting evidence.
- [x] 3.3 Persist/report when `RESEARCH_DIVERSITY_SOFT_PASS_BELOW_BY` was used
      as `research_runs.trial_only`; do not duplicate the marker on the request.
- [x] 3.4 Tests: many distinct scenarios cannot satisfy an under-supported
      design pool; repeated sub-technique findings can still pass when they
      support distinct solve/implementation profiles.

## 4. Reservation persistence and concurrency

- [x] 4.1 Add `design_profile_reservations` model, DTO, repository, Alembic
      migration, `reservation_version`, active partial unique index, and
      policy-derived nullable `occupancy_scope` and `exclusive_signature_key`
      with a partial unique index over active scoped keys, plus
      `reserved|committed|released` checks.
- [x] 4.2 Add nullable current-reservation reference/exposure on DesignTask
      (`current_reservation_id`).
- [x] 4.3 Allocate all request reservations atomically under the existing
      parent-request lock during generation/regeneration.
- [x] 4.4 Add category-scoped `design_profile_ledgers`, monotonic
      `ledger_version`, policy version binding, cross-request allocation lock,
      conflict retry, and release semantics.
- [x] 4.5 PostgreSQL concurrency tests: same-request and cross-request
      allocators cannot reserve the same hard-exclusive signature; failed
      transactions leave no partial reservations.

## 5. Evidence-backed Design prompt and output

- [x] 5.1 Build a bounded ledger snapshot containing siblings, nearest
      historical profiles, quota usage, forbidden signatures, and version.
- [x] 5.2 Extend Design prompt/schema with reservation, ledger context,
      evidence, distinctness claim, compared challenge IDs, and build contract.
- [x] 5.3 Add `design_evidence` model/DTO/repository/migration with
      `superseded_at`, `superseded_by_evidence_id`, `supersession_reason`,
      `evidence_version`, and one-unsuperseded-row partial unique constraint.
- [x] 5.4 Validate evidence finding IDs against the task ResearchRun and compare
      IDs against the supplied ledger snapshot.
- [x] 5.5 Validate exact profile equality with the reservation and validate the
      structured build contract, declared artifact/fixture IDs, closed harness
      registry, and per-stage asset verification/dependency harnesses.
- [x] 5.6 Commit ChallengeDesign, DesignEvidence, and reservation transition in
      one transaction; reject stale conflicting ledger versions.
- [x] 5.7 Tests for forged finding IDs, invented compared IDs, profile drift,
      incomplete contracts, stale ledgers, retries, and atomic completion.
- [x] 5.8 Add `request_design_revision`: supersede the live design/evidence,
      release and re-reserve under locks, clear stale review, and return eligible
      `designed|build_failed|built` tasks to `draft`; require plan
      approval before queue, reject active builds, and preserve released
      production versions.
- [x] 5.9 Route governed failed pre-build difficulty reviews through
      `request_design_revision` diagnostics instead of the legacy direct
      supersede/requeue helper; keep legacy behavior available only for the
      non-governed legacy compatibility path and `legacy_trial` submissions.

## 6. Build as construction

- [x] 6.1 Embed evidence ID, required profile, and complete build contract in
      attributed shard payloads and persist `build_attempts.design_evidence_id`
      plus contract hash.
- [x] 6.2 Remove defaults for governed fields (`language`, `runtime`,
      `artifact_format`, interaction, concealment); missing values fail with
      `build_contract_incomplete`.
- [x] 6.2a Keep legacy/shadow renderer defaults isolated from committed governed
      contracts so C/ELF/unspecified fallbacks cannot make a governed task
      production-eligible.
- [x] 6.3 Update shard prompt: contract is authoritative; Build may only use
      `allowed_implementation_freedom`; infeasibility returns
      `design_unbuildable`.
- [x] 6.4 Preserve contract identity across retry/resume; require a new Design
      evidence version for contract changes.
- [x] 6.5 Make BuildReconciler roll parent state only from attempts bound to the
      task's current DesignEvidence; older attempts remain immutable history.
- [x] 6.6 Tests proving Build payload cannot mutate or omit governed fields and
      an old successful attempt cannot overwrite a revised draft's state.

## 7. Artifact observation and contract validation

- [x] 7.1 Add `artifact_observations` model/DTO/repository/migration with
      `is_current`/`superseded_at` versioning and required/observed/check result
      API representation.
- [x] 7.2 Add category observation plugins for actual format, architecture,
      language/toolchain evidence, imports/APIs, interaction, solver behavior,
      and flag exposure.
- [x] 7.3 Implement closed host-owned negative-test harnesses with declarative
      fixture/assertion references, bounded timeout/output, challenge-local cwd,
      and no arbitrary executable/shell input.
- [x] 7.4 Compare observed and required profiles and emit closed failure codes.
- [x] 7.5 Add required asset-flow validation hooks and random-flag rebuild
      support where category contracts permit it.
- [x] 7.6 Integrate observation before successful build reconciliation and into
      existing per-attempt revalidation.
- [x] 7.7 Bind observation reuse to BuildAttempt, DesignEvidence, contract hash,
      and artifact-manifest hash; invalidate stale resume/all-skipped evidence.
- [x] 7.8 Tests for mismatch, unknown observation, successful shortcut,
      non-required asset flow, hardcoded solver, and passing contract.

## 8. Corpus governance

- [x] 8.1 Add the corpus fingerprint schema and canonical generators for
      semantic/solve/implementation/combined/source/solver/path fingerprints.
- [x] 8.2 Add `corpus_batches`, immutable batch memberships,
      `corpus_decisions`, `corpus_matches`, `observation_review_decisions`, and
      `corpus_review_decisions`, plus append-only `corpus_history_entries`.
- [x] 8.3 Implement indexed candidate shortlisting and exact similarity
      comparison against batch, live committed evidence, and published/retired
      corpus history.
- [x] 8.4 Implement `passed|review_required|blocked` decisions and configurable
      thresholds/quotas.
- [x] 8.5 Persist matched challenge IDs, scores, reasons, and separate
      observation/corpus review decisions; forbid overrides for hard mismatches,
      exact combined duplicates, and failed validation.
- [x] 8.6 Add production publication gate and keep shadow/trial modes explicit.
- [x] 8.7 Tests: renamed/constant-only clones block; high source/solver
      similarity routes correctly; distinct implementation profiles pass.
- [x] 8.8 Integrate explicit corpus batch selection with `Packer` so production
      bundles require `corpus_batch_id`, database membership decisions, and
      aggregate batch pass rather than `metadata.build_status` alone.
- [x] 8.9 Add regression coverage for aggregate corpus decisions: approved
      member reviews can produce an aggregate `passed` decision without
      rewriting raw member decisions, while unapproved reviews or hard blocks
      prevent production packing.

## 9. API and dashboard

- [x] 9.1 Expose current reservation, current DesignEvidence, build
      eligibility, and superseded/history summaries on DesignTask detail APIs;
      keep failed-quality and superseded evidence inspectable but never mark
      it build-eligible.
- [x] 9.2 Expose ArtifactObservation and corpus decision on BuildAttempt detail,
      including raw member decision, effective acceptance, aggregate batch
      decision, review provenance, selected `corpus_batch_id`, and
      non-overrideable failure reasons; without an explicit batch, list
      memberships but do not compute production delivery eligibility.
- [x] 9.3 Add dashboard sections for profile allocation, evidence citations,
      required-vs-observed contract, negative tests, and matched corpus entries;
      label current vs historical rows and raw vs effective review states.
- [x] 9.4 Add service-backed re-reserve/regenerate and review decision actions
      for build observations/corpus decisions; no client-side policy/fingerprint
      computation and no client-side promotion from raw `review_required` to
      accepted.
- [x] 9.5 Add service-backed Design revision action with quality-failure and
      contract-revision diagnostics.
- [x] 9.6 Add Chinese operator copy and responsive rendering tests.

## 10. Compatibility, deletion, and operations

- [x] 10.1 Grandfather historical rows for read/revalidate while blocking new
      production builds without committed evidence; all legacy rebuilds must be
      explicit `legacy_trial`, non-production, and never counted as governed
      `trial`.
- [ ] 10.2 Extend ResourceDeletionService scopes to reservations, evidence,
      observations, and corpus decisions; preserve minimal published/retired
      corpus history, admitted-review provenance, and acceptance/blocking
      reasons unless an explicit governance purge is requested.
- [ ] 10.3 Add optional import/fingerprint tool for reviewed historical
      challenges.
- [ ] 10.4 Document profile policy, thresholds, shadow/trial/production modes,
      and `design_unbuildable` recovery.
- [ ] 10.5 Extend hermes runner/reconciler contracts so accepted bound
      observations, not metadata solve status alone, own governed success.
- [ ] 10.6 Keep this change's single-change strict validation green; document
      unrelated active-change validation failures separately so rollout evidence
      does not depend on fixing other in-progress proposals first.

## 11. Rollout evidence

- [ ] 11.1 Shadow-run the current corpus and publish required-vs-observed and
      similarity reports.
- [ ] 11.2 Generate a 20-challenge mixed-difficulty trial batch and require all
      Design evidence/build contracts/observations to pass.
- [ ] 11.3 Record acceptance metrics: pass rate, review rate, blocked duplicate
      rate, profile distribution, and false-positive review findings.
- [ ] 11.4 Enable production mode only after two consecutive trial batches pass
      the agreed thresholds; repeat checkpoints at 50 and 150 before 500.
