## 1. Publisher Module

- [x] 1.1 Create `src/hermes/build_publisher.py` with `prepare_publication_contract(...)` and `publish_workspace_output(paths, workspace, *, contract)` entry points plus an immutable `PublicationContract`. The module lives under `hermes`, not `services`, because live repo dependency-direction tests forbid `hermes -> services` while runner integration must call the publisher directly.
- [x] 1.2 Move the existing `promote_claimed_outputs` core logic from `src/hermes/workspace.py` into the publisher as its first stage. Within the same PR, replace any remaining export of that name with a deprecation stub that raises `WorkspacePromotionError("promote_claimed_outputs removed; use hermes.build_publisher.publish_workspace_output with a PublicationContract")`; the runner and all other callers MUST import the publisher entry point directly. No silent forwarding is permitted.
- [x] 1.3 Add a `PublishResult` dataclass: `published_paths: list[Path]`, `quarantined: list[Path]`, `output_manifest_hash: str`.
- [x] 1.4 Capture normalized shard identity, execution mode, resume targets, parsed policy, base digests, and host-owned input hashes before Hermes invocation; re-verify them before publication.
- [x] 1.5 Add stable publisher error phases (`contract`, `allowlist`, `policy`, `limits`, `stage`, `commit`, `manifest`, `rollback`, `recovery`) without host-absolute path leakage.
- [x] 1.6 Verify an immutable manifest projection while allowing only publisher-owned `output_manifest_hash`/`publish_generation` evolution; use a fresh journal and increment generation for every validation-repair publication whose result is `succeeded`; `noop` publications MUST NOT increment generation or write a journal.
- [x] 1.7 Derive the next `publish_generation` from `state/highest-committed-generation.json` (falling back to 0 when absent); reject any new value not strictly greater; atomically update that high-water file (temp + rename) at the end of every successful publication; never delete it while the workspace is alive; document that `manifest.json::publish_generation` and `output_manifest_hash` are authoritative ONLY when paired with the high-water file at the same generation.
- [x] 1.8 Place all publisher-owned runtime state under `state/` (workspace-relative `state/publish-journal.json`, `state/publish-status.json`, `state/highest-committed-generation.json`). Extend `ExecutionWorkspace` with a `.state` property; ensure `initialize()` creates the directory; reject Hermes-written files under `state/` during contract verification.
- [x] 1.9 In the contract input-hash, EXPLICITLY exclude every path under `state/` and the publisher-owned manifest projection fields. Enumerate the exclusion set in code (not regex over `state/`) so adding a new publisher-owned file later requires a deliberate update.
- [x] 1.10 In `_collect_candidates` (or an adjacent contract-phase step), cross-check the observed staging directories against `contract.resume_output_targets`: every recorded id MUST resolve to exactly that workspace-relative basename in staging, and every staging directory for a claimed id MUST match its recorded target. Mismatch SHALL raise `WorkspacePublishError(phase="contract")` referencing both the recorded target and the observed path, before any limits / change-policy / canonical work runs. Empty `resume_output_targets` (initial / clean rebuild) SHALL skip the check. `resume_output_targets` remains advisory for prompt rendering; the publisher's truth source is still `./output/` enumeration under the allowlist rules.

## 2. Allowlist Hardening

- [x] 2.1 Verify the existing allowlist still rejects: output-tree symlinks, special files, `..` traversal, absolute paths, unexpected category roots, non-claimed challenge ids, duplicate-id directories, `metadata.json` missing.
- [x] 2.2 Add an explicit check that `metadata.json::id` and `metadata.json::category` match the claimed shard's `challenges[*].id` and `challenges[*].category` (identity-field hard check, independent of change_policy).
- [x] 2.3 The matcher MUST use the `_match_claimed_id` helper (claimed-ids set, NOT regex). Preserve the regex-removal contract from the previous proposal.
- [x] 2.4 Enforce defaults of 2 GiB total bytes, 50,000 files, depth 64, and 255 UTF-8 bytes/component with validated positive-integer overrides (`BUILD_PUBLISH_MAX_BYTES`, `BUILD_PUBLISH_MAX_FILES`, `BUILD_PUBLISH_MAX_DEPTH`, `BUILD_PUBLISH_MAX_COMPONENT_BYTES`); use `lstat`, never follow symlinks, and rescan temporary copies before commit.

## 3. Change-Policy Enforcement

- [x] 3.1 If `input/change-policy.json` exists, load and validate its schema (`base_artifact_relpath: str`, `preserve: list[str]`, `forbid: list[str]`).
- [x] 3.2 For each `preserve` entry:
    - `path` (no `#`): byte-compare staging file with base-artifact file.
    - `path#json_field`: load JSON from both, compare the named top-level field by equality.
    - Mismatch raises `WorkspacePublishError` with a message naming the mismatched preserve entry.
