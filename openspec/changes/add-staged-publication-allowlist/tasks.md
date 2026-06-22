## 1. Publisher Module

- [ ] 1.1 Create `src/services/build_publisher.py` with `prepare_publication_contract(...)` and `publish_workspace_output(paths, workspace, *, contract)` entry points plus an immutable `PublicationContract`.
- [ ] 1.2 Move the existing `promote_claimed_outputs` core logic from `src/hermes/workspace.py` into the publisher as its first stage. Keep the public name in `workspace.py` as a thin shim for backward compatibility during the transition; the runner stops importing it directly.
- [ ] 1.3 Add a `PublishResult` dataclass: `published_paths: list[Path]`, `quarantined: list[Path]`, `output_manifest_hash: str`.
- [ ] 1.4 Capture normalized shard identity, execution mode, resume targets, parsed policy, base digests, and host-owned input hashes before Hermes invocation; re-verify them before publication.
- [ ] 1.5 Add stable publisher error phases (`contract`, `allowlist`, `policy`, `limits`, `stage`, `commit`, `manifest`, `rollback`, `recovery`) without host-absolute path leakage.
- [ ] 1.6 Verify an immutable manifest projection while allowing only publisher-owned `output_manifest_hash`/`publish_generation` evolution; use a fresh journal and increment generation for every validation-repair publication.

## 2. Allowlist Hardening

- [ ] 2.1 Verify the existing allowlist still rejects: output-tree symlinks, special files, `..` traversal, absolute paths, unexpected category roots, non-claimed challenge ids, duplicate-id directories, `metadata.json` missing.
- [ ] 2.2 Add an explicit check that `metadata.json::id` and `metadata.json::category` match the claimed shard's `challenges[*].id` and `challenges[*].category` (identity-field hard check, independent of change_policy).
- [ ] 2.3 The matcher MUST use the `_match_claimed_id` helper (claimed-ids set, NOT regex). Preserve the regex-removal contract from the previous proposal.
- [ ] 2.4 Enforce defaults of 2 GiB total bytes, 50,000 files, depth 64, and 255 UTF-8 bytes/component with validated positive-integer overrides; use `lstat`, never follow symlinks, and rescan temporary copies before commit.

## 3. Change-Policy Enforcement

- [ ] 3.1 If `input/change-policy.json` exists, load and validate its schema (`base_artifact_relpath: str`, `preserve: list[str]`, `forbid: list[str]`).
- [ ] 3.2 For each `preserve` entry:
    - `path` (no `#`): byte-compare staging file with base-artifact file.
    - `path#json_field`: load JSON from both, compare the named top-level field by equality.
    - Mismatch raises `WorkspacePublishError` with a message naming the mismatched preserve entry.
- [ ] 3.3 For each `forbid` entry: if it newly exists in staging (and did not exist in base-artifact), raise `WorkspacePublishError`.
- [ ] 3.4 If `change-policy.json` exists but `input/base-artifact/` does not, raise `WorkspacePublishError("change-policy requires base-artifact materialization")`.
- [ ] 3.5 When `change-policy.json` is absent, skip the diff entirely (initial-run path).
- [ ] 3.6 Strictly validate policy schema and normalized POSIX paths: reject unknown keys, duplicates, wrong types, empty/dot/dotdot components, absolute paths, backslashes, NUL, symlinks, root escape, and missing selected JSON fields.
- [ ] 3.7 Treat each `forbid` value as a recursive prefix and reject every newly added descendant relative path even when the base already contains the prefix directory.

## 4. Output Manifest Hash

- [ ] 4.1 Compute a deterministic batch hash over claimed id, relative path, entry type, normalized mode, and content using length-prefixed canonical records; include empty directories.
- [ ] 4.2 Re-hash canonicals after rename, then atomically write `output_manifest_hash` to the workspace manifest while locks remain held.
- [ ] 4.3 Roll back the canonical batch on hash mismatch or manifest replacement failure; cover the rename/manifest crash window in the durable journal.

## 5. Serialized Recoverable Publish

