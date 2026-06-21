## 1. Publisher Module

- [ ] 1.1 Create `src/services/build_publisher.py` with a `publish_workspace_output(paths, workspace, payload, *, change_policy=None)` entry point.
- [ ] 1.2 Move the existing `promote_claimed_outputs` core logic from `src/hermes/workspace.py` into the publisher as its first stage. Keep the public name in `workspace.py` as a thin shim for backward compatibility during the transition; the runner stops importing it directly.
- [ ] 1.3 Add a `PublishResult` dataclass: `published_paths: list[Path]`, `quarantined: list[Path]`, `output_manifest_hash: str`.

## 2. Allowlist Hardening

- [ ] 2.1 Verify the existing allowlist still rejects: output-tree symlinks, special files, `..` traversal, absolute paths, unexpected category roots, non-claimed challenge ids, duplicate-id directories, `metadata.json` missing.
- [ ] 2.2 Add an explicit check that `metadata.json::id` and `metadata.json::category` match the claimed shard's `challenges[*].id` and `challenges[*].category` (identity-field hard check, independent of change_policy).
- [ ] 2.3 The matcher MUST use the `_match_claimed_id` helper (claimed-ids set, NOT regex). Preserve the regex-removal contract from the previous proposal.

## 3. Change-Policy Enforcement

- [ ] 3.1 If `input/change-policy.json` exists, load and validate its schema (`base_artifact_relpath: str`, `preserve: list[str]`, `forbid: list[str]`).
- [ ] 3.2 For each `preserve` entry:
    - `path` (no `#`): byte-compare staging file with base-artifact file.
    - `path#json_field`: load JSON from both, compare the named top-level field by equality.
    - Mismatch raises `WorkspacePublishError` with a message naming the mismatched preserve entry.
- [ ] 3.3 For each `forbid` entry: if it newly exists in staging (and did not exist in base-artifact), raise `WorkspacePublishError`.
- [ ] 3.4 If `change-policy.json` exists but `input/base-artifact/` does not, raise `WorkspacePublishError("change-policy requires base-artifact materialization")`.
- [ ] 3.5 When `change-policy.json` is absent, skip the diff entirely (initial-run path).

## 4. Output Manifest Hash

- [ ] 4.1 After successful publish, compute `output_manifest_hash` over the published canonical tree (sorted relative paths + per-file sha256).
- [ ] 4.2 Write the hash to `work/executions/<workspace_id>/input/manifest.json` under `output_manifest_hash`.
- [ ] 4.3 The hash MUST be deterministic across re-runs of the same canonical tree (same bytes, same hash).

## 5. Atomic Publish (Preserve Existing Behavior)

- [ ] 5.1 Keep the existing temp-sibling-then-rename algorithm; do not regress quarantine path or rollback semantics.
- [ ] 5.2 Quarantine path remains `work/executions/<workspace_id>/quarantine/<category>/<dirname>/` (locked by previous proposal).
- [ ] 5.3 On any mid-loop failure, rollback: restore quarantined dirs, delete temp dirs.

## 6. Retention Sweep

- [ ] 6.1 On successful publish, immediately clear `./output/` and `./logs/` of the just-published workspace (keep `./input/` for audit).
- [ ] 6.2 On any publish path (success or failure), run a global quarantine sweep:
    - Delete `work/executions/*/quarantine/` entries older than 7 days.
    - If more than 20 workspace-scoped quarantines exist (any age), delete the oldest until ≤ 20 remain.
- [ ] 6.3 Sweep errors (permission/busy) log a warning and do NOT block the publish result.

## 7. Runner Integration

- [ ] 7.1 In `src/hermes/runner.py`, replace the call to `promote_claimed_outputs` with `publish_workspace_output`. The runner passes `change_policy` loaded from `input/change-policy.json` (or None if absent).
- [ ] 7.2 If publish fails, runner returns `status=failed, failure_type=infrastructure` and calls `_mark_shard_failed` (so BuildReconciler observes failed, never lost).
- [ ] 7.3 If publish succeeds, validator runs against the just-published canonical tree (no behavior change vs today).

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
- [ ] 9.8 Run `uv run pytest tests/app/test_build_publisher.py tests/app/test_execution_workspace.py tests/app/test_build_reconciler.py -q` and confirm all green.

## 10. Cleanup

- [ ] 10.1 Once the publisher path is in place and tests are green, delete the now-unused shim `promote_claimed_outputs` from `src/hermes/workspace.py` (or keep as a deprecated forwarder for one release if external callers exist).
- [ ] 10.2 Remove imports of `promote_claimed_outputs` from `src/hermes/runner.py` and any other call sites; only `services.build_publisher.publish_workspace_output` should be referenced.
