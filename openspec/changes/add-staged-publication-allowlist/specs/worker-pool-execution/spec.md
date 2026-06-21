## ADDED Requirements

### Requirement: Workspace output is published via an owner module with hard allowlist

A dedicated publisher SHALL own all transitions from
`work/executions/<workspace_id>/output/` into the canonical
`work/challenges/<category>/<id>(-<slug>)?/` tree. The runner SHALL NOT
import or invoke the narrow `promote_claimed_outputs` shim that the
previous proposal `add-execution-workspace-and-profile-per-category`
introduced as a compatibility bridge; that bridge is REMOVED in this change.

The publisher SHALL reject all of the following before any rename:

- symlinks anywhere under the output tree
- non-regular-file entries
- `..` path traversal or absolute paths
- directory names that do not match any claimed `challenges[*].id` via
  exact name OR `<id>-<slug>` prefix
- duplicate output directories for the same claimed id
- missing `metadata.json`
- `metadata.json::id` ≠ claimed `challenge_id`
- `metadata.json::category` ≠ claimed `category`

Matching MUST use the claimed-ids set from `input/shard.json` (the
`_match_claimed_id` helper), not a regex over the id shape. Real design-task
ids use `<category>-<hex8>-<NNNN>(-<slug>)?` and the legacy
`^(web|pwn|re)-\d+` pattern is invalid.

#### Scenario: Publisher rejects symlinked output

- **GIVEN** Hermes writes `./output/challenges/web/web-abcdef12-0001-demo`
  as a symlink to a path outside the workspace
- **WHEN** the publisher runs
- **THEN** publication fails before any canonical rename
- **AND** the claimed shard is marked failed before validation runs

#### Scenario: Publisher rejects metadata identity mismatch

- **GIVEN** claimed `challenge_id = "web-abcdef12-0001"`
- **AND** the staging `metadata.json::id` is `web-abcdef12-0002`
- **WHEN** the publisher runs
- **THEN** publication fails before any canonical rename
- **AND** `work/challenges/web/` is unchanged

#### Scenario: Publisher accepts real design-task id format

- **GIVEN** claimed `challenge_id = "web-abcdef12-0001"`
- **AND** Hermes writes `./output/challenges/web/web-abcdef12-0001-demo/`
  with matching metadata
- **WHEN** the publisher runs
- **THEN** publication succeeds (claimed-ids matcher, not legacy regex)
- **AND** the canonical directory exists at
  `work/challenges/web/web-abcdef12-0001-demo/`

### Requirement: Publish is atomic and quarantines the previous canonical version

The publisher SHALL stage each claimed directory in a temporary sibling under
`work/challenges/<category>/.workspace-<workspace_id>-<rand>/`, then atomically
rename it into place. If a canonical directory for the same claimed id
already exists, the publisher SHALL move it to
`work/executions/<workspace_id>/quarantine/<category>/<dirname>/` BEFORE the
rename, and SHALL roll back (restore quarantined dir, delete temp) on any
mid-loop failure.

The publisher SHALL NOT touch unrelated challenge directories under
`work/challenges/<category>/`.

#### Scenario: Quarantine path preserves previous canonical version

- **GIVEN** `work/challenges/web/web-abcdef12-0001-demo/` already exists
- **WHEN** the publisher publishes a new directory for the same claimed id
- **THEN** the existing canonical directory is moved to
  `work/executions/<workspace_id>/quarantine/web/web-abcdef12-0001-demo/`
- **AND** the new directory is atomically renamed into the canonical slot
- **AND** unrelated directories under `work/challenges/web/` are not touched

#### Scenario: Mid-loop failure rolls back

- **GIVEN** two claimed ids and publisher succeeds for the first
- **AND** the second claimed id raises a validation error
- **WHEN** the publisher rolls back
- **THEN** the first id's quarantined previous version is restored
- **AND** the first id's temp directory is deleted
- **AND** the canonical tree is identical to its pre-publish state

### Requirement: Change-policy file enforces preserve and forbid rules

The publisher SHALL honor an optional `change-policy.json` file in the
workspace input dir. When
`work/executions/<workspace_id>/input/change-policy.json` exists, the
publisher SHALL load it and SHALL enforce its `preserve` and `forbid`
rules against `work/executions/<workspace_id>/input/base-artifact/`.
Schema:

```json
{
  "base_artifact_relpath": "challenges/web/web-abcdef12-0001-demo",
  "preserve": ["metadata.json#challenge_id", "validate.sh", ...],
  "forbid":   ["secrets/", ...]
}
```

For each `preserve` entry:

- A bare path (no `#`) is compared byte-for-byte against the same relative
  path under `base-artifact/`.
- A path with `#json_field` selects the named top-level field in both files
  (parsed as JSON) and compares for equality.

For each `forbid` entry: if it newly exists in the staging output and did
NOT exist under `base-artifact/`, publication fails.

If `change-policy.json` exists but `base-artifact/` does not, publication
fails with a clear infrastructure-error message: the revision flow that
created the change-policy is responsible for materializing base-artifact
beforehand.

When `change-policy.json` is absent (initial-run case), the publisher
SHALL skip change-policy enforcement.

