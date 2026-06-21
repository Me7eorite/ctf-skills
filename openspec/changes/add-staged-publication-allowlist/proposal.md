## Why

The just-archived `add-execution-workspace-and-profile-per-category` change
introduced a **narrow compatibility bridge** in `hermes-execution-protocol`:
the runner promotes claimed challenge ids from `./output/` back to
`work/challenges/<category>/` so existing validation can run. That bridge has
no fencing token, no operator approval gate, and no general allowlist — it is
explicitly marked "SHALL be REMOVED by add-staged-publication-allowlist".

This proposal is that removal. It introduces a dedicated **publisher** that
owns the write boundary into `work/challenges/`. The publisher:

- enforces a strict allowlist (symlink/traversal/scope rejection, identity
  field validation, change-policy diff)
- consumes a **change policy** materialized into the workspace for revision
  iterations (see `add-execution-lease-and-fencing` for the iteration model)
- atomically publishes the complete accepted output set or publishes nothing
- records output manifest hashes for downstream audit
- retains failed staging under a bounded retention policy

It does NOT add execution rows, fencing tokens, agents, supervisor, or feedback
APIs — those land in subsequent proposals.

## What Changes

- **New capability `worker-pool-execution`** (its first Requirement; later
  proposals add lease/fencing, supervisor, agent ownership, audit snapshots
  to the same capability).
- Replace the narrow promotion logic from the previous change with a
  publisher module that owns `./output/` → `work/challenges/` transitions.
- **Hard allowlist** rejecting symlinks, special files, absolute paths,
  `..` traversal, unexpected category roots, non-claimed challenge ids,
  duplicate-id directories, and metadata id/category mismatch.
- **Change-policy hard diff** (when `change-policy.json` is present in
  `input/`): identity fields (`challenge_id`, `flag`, `category`,
  `build_status`) and any path in `change_policy.preserve` MUST equal the
  `base-artifact/` snapshot byte-for-byte; any new file in
  `change_policy.forbid` is rejected.
- **Atomic publish**: temp sibling → rename; previous canonical directory is
  quarantined to `work/executions/<workspace_id>/quarantine/<category>/<dirname>/`
  (path locked by the previous proposal's spec).
- **Output manifest hash** (sha256 over the published tree) recorded in
  `input/manifest.json` under `output_manifest_hash` for later audit.
- **Bounded retention**: on successful publish, runner clears the
  `./output/` and `./logs/` staging immediately; failed staging is kept under
  `work/executions/<workspace_id>/quarantine/.../` per the policy
  (default: last 20 failures across all workspaces OR 7 days, whichever is
  stricter).
- **Modify** `hermes-execution-protocol`: REMOVE the
  "Claimed workspace output is promoted for existing validation" Requirement
  that the previous change introduced as a bridge; the publisher Requirement
  in `worker-pool-execution` takes over.

## Capabilities

### New Capabilities

- `worker-pool-execution` (first appearance; later proposals layer
  lease/fencing/supervisor/agent/audit Requirements onto it).

### Modified Capabilities

- `hermes-execution-protocol`: REMOVE the narrow promotion Requirement
  introduced as a compatibility bridge.

## Impact

- **Code**: a new `services/build_publisher.py` (or extension of
  `hermes/workspace.py::promote_claimed_outputs`) owning the boundary. The
  runner calls publisher instead of the narrow promotion path.
- **Database**: no schema change (execution rows are deferred to the next
  proposal). `output_manifest_hash` is stored in workspace
  `input/manifest.json` for now; persistence moves to DB rows in
  `add-execution-lease-and-fencing` (proposal 3).
- **Filesystem**: success path stays the same (`work/challenges/<cat>/<id>-<slug>/`);
  failure path goes to existing per-workspace `quarantine/<cat>/<dirname>/`
  with a new top-level retention sweep.
- **Compatibility**: the previous proposal's tests for promotion semantics
  remain valid (the new publisher implements a superset). The narrow regex
  remediation already in place (`_match_claimed_id`) keeps working.
- **Operator runbook**: no new ops steps. Retention sweep runs opportunistic
  alongside the existing manual-workspace GC.
- **Out of scope**: lease/fencing, agent registry, supervisor, feedback API,
  change-policy materialization (which depends on revision execution kind
  from proposal 3).
