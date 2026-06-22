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
- Serialized, failure-atomic batch publication with durable crash recovery.
- Change-policy hard diff for identity fields and explicit preserve/forbid
  paths.
- Bounded retention for failed staging (audit-friendly, disk-bounded).
- Output manifest hash recorded for downstream audit hand-off.
- Bind every resume invocation to the exact pre-existing output directory so
  Hermes edits it in place instead of creating a second slug directory.
- Separate retry/resume from clean rebuild so operators can intentionally
  discard prior filesystem and progress evidence.

**Non-Goals:**

- No execution row / lease / fencing token (next proposal).
- No agent registry, supervisor, slots (proposals 4-5).
- No live feedback API for change-policy (deferred to revision proposal).
- No change to research/design output paths.
- No live tailing changes (already in place from previous proposal).

## Decisions

### Decision 1: Publisher is a separate module, called from the runner

`hermes/build_publisher.py` exposes preparation and publication functions. This
is intentionally inside the `hermes` package: the live repository enforces a
dependency direction where `hermes` may depend only on `domain` and `core`, and
must not import `services`. The publisher is part of the execution-workspace
boundary and reuses workspace-private validation helpers, so placing it under
`hermes` preserves the existing architecture while still giving the runner a
direct publisher entry point:

```python
def prepare_publication_contract(
    paths: ProjectPaths,
    workspace: ExecutionWorkspace,
    payload: Mapping[str, Any],
    *,
    resume_output_targets: Mapping[str, str],
) -> PublicationContract: ...

def publish_workspace_output(
    paths: ProjectPaths,
    workspace: ExecutionWorkspace,
    *,
    contract: PublicationContract,
) -> PublishResult: ...
```

The runner prepares the immutable contract before invoking Hermes, then calls
the publisher with that in-memory contract after Hermes returns and BEFORE
validation. The contract captures normalized shard identity, parsed
change-policy, base-artifact digests, input hashes, execution mode, and resume
targets. The publisher re-verifies host-owned inputs against the captured
digests before trusting workspace state. For `manifest.json`, the contract
hashes an immutable projection that excludes only publisher-owned runtime
fields such as `output_manifest_hash` and `publish_generation`; all identity,
input hashes, timeout, and resume-target fields remain covered. The narrow
`promote_claimed_outputs` from the previous proposal becomes the
publisher's implementation core (it already does symlink/traversal/id
checks). New responsibilities (manifest hash, change-policy diff, retention
sweep) are layered around it.

All publisher-owned runtime state lives under
`work/executions/<workspace_id>/state/`, kept separate from agent-readable
`input/`:

- `state/publish-journal.json` — current in-flight journal (absent at
  contract-capture time outside of bootstrap recovery).