- [x] 3.3 For each `forbid` entry: if it newly exists in staging (and did not exist in base-artifact), raise `WorkspacePublishError`.
- [x] 3.4 If `change-policy.json` exists but `input/base-artifact/` does not, raise `WorkspacePublishError("change-policy requires base-artifact materialization")`.
- [x] 3.5 When `change-policy.json` is absent, skip the diff entirely (initial-run path).
- [x] 3.6 Strictly validate policy schema and normalized POSIX paths: reject unknown keys, duplicates, wrong types, empty/dot/dotdot components, absolute paths, backslashes, NUL, symlinks, root escape, and missing selected JSON fields.
- [x] 3.7 Treat each `forbid` value as a recursive prefix and reject every newly added descendant relative path even when the base already contains the prefix directory.

## 4. Output Manifest Hash

> Status: 4.1 and 4.4 land in this PR. 4.2 and 4.3 require Task 5's
> publisher locks and durable journal; they remain unchecked until Task 5
> lands. See assessment §29 (Task 4 partial completion) and §30 G11.

- [x] 4.1 Compute a deterministic batch hash over claimed id, relative path, entry type, normalized mode, and content using length-prefixed canonical records; include empty directories.
- [x] 4.2 Re-hash canonicals after rename, then atomically write `output_manifest_hash` to the workspace manifest while locks remain held.
- [x] 4.3 Roll back the canonical batch on hash mismatch or manifest replacement failure; cover the rename/manifest crash window in the durable journal.
- [x] 4.4 Short-circuit publish with a `noop` outcome (no journal, no generation increment, no quarantine, no sweep) when the staged hash equals the last committed `output_manifest_hash`; expose the outcome to the runner so it can avoid a redundant validation rerun.

## 5. Serialized Recoverable Publish

- [x] 5.0 Add `paths.locks_root` (= `work/locks`) and `paths.build_publisher_locks` (= `paths.locks_root / "build-publisher"`) to `src/core/paths.py`; ensure `initialize()` creates both (default umask is sufficient — the lock files carry no secrets). Future proposals MUST add sibling subdirectories under `locks_root`, never nest under `build-publisher/`. Reject non-POSIX hosts in publisher preflight with a clear unsupported-platform error; mark POSIX-only publisher lock/recovery tests with an explicit platform gate so Windows development can still run the non-publisher suite.
- [x] 5.1 Acquire sorted digest-named POSIX cross-process `(category, claimed_id)` locks under `paths.build_publisher_locks` using `fcntl.flock(LOCK_EX)` with a validated default 30-second timeout, and hold them through commit or rollback. Lock filenames SHALL be a digest of `(category, claimed_id)` only — host paths are never embedded.
- [x] 5.2 Validate and stage the complete batch before canonical mutation; require temp/canonical/quarantine to be on one filesystem.
- [x] 5.3 Write/fsync a durable batch journal before the first canonical rename and after every phase transition.
- [x] 5.4 Preserve the fixed quarantine tree, adding a unique transaction suffix on basename collision.
- [x] 5.5 On ordinary failure, reverse the journal to restore every predecessor and remove every temp/new destination.
- [x] 5.6 Add bootstrap reconciliation for incomplete journals under the same locks and make recovery idempotent.
- [x] 5.7 In bootstrap reconciliation, handle the "committed journal generation > high-water" case explicitly: atomically push `state/highest-committed-generation.json` forward to the journal's generation, then archive/remove the journal. Test that re-running reconciliation on an already-finalized workspace is a no-op.
- [x] 5.8 Document that publisher lock files are decoupled from challenge lifecycle: the publisher never deletes them on publication completion or on `resource_deletion`. Orphan lock files are expected and harmless.

## 6. Retention Sweep

- [x] 6.1 Keep output/logs through host validation and all validation-repair attempts; clear them only after terminal validation success.
- [x] 6.2 On publisher or validation failure, retain output/logs; atomically write a host-owned terminal status/timestamp marker for every terminal success or failure.
- [x] 6.3 Treat each terminal workspace containing replaced-canonical quarantine or failed output/log staging as one retention root; remove roots older than 7 days, then cap all such roots at the newest 20.
- [x] 6.4 Skip incomplete journals and any workspace whose publisher locks cannot be acquired non-blockingly.
- [x] 6.5 Sweep errors (permission/busy) log a warning and do NOT block the publish result.
- [x] 6.6 Throttle sweep invocations to at most once per `BUILD_PUBLISH_SWEEP_INTERVAL_SECONDS` per process (default 60 seconds), recording suppressed calls so the next eligible call still runs the sweep; validate that the env override parses as a positive integer.