#### Scenario: Preserve byte-mismatch fails closed

- **GIVEN** `change-policy.json` lists `validate.sh` under `preserve`
- **AND** the staging `validate.sh` differs from `base-artifact/.../validate.sh`
- **WHEN** the publisher runs
- **THEN** publication fails
- **AND** the canonical tree is unchanged

#### Scenario: Preserve JSON-field mismatch fails closed

- **GIVEN** `change-policy.json` lists `metadata.json#challenge_id` under `preserve`
- **AND** the staging metadata's `challenge_id` differs from base-artifact's
- **WHEN** the publisher runs
- **THEN** publication fails

#### Scenario: Forbid entry newly added fails closed

- **GIVEN** `change-policy.json` lists `secrets/` under `forbid`
- **AND** the staging output newly contains `secrets/key.pem`
- **WHEN** the publisher runs
- **THEN** publication fails

#### Scenario: Change-policy without base-artifact fails closed

- **GIVEN** `change-policy.json` exists
- **AND** `input/base-artifact/` does not exist
- **WHEN** the publisher runs
- **THEN** publication fails with a message instructing the operator that
  the revision flow must materialize base-artifact before invoking Hermes

#### Scenario: No change-policy file behaves like initial run

- **GIVEN** `change-policy.json` does NOT exist
- **WHEN** the publisher runs
- **THEN** no change-policy diff is performed
- **AND** publication proceeds (subject to identity-field hard check and
  allowlist)

### Requirement: Successful publish records output manifest hash

On successful publish, the publisher SHALL compute a deterministic
`output_manifest_hash` over the published canonical tree
(sha256 of sorted `<relpath>:sha256:<file_sha256>\n` lines), and SHALL
write it to `work/executions/<workspace_id>/input/manifest.json` under the
`output_manifest_hash` key.

The hash MUST be stable across reruns of the same canonical tree
(byte-equivalent output → identical hash). A later proposal will move this
field to a database execution row.

#### Scenario: Manifest hash is deterministic

- **GIVEN** the publisher publishes the same canonical tree twice (after a
  rollback / restore for example)
- **WHEN** both runs complete successfully
- **THEN** both `output_manifest_hash` values are equal

#### Scenario: Manifest hash changes when output bytes change

- **GIVEN** two publish runs whose canonical outputs differ by even one byte
- **WHEN** both runs complete successfully
- **THEN** their `output_manifest_hash` values are different

### Requirement: Bounded retention sweep keeps quarantine disk-bounded

On every publish call (success or failure), the publisher SHALL perform an
opportunistic sweep over `work/executions/*/quarantine/`:

- Delete quarantine entries with mtime older than 7 days.
- If more than 20 workspace-scoped quarantine roots exist (any age), delete
  the oldest until ≤ 20 remain.

Sweep errors (permission/busy) MUST be logged and MUST NOT block the publish
result. The sweep is independent of the manual-workspace GC introduced in the
previous proposal; both run together at workspace bootstrap.

On successful publish, the publisher SHALL also clear `./output/` and
`./logs/` of the just-published workspace immediately (keep `./input/` for
audit).

#### Scenario: Old quarantine is reclaimed

- **GIVEN** `work/executions/<W1>/quarantine/web/web-old/` has mtime
  older than 7 days
- **AND** `work/executions/<W2>/quarantine/web/web-fresh/` has mtime
  within 7 days
- **WHEN** the publisher's retention sweep runs
- **THEN** `web-old` is deleted
- **AND** `web-fresh` is kept

#### Scenario: Quarantine count cap evicts oldest

- **GIVEN** 21 workspace-scoped quarantine roots exist (all fresher than 7
  days)
- **WHEN** the publisher's retention sweep runs
- **THEN** the oldest is deleted, leaving 20

#### Scenario: Sweep failure does not block publish

- **GIVEN** one quarantine entry has a permission error preventing deletion
- **WHEN** the publisher runs and the sweep encounters that error
- **THEN** the sweep logs a warning
- **AND** the publish result is unaffected (success or failure as
  determined by the publish itself)

### Requirement: Publish failure marks the shard failed, not lost

The runner SHALL classify any publisher failure as a build failure (not a
disappearance). When the publisher fails for any reason (allowlist
rejection, change-policy diff failure, identity mismatch, atomic-rename
failure), the runner SHALL return `status=failed,
failure_type=infrastructure` and SHALL move the claimed shard through the
existing failed-shard path so the BuildReconciler observes `failed`, not
`lost`.

This requirement specifically prevents the Phase 0 lost-race regression:
publish failure happens AFTER worker claim, so the shard is in
`running/<basename>.<worker>.json` at the time of publish failure. Marking
the shard failed via the existing path triggers reconciler's `state=failed`
observation cleanly.

#### Scenario: Publish failure surfaces as failed, never lost

- **GIVEN** the publisher rejects a publication due to a preserve mismatch
- **WHEN** the runner records the outcome
- **THEN** the build attempt status becomes `failed` with
  `failure_type=infrastructure`
- **AND** the BuildReconciler subsequently observes `state=failed` (NOT a
  late `lost` reclassification)
- **AND** the canonical tree under `work/challenges/` is unchanged