- [ ] 5.1 Acquire sorted digest-named POSIX cross-process `(category, claimed_id)` locks with a validated default 30-second timeout and hold them through commit or rollback.
- [ ] 5.2 Validate and stage the complete batch before canonical mutation; require temp/canonical/quarantine to be on one filesystem.
- [ ] 5.3 Write/fsync a durable batch journal before the first canonical rename and after every phase transition.
- [ ] 5.4 Preserve the fixed quarantine tree, adding a unique transaction suffix on basename collision.
- [ ] 5.5 On ordinary failure, reverse the journal to restore every predecessor and remove every temp/new destination.
- [ ] 5.6 Add bootstrap reconciliation for incomplete journals under the same locks and make recovery idempotent.

## 6. Retention Sweep

- [ ] 6.1 Keep output/logs through host validation and all validation-repair attempts; clear them only after terminal validation success.
- [ ] 6.2 On publisher or validation failure, retain output/logs; atomically write a host-owned terminal status/timestamp marker for every terminal success or failure.
- [ ] 6.3 Treat each terminal workspace containing replaced-canonical quarantine or failed output/log staging as one retention root; remove roots older than 7 days, then cap all such roots at the newest 20.
- [ ] 6.4 Skip incomplete journals and any workspace whose publisher locks cannot be acquired non-blockingly.
- [ ] 6.5 Sweep errors (permission/busy) log a warning and do NOT block the publish result.

## 7. Runner Integration

- [ ] 7.1 In `src/hermes/runner.py`, prepare the publication contract before invoking Hermes and replace every initial/repair call to `promote_claimed_outputs` with `publish_workspace_output(..., contract=contract)`.
- [ ] 7.2 If publish fails, runner returns `status=failed, failure_type=infrastructure` and calls `_mark_shard_failed` (so BuildReconciler observes failed, never lost).
- [ ] 7.3 If publish succeeds, validator runs against the just-published canonical tree (no behavior change vs today).
- [ ] 7.4 Make `materialize_resume_outputs` return the exact workspace-relative target for each claimed id; persist the mapping in `input/manifest.json::resume_output_targets`.
- [ ] 7.5 Render every materialized target's exact path into the resume plan and explicitly prohibit creating or renaming another directory for that id.
- [ ] 7.6 Parse `execution_mode` with the compatibility rule: explicit value wins; otherwise `resume_from_shard_basename` means `resume`, and its absence means `clean`.
- [ ] 7.7 In clean mode, skip resume output materialization and prior-shard progress carry-forward; leave the canonical predecessor untouched until successful publication.
- [ ] 7.8 Reject unknown or contradictory execution modes during preflight; explicit resume requires a safe resume basename and explicit clean forbids one.
- [ ] 7.9 Preserve structured publisher phase/id/path diagnostics in the failed shard and terminal marker.

## 7A. Build Orchestration and UI

- [ ] 7A.1 Keep the existing retry action resume-oriented and write `execution_mode: "resume"` into its shard payload.
- [ ] 7A.2 Add a distinct clean-rebuild service/API action that writes `execution_mode: "clean"`, omits `resume_from_shard_basename`, and transactionally rechecks latest-attempt/task eligibility.
- [ ] 7A.3 Require an idempotency key and explicit confirmation field in the clean-rebuild API; concurrent/replayed requests create at most one attempt and stale source attempts fail.
- [ ] 7A.4 Expose separate `重试构建` and `干净重建` controls in the build list; browser confirmation supplements, but does not replace, API confirmation.

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
- [ ] 9.15 Add clean-rebuild concurrency/idempotency and contradictory-mode preflight tests.
- [ ] 9.16 Run `uv run pytest tests/app/test_build_publisher.py tests/app/test_execution_workspace.py tests/app/test_runner_resume.py tests/app/test_build_reconciler.py tests/app/test_build_attempts_api.py tests/app/test_build_dispatch_ui.py -q` and confirm all green.

## 10. Cleanup

- [ ] 10.1 Once the publisher path is in place and tests are green, delete the now-unused shim `promote_claimed_outputs` from `src/hermes/workspace.py` (or keep as a deprecated forwarder for one release if external callers exist).
- [ ] 10.2 Remove imports of `promote_claimed_outputs` from `src/hermes/runner.py` and any other call sites; only `services.build_publisher.publish_workspace_output` should be referenced.
