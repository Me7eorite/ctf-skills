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
- serializes overlapping publications, commits the accepted output set as a
  recoverable batch, and rolls back ordinary failures
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
  `input/`): identity fields (`id`, `flag`, `category`,
  `build_status`) and any path in `change_policy.preserve` MUST equal the
  `base-artifact/` snapshot byte-for-byte; any new file in
  `change_policy.forbid` is rejected.
- **Pre-invocation publication contract**: shard identity, policy, base hashes,
  and resume targets are captured before Hermes runs; publisher rejects any
  post-invocation mutation of host-owned input.
- **Serialized, recoverable publish**: deterministic per-id locks, whole-batch
  validation/staging, a durable journal, then temp sibling → rename; previous canonical directory is
  quarantined to `work/executions/<workspace_id>/quarantine/<category>/<dirname>/`
  (path locked by the previous proposal's spec). Ordinary exceptions roll back
  synchronously; process death is repaired from the journal at bootstrap.
- **Resume target binding**: retry workspaces record the exact materialized
  output directory for every claimed id and require Hermes to edit that
  directory in place, without creating or renaming a second `<id>-<slug>`
  directory.
- **Explicit clean rebuild**: operators can choose a clean rebuild that starts
  with an empty output tree and does not carry forward prior stage evidence.
  This remains distinct from retry/resume.
- **Output manifest hash** (sha256 over path, type, mode, and content records) recorded in
  `input/manifest.json` under `output_manifest_hash` for later audit.
- **Bounded retention**: only after validation reaches terminal success, the
  runner clears `./output/` and `./logs/`; failed output/log staging stays in
  its workspace for diagnosis and is swept per the policy
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
  runner calls publisher instead of the narrow promotion path. Workspace
  materialization, prompt rendering, build orchestration, API, and build-list
  UI also distinguish retry/resume from clean rebuild.
- **Database**: no schema change (execution rows are deferred to the next
  proposal). `output_manifest_hash` is stored in workspace
  `input/manifest.json` for now; persistence moves to DB rows in
  `add-execution-lease-and-fencing` (proposal 3).
- **Filesystem**: success path stays the same (`work/challenges/<cat>/<id>-<slug>/`);
  replaced canonical versions use per-workspace
  `quarantine/<cat>/<dirname>/`; failed output/log staging remains in its
  workspace with a new top-level retention sweep.
- **Compatibility**: the previous proposal's tests for promotion semantics
  remain valid (the new publisher implements a superset). The narrow regex
  remediation already in place (`_match_claimed_id`) keeps working.
- **Operator runbook**: no new ops steps. Retention sweep runs opportunistic
  alongside the existing manual-workspace GC.
- **Out of scope**: lease/fencing, agent registry, supervisor, feedback API,
  change-policy materialization (which depends on revision execution kind
  from proposal 3). Automatic selection or merging of duplicate output
  directories is explicitly out of scope; duplicate publication remains a
  fail-closed publisher error.