- `state/publish-status.json` — last terminal status marker for the sweep.
- `state/highest-committed-generation.json` — single high-water file
  carrying the largest committed `publish_generation` plus its
  `output_manifest_hash`. Authoritative monotonicity source; never deleted
  while the workspace is alive. A full per-generation archive is
  deliberately NOT kept by this proposal (history belongs to proposal #6).

Contract input-hash exclusions are enumerated in code: every path under
`state/` plus the publisher-owned manifest projection fields
(`output_manifest_hash`, `publish_generation`). Adding a new
publisher-owned file later requires deliberately updating that exclusion
list. The publisher creates state files only after contract verification.
The final host mutation of `manifest.json` is governed by the journal and
is not mistaken for an agent input mutation. Each `succeeded` publication
in a validation-repair loop increments `manifest.publish_generation`, uses
a fresh journal, and atomically updates the high-water file before Hermes
can start the next repair invocation. `noop` publications touch none of
these. Thus one pre-invocation contract can safely authorize multiple
publisher-owned hash updates without allowing agent mutation of immutable
manifest fields.

Why a separate module: the same logic will later be invoked from
`BuildPoolSupervisor` (proposal 5) with extra fencing-token args. Splitting
it now avoids re-plumbing the runner twice.

### Decision 2: Output manifest hash semantics

`output_manifest_hash` is computed over the fully validated temporary copies
that are byte-equivalent to the eventual canonical trees. Hash records include
relative POSIX path, entry type, normalized permission mode, and content hash;
therefore executable-bit changes and empty-directory changes are observable.
Normalized mode means the low permission bits `st_mode & 0o777`; ownership,
mtime, inode, ACL, and other host-specific metadata are excluded.
Records use a canonical length-prefixed encoding rather than delimiter-only
lines, avoiding ambiguity from legal filename characters. The batch hash is:

```
sha256(canonical_encode(sorted(entry_records_for_all_claimed_ids)))
```

It is written to `work/executions/<workspace_id>/input/manifest.json` under
the `output_manifest_hash` key. The manifest is the visible workspace record,
but the hash is authoritative only when paired with
`state/highest-committed-generation.json` at the same generation/hash. The
next proposal will move it onto an execution row.

Why a manifest hash (not just per-file hashes): downstream audit
(`add-execution-audit-snapshots`, proposal 6) needs a single value that
distinguishes "same artifacts as a previous run" from "different artifacts".
The publisher re-hashes canonical trees after rename before committing the
manifest update. A mismatch triggers rollback. The manifest is updated by
temp-file plus atomic rename while publication locks are still held; a
manifest-write failure also rolls back the canonical batch. The durable
journal lets bootstrap recovery finish or roll back a process death between
canonical rename and manifest update.

### Decision 3: Change-policy diff is opt-in via input file

When `work/executions/<workspace_id>/input/change-policy.json` exists before
Hermes invocation, `prepare_publication_contract` MUST parse and capture it.
The publisher MUST honor the captured policy and reject any later mutation.
Schema:

```json
{
  "base_artifact_relpath": "challenges/web/web-abcdef12-0001-demo",
  "preserve": ["metadata.json#id", "metadata.json#flag",
               "metadata.json#category", "metadata.json#build_status",
               "validate.sh"],
  "forbid": ["secrets/"]
}
```

- `base_artifact_relpath` is a normalized relative POSIX path and resolves against
  `work/executions/<workspace_id>/input/base-artifact/` (materialized by the
  revision flow in proposal 3).
- `base_artifact_relpath`, `preserve`, and `forbid` entries reject empty path
  components, `.`, `..`, absolute paths, backslashes, NUL, symlinks, and any
  resolution outside their declared roots. Unknown schema keys, duplicate
  entries, wrong types, and missing selected JSON fields are errors.
- `preserve` paths are checked byte-for-byte against base. `path#json_field`
  syntax selects a specific JSON field within a file.
- `forbid` paths are prefixes. Every descendant relative path in staging is
  compared with the base inventory; any newly added descendant under a
  forbidden prefix is rejected even when the prefix directory already existed.
- Without `change-policy.json`, publisher skips the diff (initial runs).

When the file exists but base-artifact/ does not, contract preparation fails
before Hermes is invoked. The base tree and all host-owned input files are
hashed into the contract; post-invocation changes fail publication.

### Decision 4: Identity fields are always hard-checked

Independent of `change-policy.json`, the publisher ALWAYS validates these
identity fields against the claimed shard payload:

- `metadata.json::id` == claimed `challenge_id`
- `metadata.json::category` == claimed `category`

These are not enumerable through change_policy because they're contract-level
invariants. The previous proposal already enforces this in
`promote_claimed_outputs`; we preserve that.

### Decision 5: Serialized, recoverable batch publish

A sequence of directory renames cannot provide literal multi-directory
transactional atomicity across process death. This proposal therefore promises
serialization, synchronous rollback for ordinary failures, and deterministic
crash recovery rather than claiming an impossible filesystem transaction.

1. Acquire cross-process exclusive locks for every `(category, claimed_id)` in
   sorted order under `paths.build_publisher_locks` (resolves to
   `work/locks/build-publisher/`). The directory is added to `ProjectPaths` and
   created during `initialize()`; same-host operators do not need to pre-create
   it. Lock primitive is `fcntl.flock(LOCK_EX)` on a regular file whose
   basename is a hex digest of `(category, claimed_id)` (no host paths in the
   filename to keep error messages safe). The default acquisition timeout is 30
   seconds (`BUILD_PUBLISH_LOCK_TIMEOUT_SECONDS`); invalid configuration fails
   `initialize()`/preflight rather than silently falling back. Hold them through
   manifest commit or rollback. The lock tree MAY live on a different
   filesystem than `work/challenges/` and `work/executions/`; only temp,
   canonical, and quarantine are required to be co-located (Decision 5 step 2).
   Windows hosts are out of scope for the publisher runtime path; preflight
   rejects them with a clear unsupported-platform message rather than silently
   using a different primitive. Local Windows development may still run
   non-publisher tests, but publisher lock/recovery tests must be POSIX-gated
   or run under a POSIX CI/deployment environment.
2. Validate the entire output and policy before changing canonical state.
   Enforce configured file-count, byte-count, path-depth, and component-length
   limits while walking with `lstat`; never follow symlinks. Defaults are 2 GiB
   total regular-file bytes, 50,000 regular files, depth 64, and 255 UTF-8 bytes
   per component; environment overrides (`BUILD_PUBLISH_MAX_BYTES`,
   `BUILD_PUBLISH_MAX_FILES`, `BUILD_PUBLISH_MAX_DEPTH`,
   `BUILD_PUBLISH_MAX_COMPONENT_BYTES`) must parse as positive integers.
3. Copy every candidate to a temp sibling
   `work/challenges/<cat>/.workspace-<workspace_id>-<rand>/`, then independently
   revalidate and hash every temp tree. No canonical rename begins until all
   claimed ids are staged successfully.
4. Write and fsync `state/publish-journal.json` containing source, temp,
   canonical, quarantine, expected hash, and phase for the whole batch.
5. Move existing canonicals to unique quarantine paths and rename all temps
   into place, updating and fsyncing the journal after every step.
6. Re-hash canonicals, atomically update the manifest, mark the journal
   committed, then release locks.
7. On an ordinary exception, walk the journal in reverse: remove newly placed
   canonicals, restore quarantined predecessors, delete temps, and record the
   rollback result. Bootstrap reconciliation acquires the same locks and
   performs the same recovery for an incomplete journal after process death.

Temp, canonical, and quarantine paths MUST be on the same filesystem; this is
checked before the commit phase. A cross-device layout fails before canonical
mutation. The previous proposal's quarantine path is preserved, with a unique
transaction suffix when the fixed basename already exists.

### Decision 6: Terminal cleanup and race-safe retention sweep

Publisher success is followed by host validation and may be followed by one or
more validation-repair invocations in the same workspace. The publisher MUST
NOT clear `./output/` or `./logs/`. The runner clears them only after validation
reaches terminal success. Validation failure and publisher failure retain both
trees for diagnosis. Every terminal outcome writes a host-owned
timestamp/status marker.

An opportunistic sweep handles replaced-canonical quarantine and failed
output/log staging as one workspace-scoped retention root. It uses the terminal
marker timestamp, not mutable child mtime, and applies both constraints: retain
roots only while newer than 7 days and then retain at most the newest 20 roots
that contain quarantine or failed staging. It skips workspaces with an
incomplete publish journal or any publisher lock it cannot acquire
non-blockingly. Sweep errors are warnings and never change publish outcome.

### Decision 7: REMOVAL of the bridge requirement

The previous proposal's "Claimed workspace output is promoted for existing
validation" Requirement is REMOVED in this change's spec delta. The publisher
Requirement in `worker-pool-execution` is its replacement; tests that
referenced the old requirement name are migrated to assert publisher
semantics.

### Decision 8: Runner integration point

The runner today calls `materialize_resume_outputs` → `Hermes` → validator.
This change prepares the publication contract after materialization and before
Hermes, then inserts `publish_workspace_output()` between Hermes and validator.
If publish fails:

- runner returns `status=failed, failure_type=infrastructure`
- existing failed-shard path runs (so BuildReconciler observes failed, not
  lost — important for the Phase 0 hot-fix)
- canonical tree is untouched
- staging stays for audit

If publish succeeds, validator runs against the just-published canonical
tree (same as today).

### Decision 9: Resume target binding is explicit and fail-closed

`materialize_resume_outputs` returns the exact workspace-relative directory
for each claimed id copied from the canonical tree. The runner writes that
mapping to `input/manifest.json` as `resume_output_targets`, for example:

```json
{
  "resume_output_targets": {
    "pwn-c8c19354-0001":
      "output/challenges/pwn/pwn-c8c19354-0001-stack-notes"
  }
}
```

The rendered resume plan names the same exact directory and instructs Hermes
to edit it in place. When a target is present, creating or renaming another
directory matching the same claimed id violates the execution contract. The
publisher still detects and rejects duplicate-id directories before any
canonical mutation; it MUST NOT guess which directory is newer or merge them.

This closes the ambiguity where retry materialization copies
`<id>-<old-slug>` and Hermes independently creates `<id>-<new-slug>` from the
generic `<id>-<slug>` prompt.

### Decision 10: Retry and clean rebuild are different operations

The existing retry endpoint remains resume-oriented. It links the prior shard,
materializes the existing canonical artifact, computes carry-forward evidence,
and emits `execution_mode: "resume"` in the new shard payload.

A separate clean-rebuild action emits `execution_mode: "clean"`. In clean
mode the runner MUST NOT call `materialize_resume_outputs`, MUST NOT use the
prior shard as a resume source, and MUST compute the plan as a first-time run.
The canonical artifact remains untouched until the new workspace contains one
valid output directory and publication succeeds; normal quarantine then
preserves the previous canonical version.

Payloads without `execution_mode` retain current compatibility behavior:
payloads with `resume_from_shard_basename` are `resume`; all others are
`clean`.

Only the literal values `resume` and `clean` are valid. Explicit `resume`
requires a safe basename-only `resume_from_shard_basename`; explicit `clean`
forbids that field. Contradictory or unknown values fail preflight before
Hermes. The mode used for plan computation, materialization, prompt rendering,
and publication contract MUST be one normalized in-memory value.

### Decision 11: Clean rebuild preserves orchestration concurrency rules

Clean rebuild has the same eligibility boundary as retry: only the latest
failed/lost attempt of a `build_failed` design task can be rebuilt. This
proposal's idempotency guarantee is same-key only: concurrent requests with the
same idempotency key produce at most one new attempt, while two deliberate
requests with different keys are separate submissions until proposal 3 adds
execution-row lease/fencing. The service performs eligibility re-check while
preparing the attempt identity and shard payload. The API returns the existing
attempt for a replay of the same idempotency key, rejects stale source
attempts, and never deletes the canonical predecessor when submitting work.
Operator confirmation is enforced by an explicit request field, not only by
browser JavaScript.

### Decision 12: Publisher errors remain diagnosable by phase

The current runner exposes only `failure_type=infrastructure`; changing the
global failure taxonomy is outside this proposal. The publisher nevertheless
returns stable phases (`contract`, `allowlist`, `policy`, `limits`, `stage`,
`commit`, `manifest`, `rollback`, `recovery`) and the offending claimed id/path
where safe. Runner error messages and terminal markers include this phase so an
agent-output duplicate can be distinguished operationally from filesystem or
rollback failure without parsing free-form exception text.

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
- Prompt compliance is not a security boundary. Hermes may still violate the
  resume target instruction, so publisher duplicate detection remains
  mandatory and failed staging remains available for diagnosis.
- Multi-directory rename is not instantaneously atomic across process death.
  Readers outside the publisher lock protocol could briefly observe an
  intermediate batch. Current validation and publisher paths use the locks;
  database fencing and broader reader coordination remain follow-up work.
- The bridge REMOVAL means tests written against the previous proposal's
  bridge wording need updating. We provide a migration table in the spec
  delta (see "Migration" section in spec.md).
