## Context

The previous proposal (`add-execution-workspace-and-profile-per-category`)
established the per-execution workspace under `work/executions/<workspace_id>/`
and a **narrow output promotion bridge** so the existing validator can keep
reading canonical `work/challenges/<cat>/<id>-<slug>/`. That bridge was
explicitly marked temporary, with a contract that the next proposal would
REMOVE it and replace it with a publisher-owned boundary.

Lease/fencing tokens and database execution rows are deliberately deferred to
the next proposal (`add-execution-lease-and-fencing`); change-policy
enforcement is documented here but only activates when revision iterations
land (also deferred). The publisher must be designed so those later additions
slot in without rewriting the boundary.

## Goals / Non-Goals

**Goals:**

- Single owner for the `./output/` → `work/challenges/` boundary.
- Hard, code-enforced allowlist (no string-grep heuristics).
- Atomic publish-or-nothing semantics; never half-published canonical state.
- Change-policy hard diff for identity fields and explicit preserve/forbid
  paths.
- Bounded retention for failed staging (audit-friendly, disk-bounded).
- Output manifest hash recorded for downstream audit hand-off.

**Non-Goals:**

- No execution row / lease / fencing token (next proposal).
- No agent registry, supervisor, slots (proposals 4-5).
- No live feedback API for change-policy (deferred to revision proposal).
- No change to research/design output paths.
- No live tailing changes (already in place from previous proposal).

## Decisions

### Decision 1: Publisher is a separate module, called from the runner

`services/build_publisher.py` exposes a single public function:

```python
def publish_workspace_output(
    paths: ProjectPaths,
    workspace: ExecutionWorkspace,
    payload: Mapping[str, Any],
    *,
    change_policy: Mapping[str, Any] | None = None,
) -> PublishResult: ...
```

The runner calls this after Hermes returns and BEFORE validation. The narrow
`promote_claimed_outputs` from the previous proposal becomes the
publisher's implementation core (it already does symlink/traversal/id
checks). New responsibilities (manifest hash, change-policy diff, retention
sweep) are layered around it.

Why a separate module: the same logic will later be invoked from
`BuildPoolSupervisor` (proposal 5) with extra fencing-token args. Splitting
it now avoids re-plumbing the runner twice.

### Decision 2: Output manifest hash semantics

`output_manifest_hash` is computed over the *published* canonical tree (post-
rename), not the staging tree. The hash is:

```
sha256(sorted(
  f"{relative_posix_path}:sha256:{file_sha256}\n"
  for file in published_tree
))
```

It is written to `work/executions/<workspace_id>/input/manifest.json` under
the `output_manifest_hash` key. The next proposal will move it onto an
execution row; until then the workspace manifest is the source of truth.

Why a manifest hash (not just per-file hashes): downstream audit
(`add-execution-audit-snapshots`, proposal 6) needs a single value that
distinguishes "same artifacts as a previous run" from "different artifacts".
Computing on the published tree (not staging) means hash comparisons across
the audit/quarantine boundary are unambiguous.

### Decision 3: Change-policy diff is opt-in via input file

When `work/executions/<workspace_id>/input/change-policy.json` exists, the
publisher MUST honor it. Schema:

```json
{
  "base_artifact_relpath": "challenges/web/web-abcdef12-0001-demo",
  "preserve": ["metadata.json#challenge_id", "metadata.json#flag",
               "metadata.json#category", "metadata.json#build_status",
               "validate.sh"],
  "forbid": ["secrets/"]
}
```

- `base_artifact_relpath` resolves against
  `work/executions/<workspace_id>/input/base-artifact/` (materialized by the
  revision flow in proposal 3).
- `preserve` paths are checked byte-for-byte against base. `path#json_field`
  syntax selects a specific JSON field within a file.
- `forbid` paths are rejected if they newly exist in the staging output.
- Without `change-policy.json`, publisher skips the diff (initial runs).

When the file exists but base-artifact/ does not, publish fails with a clear
infrastructure error — runner must materialize base-artifact before invoking
Hermes.

### Decision 4: Identity fields are always hard-checked

Independent of `change-policy.json`, the publisher ALWAYS validates these
identity fields against the claimed shard payload:

- `metadata.json::id` == claimed `challenge_id`
- `metadata.json::category` == claimed `category`

These are not enumerable through change_policy because they're contract-level
invariants. The previous proposal already enforces this in
`promote_claimed_outputs`; we preserve that.

### Decision 5: Atomic publish via temp sibling + rename

Same algorithm as the previous proposal's `promote_claimed_outputs`:

1. For each claimed id, build a temp directory
   `work/challenges/<cat>/.workspace-<workspace_id>-<rand>/` and copy
   validated staging into it.
2. If canonical for that id exists, mv it to
   `work/executions/<workspace_id>/quarantine/<cat>/<dirname>/`.
3. Rename temp → canonical name.
4. On any failure mid-loop, rollback: restore quarantined dir, delete temp.

The previous proposal's tests for atomicity and quarantine path
(`5.14`, `5.15`) carry over.

### Decision 6: Retention sweep

On successful publish, runner clears `./output/` AND `./logs/` of the
just-published workspace (input/manifest stays for audit).

On any publish failure, the workspace stays intact for audit, and the
publisher additionally runs a global retention sweep over
`work/executions/*/quarantine/`:

- Drop quarantine entries older than 7 days.
- If more than 20 workspace-scoped quarantines exist (any age), drop the
  oldest until ≤ 20 remain.

The sweep is the same shape as the existing manual-workspace GC from
the previous proposal's D7 (best-effort, log on error, never blocks the
publish path).

### Decision 7: REMOVAL of the bridge requirement

The previous proposal's "Claimed workspace output is promoted for existing
validation" Requirement is REMOVED in this change's spec delta. The publisher
Requirement in `worker-pool-execution` is its replacement; tests that
referenced the old requirement name are migrated to assert publisher
semantics.

### Decision 8: Runner integration point

The runner today calls `materialize_resume_outputs` → `Hermes` → validator.
This change inserts `publish_workspace_output()` between Hermes and
validator. If publish fails:

- runner returns `status=failed, failure_type=infrastructure`
- existing failed-shard path runs (so BuildReconciler observes failed, not
  lost — important for the Phase 0 hot-fix)
- canonical tree is untouched
- staging stays for audit

If publish succeeds, validator runs against the just-published canonical
tree (same as today).

## Risks / Trade-offs

- The 7-day / last-20 retention policy is a baked-in heuristic. A
  configurable `BUILD_PUBLISHER_RETENTION_DAYS` env var COULD be added later
  without a spec change (D6 phrases it as default values).
- Computing the output manifest hash adds I/O proportional to artifact size.
  For typical CTF challenges (KB–few MB) this is negligible; for very large
  outputs (>100MB Docker contexts) it may add seconds. Acceptable for v1.
- Change-policy enforcement depends on `base-artifact/` being correctly
  materialized by the revision flow (proposal 3). Until proposal 3 lands,
  change-policy.json is never created in practice, so this code path stays
  inert — the publisher is forward-compatible.
- The bridge REMOVAL means tests written against the previous proposal's
  bridge wording need updating. We provide a migration table in the spec
  delta (see "Migration" section in spec.md).