## 7. Runner Integration

- [x] 7.1 In `src/hermes/runner.py`, prepare the publication contract before invoking Hermes and replace every initial/repair call to `promote_claimed_outputs` with `publish_workspace_output(..., contract=contract)`.
- [x] 7.2 If publish fails, runner returns `status=failed, failure_type=infrastructure` and calls `_mark_shard_failed` (so BuildReconciler observes failed, never lost).
- [x] 7.3 If publish succeeds, validator runs against the just-published canonical tree (no behavior change vs today).
- [x] 7.4 Make `materialize_resume_outputs` return the exact workspace-relative target for each claimed id; persist the mapping in `input/manifest.json::resume_output_targets`.
- [ ] 7.5 Render every materialized target's exact path into the resume plan and explicitly prohibit creating or renaming another directory for that id.
- [x] 7.6 Parse `execution_mode` with the compatibility rule: explicit value wins; otherwise `resume_from_shard_basename` means `resume`, and its absence means `clean` (publisher contract layer). Runner adopts a wider "implicit" compatibility shim that still materializes for legacy first-run payloads — Task 9 covers the migration of those payloads to explicit `execution_mode`.
- [ ] 7.7 In clean mode, skip resume output materialization and prior-shard progress carry-forward; leave the canonical predecessor untouched until successful publication. (Runner already skips materialize for explicit `clean`; prior-shard carry-forward bypass is wired in 7A's `BuildOrchestrationService.clean_rebuild`.)
- [x] 7.8 Reject unknown or contradictory execution modes during preflight; explicit resume requires a safe resume basename and explicit clean forbids one.
- [x] 7.9 Preserve structured publisher phase/id/path diagnostics in the failed shard and terminal marker.
- [x] 7.10 Consume `PublishResult.outcome` after every `publish_workspace_output` call. On `noop` the runner SHALL exit the current validation-repair loop iteration: do NOT rerun the validator, do NOT advance the attempt's progress percent, and finish the attempt from the most recently recorded `per_results`. The existing `pre_signature == post_signature` fast-path in `process_one` MAY remain as a performance optimization but is NOT the semantic source of truth; publisher `noop` is the authoritative no-op signal (see design Decision 13).

## 7A. Build Orchestration and UI

- [ ] 7A.1 Keep the existing retry action resume-oriented and write `execution_mode: "resume"` into its shard payload.
- [ ] 7A.2 Add a database migration introducing `build_attempts.idempotency_key TEXT NULL UNIQUE` (sparse unique index, NULL allowed for legacy rows). Persist the API-provided key on the new attempt; replayed requests with the same key resolve to the existing row and return its id. Concurrent inserts that lose the UNIQUE race SHALL be caught and re-read the surviving row instead of surfacing the DB error. Stale source attempts fail with a stable error code. The `confirmed` boolean is a request-body field; absence of `confirmed=true` returns `409 confirmation_required`. (This task lands before 7A.3; 7A.3's concurrency safety depends on the UNIQUE column existing.)
- [ ] 7A.3 Add `BuildOrchestrationService.clean_rebuild(attempt_id, *, idempotency_key, confirmed)` that reuses the existing `_prepare` / `_submit` plumbing (eligibility re-check happens in `_prepare`'s session against the source attempt and design task rows; new attempt commit MAY use a separate session, matching `retry()`). Extend `_prepare` / `_validate_task_for_submit` with an explicit `execution_mode` branch: in `resume` mode it continues to consume `expected_source_id` and writes `resume_from_shard_basename` into the shard payload; in `clean` mode it still anchors eligibility on the source attempt id (latest failed/lost) but SHALL NOT write `resume_from_shard_basename`. The new shard payload SHALL write `execution_mode: "clean"`. Same-key idempotency relies on the idempotency-key UNIQUE constraint introduced in 7A.2, NOT on holding a single transaction across re-check and commit; different keys remain separate submissions until proposal #3 adds source-attempt lease/fencing. Tests cover (a) eligibility against a stale source attempt, (b) concurrent submissions with the same idempotency key collapsing to one row, (c) replay of an already-completed key returning the same id, (d) different idempotency keys are not promised to collapse in this proposal, and (e) clean payload contains `execution_mode: "clean"` and omits `resume_from_shard_basename`.
- [ ] 7A.4 Expose separate `重试构建` and `干净重建` controls in the build list; browser confirmation supplements, but does not replace, API confirmation. The UI generates the idempotency key client-side per **button press** (one UUIDv4 per click — a new click is a new key, even if the previous attempt is still failed). Only the HTTP-layer retries of the SAME in-flight `fetch` reuse the same key (network deduplication); the UI MUST NOT reuse a key across user-visible interactions. Document this rule next to the click handler so future maintainers don't widen the reuse window.

## 8. Spec Migration

- [ ] 8.1 REMOVE the "Claimed workspace output is promoted for existing validation" Requirement from `openspec/specs/hermes-execution-protocol/spec.md` (carried over by the previous proposal's archive).
- [ ] 8.2 ADD the publisher Requirements (see specs/worker-pool-execution/spec.md) to the new capability.
- [ ] 8.3 Verify `openspec validate add-staged-publication-allowlist --strict` passes.

## 9. Tests

- [ ] 9.1 Migrate all tests previously asserting promotion semantics under the bridge requirement to assert publisher semantics. Test names should reference the publisher (`test_publisher_*`).
- [ ] 9.2 Add output manifest hash regression: same canonical tree → same hash; one byte change → different hash.
- [ ] 9.3 Add change-policy diff tests:
    - preserve byte-mismatch → reject (e.g. `validate.sh` modified)
    - preserve JSON-field mismatch → reject (`metadata.json#flag` changed)
    - forbid path newly present → reject
    - change-policy.json without base-artifact → reject
    - all clear → publish succeeds, hash recorded
- [ ] 9.4 Add identity-field hard check: `metadata.json::id` ≠ claimed id → reject (even without change_policy).
- [ ] 9.5 Add retention sweep tests:
    - quarantine older than 7 days → deleted
    - quarantine fresher than 7 days → kept
    - 21st quarantine triggers oldest eviction
    - sweep error does not block successful publish result
- [ ] 9.6 Add runner integration test: publisher failure → runner returns `failure_type=infrastructure`, BuildReconciler observes failed (not lost).
- [ ] 9.7 Add a no-change-policy test: when `input/change-policy.json` is absent, publisher skips the diff and behaves identically to the previous proposal's narrow promotion.
- [ ] 9.8 Add resume-target regressions:
    - materialized canonical basename is recorded exactly in the manifest
    - rendered prompt names that exact path and prohibits a second directory
    - a simulated second `<id>-<slug>` directory still fails closed and leaves canonical unchanged
- [ ] 9.9 Add clean-rebuild regressions:
    - output starts empty even when canonical exists
    - prior progress is not carried forward
    - failed publish preserves canonical
    - successful publish quarantines and replaces canonical
- [ ] 9.10 Add API/UI tests proving retry emits `resume` while clean rebuild emits `clean` and requires explicit confirmation.
- [ ] 9.11 Add contract/policy security tests: post-invocation input mutation, traversal, symlink, unknown schema key, missing JSON field, and new descendant under an existing forbid prefix all fail before canonical mutation.
- [ ] 9.12 Add batch/recovery tests: overlapping publisher serialization, second-id rollback, cross-device preflight rejection, process-death journal reconciliation, manifest-write rollback, and idempotent recovery.
- [ ] 9.13 Add hash/limit tests: executable mode and empty directory affect hash; delimiter-like filenames remain unambiguous; byte/file/depth limits fail before commit.
- [ ] 9.14 Add retention/repair tests: publisher does not clear repair inputs, repeated publish accepts only publisher-owned manifest evolution and uses fresh journals, terminal success cleans staging, failed staging is bounded, and active journals/locks are never swept.
- [ ] 9.15 Add clean-rebuild same-key idempotency, different-key non-collapse documentation, and contradictory-mode preflight tests.
- [ ] 9.16 On POSIX, run `uv run pytest tests/app/test_build_publisher.py tests/app/test_execution_workspace.py tests/app/test_runner_resume.py tests/app/test_build_reconciler.py tests/app/test_build_attempts_api.py tests/app/test_build_dispatch_ui.py -q` and confirm all green. On Windows, run the same command with POSIX-only publisher lock/recovery tests skipped and separately document that full publisher validation requires POSIX CI/deployment.

## 10. Cleanup

- [ ] 10.1 Delete the `promote_claimed_outputs` function and its deprecation stub from `src/hermes/workspace.py` before this change archives. The exception class `WorkspacePromotionError` is NOT removed — it remains as the base class of `WorkspacePublishError` and is still imported by runner/publisher. A silent forwarding shim of the function is NOT a permitted exit; if any caller still references the function name at archive time, archival is blocked until the reference is migrated.
- [ ] 10.2 Remove imports of the `promote_claimed_outputs` **function** from `src/hermes/runner.py` and any other call sites; only `hermes.build_publisher.publish_workspace_output` should be referenced. Add a repository-level grep guard in CI (or an equivalent unit test) matching the function call pattern (e.g. `\bpromote_claimed_outputs\s*\(`); the guard SHALL NOT match the `WorkspacePromotionError` exception class name.
