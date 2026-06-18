## 1. Schema, protocol, and deletion scope

- [ ] 1.1 Run the existing non-PostgreSQL and PostgreSQL build-orchestration suites against the archived `add-build-attempts` baseline.
- [ ] 1.2 Add an Alembic revision for `design_tasks.next_build_attempt_no`, backfill each task to `COALESCE(MAX(build_attempts.attempt_no), 0) + 1`, and test clean upgrade/downgrade plus existing-row backfill.
- [ ] 1.3 Change build submission/retry to lock the task, consume and increment `next_build_attempt_no` transactionally, and add tests proving deletion cannot cause number reuse.
- [ ] 1.4 Extend `ProgressStore`, `PostgresProgressStore`, and `InMemoryProgressStore` with transaction-aware `purge_shards`, including standalone and caller-transaction tests.
- [ ] 1.5 Add deletion result/error DTOs that distinguish not-found, active conflict, deleted/retained/skipped/quarantined artifacts, and cleanup warnings.
- [ ] 1.6 Implement scope discovery for generation requests, design tasks, and individual build attempts, including row locking, relational descendants, shard basenames, referenced artifact paths, and surviving-path references.
- [ ] 1.7 Implement the active-state guard from authoritative running research/design/build child rows; allow a `building` projection containing only queued builds to proceed through safe withdrawal.
- [ ] 1.8 For direct Build Attempt deletion, reject when a different sibling is queued/running so resume evidence and in-use challenge artifacts cannot be removed.

## 2. Filesystem and persistence orchestration

- [ ] 2.1 Implement allowlisted path resolution for directly referenced paths under `work/challenges`, `work/research`, and `work/design`, including traversal, symlink escape, missing-path, unowned-path, and shared-reference classification; do not infer ownership from directory names.
- [ ] 2.2 Implement same-filesystem deletion quarantine with an atomic manifest, rollback restore, post-commit purge, and startup/before-delete recovery based on committed root-resource existence; retain ambiguous entries with warnings.
- [ ] 2.3 Implement queued-build withdrawal from staging/pending with attributed-running checks before and after withdrawal, returning a conflict when a worker wins the claim race.
- [ ] 2.4 Implement attempt operational cleanup for shard files and claim sidecars, and call transaction-aware `ProgressStore.purge_shards` for events/snapshots in the relational deletion transaction.
- [ ] 2.5 Implement `delete_artifacts=false` retention and explicit `delete_artifacts=true` removal for exclusively owned, path-contained research/design/challenge artifacts.
- [ ] 2.6 Lock reference-bearing tables for the explicit-artifact shared-reference check through candidate quarantine, and add a concurrency test against reconciler/reference updates.
- [ ] 2.7 Implement relational deletion for each root resource, explicitly deleting Challenge Designs before their RESTRICT-linked Design Attempts, and recompute parent task status after direct attempt deletion in the same transaction.
- [ ] 2.8 Re-export the deletion service from `src/services/__init__.py` and extend dependency-direction tests to prove it does not import `web` or `hermes`.

## 3. HTTP API

- [ ] 3.1 Add `DELETE /api/research/requests/{id}?delete_artifacts=false` and translate malformed/unknown ids to 404 and active scopes to 409.
- [ ] 3.2 Add `DELETE /api/design-tasks/{id}?delete_artifacts=false` with the same response and error contract.
- [ ] 3.3 Add `DELETE /api/build-attempts/{id}?delete_artifacts=false` with the same response and error contract.
- [ ] 3.4 Serialize deleted, retained, skipped, quarantined, and warning entries consistently across all three endpoints.

## 4. Dashboard deletion interaction

- [ ] 4.1 Add a reusable accessible confirmation dialog that names the resource, describes cascade impact, and exposes an unchecked `同时删除产物` checkbox.
- [ ] 4.2 Add Delete actions to generation-request list/detail views; preserve artifacts by default, refresh/navigate only after success, and surface conflicts/warnings.
- [ ] 4.3 Add Delete actions to Design Task list/detail views with the same behavior and disable or clearly reject active rows.
- [ ] 4.4 Add Delete actions to Build Attempt list/detail views with the same behavior and preserve existing retry/detail actions.
- [ ] 4.5 Ensure cancel sends no request, double-submit is disabled while pending, and polling cannot re-render over an open confirmation or in-flight deletion.

## 5. Verification

- [ ] 5.1 Add PostgreSQL service tests for all three cascade scopes, active-state conflicts, unrelated-row isolation, progress cleanup, and parent status recomputation.
- [ ] 5.2 Add filesystem tests for queued cancellation, worker claim race, rollback restoration, post-commit cleanup warnings, and interrupted-quarantine recovery.
- [ ] 5.3 Add artifact-policy tests proving default retention, explicit removal, path traversal/symlink refusal, shared-reference retention, and per-path result reporting.
- [ ] 5.4 Add API tests for default/explicit query behavior, success payloads, malformed/unknown ids, and 409 conflicts on each endpoint.
- [ ] 5.5 Add dashboard interaction coverage for unchecked/checked/cancel confirmation paths and list/detail navigation, or document manual coverage where no DOM harness exists.
- [ ] 5.6 Run `uv run ruff check src tests`, the non-PostgreSQL pytest suite, the PostgreSQL-marked deletion/API suite, and `openspec validate add-resource-deletion --strict`.
- [ ] 5.7 Manually smoke-test deletion from all six list/detail surfaces, including one default-retain deletion, one explicit artifact deletion, one queued build cancellation, and one running conflict.
