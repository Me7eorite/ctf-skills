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

Before invoking Hermes, the runner SHALL construct an immutable in-memory
publication contract containing normalized shard identity, execution mode,
resume targets, parsed change policy, base-artifact digests, and hashes of all
host-owned workspace inputs. Before publication, the publisher SHALL verify
those inputs against the captured hashes and SHALL reject any post-invocation
mutation. It SHALL NOT trust policy, shard identity, resume targets, or base
hashes re-read only after Hermes returns.

For `manifest.json`, contract verification SHALL compare an immutable
projection that excludes only publisher-owned `output_manifest_hash` and
`publish_generation`. Identity, `input_hashes`, timeout, execution mode, and
resume-target fields SHALL remain protected (execution mode is protected via
the hashed shard snapshot rather than a duplicate manifest field). Every
publication whose result is `succeeded` (i.e. canonical state changed) SHALL
increment `publish_generation`, use a fresh journal, and update the
high-water file (see below) before the next Hermes repair invocation.
Publications whose result is `noop` (see the no-op scenario below) SHALL
NOT touch `publish_generation`, the journal, or the high-water file.

All publisher-owned runtime state SHALL live under
`work/executions/<workspace_id>/state/`, NOT under `input/`. This separates
host-runtime artifacts from agent-readable inputs and keeps contract
verification clean (see "input-hash exclusions" below). The minimum set is:

- `state/publish-journal.json` — current in-flight journal (present only
  during a publication; archived/removed at terminal phase).
- `state/publish-status.json` — last terminal status marker used by the
  retention sweep.
- `state/highest-committed-generation.json` — a single high-water file
  recording the largest committed `publish_generation` and the
  corresponding `output_manifest_hash`. Written atomically (temp +
  rename) at the end of every successful publication. This file is the
  authoritative source of monotonicity; it SHALL NOT be deleted while the
  workspace is alive. A full historical archive is intentionally NOT kept
  by this proposal (audit trails belong to proposal #6).

Contract input-hash exclusions: the publisher's contract input-hash SHALL
exclude every path under `state/` and SHALL exclude `manifest.json`'s
publisher-owned projection fields (`output_manifest_hash`,
`publish_generation`). The host-owned input set that IS hashed comprises
`shard.json`, `change-policy.json` (when present), `base-artifact/` (when
present), and the rest of `manifest.json`. Implementations SHALL enumerate
the excluded set in code (not regex over `state/`), so adding a new
publisher-owned file later requires an explicit update.

Because `publish_generation` and `output_manifest_hash` are excluded from
contract verification, the publisher SHALL NOT trust their on-disk values
unsupervised. Specifically:

- The publisher SHALL read the last committed `publish_generation` from
  `state/highest-committed-generation.json` (or 0 when that file does not
  exist), NOT from `manifest.json`. The new generation SHALL be strictly
  greater than that value; any other manifest value is ignored for
  monotonicity purposes and replaced atomically by the new generation
  during the manifest commit step.
- The publisher SHALL treat `manifest.output_manifest_hash` as authoritative
  ONLY when `manifest.publish_generation` matches the generation recorded in
  `state/highest-committed-generation.json` and the high-water file records
  the same `output_manifest_hash`. A hash recorded in `manifest.json` whose
  generation/hash pair does not match the high-water file SHALL be ignored
  for downstream audit and re-derived from the canonical tree before the next
  commit.

#### Scenario: Publish generation must advance past the high-water file

- **GIVEN** `state/highest-committed-generation.json` records
  `publish_generation = 3`
- **AND** `manifest.json` reports `publish_generation = 999` (agent-written
  during Hermes execution, excluded from contract verification)
- **WHEN** the publisher prepares the next publication
- **THEN** it ignores the manifest value
- **AND** it writes a new journal at `publish_generation = 4`
- **AND** the manifest commit replaces both fields atomically

#### Scenario: Uncommitted manifest hash is not authoritative

- **GIVEN** a prior publication crashed after manifest write but before
  the high-water file was advanced to the same generation/hash pair
- **WHEN** bootstrap reconciles the workspace
- **THEN** recovery uses the journal phase to either finalize high-water or
  roll back the predecessor canonical tree
- **AND** any downstream audit treats the manifest's `output_manifest_hash`
  as authoritative only after it matches the high-water file

Everything under `state/` SHALL be reserved for host runtime writes; agents
have no read or write need for these files and contract verification SHALL
fail if Hermes creates anything under `state/`. Among these:

- `state/publish-journal.json` MUST be absent when a new contract is
  captured unless bootstrap is recovering an existing journal.
- `state/highest-committed-generation.json` MAY exist and SHALL NOT be
  deleted by contract capture.
- `state/publish-status.json` MAY exist (it records the last terminal
  marker) and SHALL NOT be deleted by contract capture.

Publisher-created files under `state/` and the journaled final manifest
update are authorized runtime mutations and SHALL not be confused with
agent input mutation.

These reserved paths SHALL be uniquely scoped to one workspace by the
`workspace_id` segment of their parent path; no other workspace ever reads or
writes them. Each journal record SHALL be identified by the tuple
`(workspace_id, publish_generation)`. Starting a new execution in an existing
workspace SHALL fail preflight when `state/publish-journal.json` is in a
phase other than `committed`, and SHALL force the bootstrap recovery path
instead of silently overwriting the journal. Recovery is idempotent under
the same `(workspace_id, publish_generation)` and SHALL refuse to recover
a journal whose generation is less than the value in
`state/highest-committed-generation.json`.

After bootstrap recovery completes successfully (either finalize or
rollback), the publisher SHALL treat the workspace as in its post-recovery
canonical state and proceed with whatever fresh contract preparation or
publication the runner requests next. Bootstrap recovery is NOT itself
counted as a publication; if it finalizes an incomplete journal, the
high-water file is updated to that finalized generation, and no new
generation is consumed. If it rolls back, no generation is consumed and the
high-water file is unchanged.

Crash window between manifest write and high-water update: the commit
sequence (canonical rename → manifest write → high-water update) can crash
between any pair of steps. If recovery finds a `committed` journal whose
generation is strictly greater than the high-water value, this means the
manifest was written but the high-water update did not land. Recovery
SHALL push the high-water file forward to the journal's generation
atomically (temp + rename), then mark the journal archived/removed. This
operation is idempotent: re-running it on a workspace already at the same
generation is a no-op. Recovery SHALL NOT roll back canonical state in
this case because the journal is `committed` — the canonical and manifest
are already the new authoritative version.

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

#### Scenario: Hermes mutates host-owned publication policy

- **GIVEN** the runner captured `change-policy.json` and base-artifact hashes
  before invoking Hermes
- **AND** Hermes changes either the policy, base artifact, shard snapshot, or
  resume target manifest field
- **WHEN** the publisher verifies the publication contract
- **THEN** publication fails before canonical mutation
- **AND** the error identifies the `contract` phase

#### Scenario: Validation repair may republish with the original contract

- **GIVEN** initial publication legitimately updated `output_manifest_hash`
  and `publish_generation`
- **AND** host validation requests a repair in the same workspace
- **WHEN** the repair publication verifies the original contract
- **THEN** publisher-owned manifest fields do not cause a false contract error
- **AND** any mutation to an immutable manifest field still fails

#### Scenario: Repair with byte-identical output is a publisher no-op

- **GIVEN** the prior publication committed `output_manifest_hash = H`
- **AND** the repair invocation produced staging whose computed hash equals
  `H` (byte-identical output — for example because the runner's
  no-changes detector aborted the repair before Hermes edited anything)
- **WHEN** the publisher prepares to publish
- **THEN** it MUST short-circuit: no journal is written, `publish_generation`
  is unchanged, no quarantine is created, no canonical rename runs, and the
  retention sweep is not triggered
- **AND** the publisher reports a `noop` outcome distinct from `succeeded`

#### Scenario: Runner observes noop and exits the repair loop

- **GIVEN** the publisher returned `noop` for the current repair iteration
- **WHEN** the runner receives that outcome
- **THEN** it MUST exit the validation-repair loop without invoking the
  validator again (the canonical tree is byte-identical to the previous
  iteration, so re-running validation would only produce duplicate events)
- **AND** it MUST NOT advance the attempt's progress percent or write a
  terminal marker on the noop iteration itself
- **AND** the runner MUST finish the attempt from the most recent validation
  results already recorded before the noop; if those results still contain a
  failed challenge, the attempt is marked failed and the normal terminal
  failure marker is written by the finalization path

### Requirement: Publisher bounds resource consumption before staging

The publisher SHALL enforce configurable limits for total regular-file bytes,
regular-file count, path depth, and path-component byte length while scanning
output with `lstat`. It SHALL never follow a symlink while calculating limits
or copying. All candidates SHALL pass limits before any canonical mutation,
and each temporary copy SHALL be independently rescanned before commit to
detect a changed entry or type.

Defaults SHALL be 2 GiB total regular-file bytes, 50,000 regular files, depth
64, and 255 UTF-8 bytes per path component. Environment overrides and the
publisher lock timeout (default 30 seconds) SHALL parse as positive integers;
invalid configuration SHALL fail startup/preflight rather than silently use a
different value.

#### Scenario: Oversized output fails before canonical mutation

- **GIVEN** workspace output exceeds the configured byte or file-count limit
- **WHEN** the publisher scans the complete claimed output set
- **THEN** publication fails in the `limits` phase
- **AND** no temporary or canonical destination is committed

### Requirement: Resume executions bind each claimed id to one exact output directory

For a resume execution, the runner SHALL copy at most one canonical directory
per claimed id into the workspace output tree and SHALL record its exact
workspace-relative path in `input/manifest.json::resume_output_targets`. The
rendered resume plan SHALL name that exact path and require Hermes to edit the
directory in place without renaming it or creating another directory that
matches the same claimed id.

The publisher SHALL remain the fail-closed enforcement boundary. If output
contains more than one directory matching a claimed id, publication SHALL fail
before any canonical rename. The publisher SHALL NOT select, merge, or delete
one of the ambiguous candidates automatically.

`resume_output_targets` is an advisory binding used to render the prompt and
to enrich diagnostics; it SHALL NOT be the publisher's truth source for what
to publish. The publisher SHALL enumerate `./output/` directly under the
allowlist rules and decide what to publish from that enumeration. When the
enumeration disagrees with `resume_output_targets` (e.g. an id is missing
from output, or the published basename differs from the recorded target),
publication SHALL fail in the `contract` phase with both the recorded target
and the offending observed path, and the canonical tree SHALL be unchanged.

#### Scenario: Retry edits the materialized directory in place

- **GIVEN** canonical directory
  `work/challenges/pwn/pwn-c8c19354-0001-stack-notes/` exists
- **WHEN** a resume retry workspace is prepared
- **THEN** that directory is copied to
  `output/challenges/pwn/pwn-c8c19354-0001-stack-notes/`
- **AND** `resume_output_targets["pwn-c8c19354-0001"]` records that exact path
- **AND** the rendered prompt names the exact path as the only permitted
  directory for that claimed id

#### Scenario: Resume agent creates a second slug directory

- **GIVEN** the materialized resume target is
  `pwn-c8c19354-0001-stack-notes/`
- **AND** Hermes also creates `pwn-c8c19354-0001-new-slug/`
- **WHEN** the publisher validates workspace output
- **THEN** publication fails with a duplicate-id error
- **AND** the canonical tree is unchanged

#### Scenario: Resume target binding disagrees with staging

- **GIVEN** `resume_output_targets["web-abcdef12-0001"]` records
  `output/challenges/web/web-abcdef12-0001-demo`
- **AND** the only directory the agent left in staging is
  `output/challenges/web/web-abcdef12-0001-other`
- **WHEN** the publisher validates workspace output
- **THEN** publication fails in the `contract` phase referencing both paths
- **AND** the canonical tree is unchanged

### Requirement: Clean rebuild starts without prior artifact or progress carry-forward

Build orchestration SHALL expose clean rebuild as an operation distinct from
resume retry. A clean-rebuild shard SHALL contain `execution_mode: "clean"`.
The runner SHALL start it with an empty output tree, SHALL NOT materialize the
existing canonical artifact, SHALL NOT use a prior shard as its resume source,
and SHALL compute all authoring stages as a first-time run.

The existing canonical artifact SHALL remain unchanged until clean output
passes publisher checks. Successful publication SHALL quarantine and replace
the prior canonical artifact using the normal atomic publication algorithm.

For compatibility, a payload without `execution_mode` SHALL be interpreted as
`resume` when `resume_from_shard_basename` is present and as `clean` otherwise.
Only literal `resume` and `clean` values are valid. Explicit `resume` SHALL
require a safe basename-only `resume_from_shard_basename`; explicit `clean`
SHALL reject that field. The normalized mode SHALL be used consistently for
plan computation, materialization, prompt rendering, and contract creation.

#### Scenario: Operator requests clean rebuild after a failed attempt

- **GIVEN** one canonical directory already exists for the claimed id
- **WHEN** the operator selects clean rebuild
- **THEN** the new workspace output starts empty
- **AND** no stage is marked passed by carry-forward from the prior shard
- **AND** Hermes can create exactly one new `<id>-<slug>` directory

#### Scenario: Clean rebuild publication fails

- **GIVEN** a clean rebuild has an existing canonical predecessor
- **WHEN** publisher validation of the new output fails
- **THEN** the predecessor remains canonical and unchanged

#### Scenario: Contradictory execution mode fails preflight

- **GIVEN** a shard declares `execution_mode: "clean"`
- **AND** it also contains `resume_from_shard_basename`
- **WHEN** the runner performs preflight
- **THEN** the shard fails before Hermes invocation

### Requirement: Clean rebuild submission is transactional and idempotent

Clean rebuild SHALL apply the same eligibility rules as retry: its source MUST
be the latest failed/lost build attempt and its design task MUST be
`build_failed`. Eligibility re-check SHALL re-read the source attempt row and
the design task row within the same session that prepares the new attempt's
identity and shard payload (the existing `_prepare` pattern in
`BuildOrchestrationService`); committing the new attempt MAY use a separate
session. Idempotent replay safety SHALL be enforced by a UNIQUE constraint on
`build_attempts.idempotency_key`: replayed requests with the same key
resolve to the existing row, and concurrent inserts produce one row plus a
UNIQUE-violation that the API converts to the existing-row response. This
proposal does NOT guarantee that two deliberate clean rebuild submissions with
different idempotency keys create only one attempt; source-attempt-scoped
single-child enforcement is deferred to proposal #3's execution-row lease and
fencing model. A stale source attempt SHALL be rejected with a stable error
code. The API SHALL require an explicit `confirmed=true` field in the request body, so
misconfigured clients that omit it (default-false case) fail closed with
`409 confirmation_required` rather than triggering clean rebuild
unintentionally. This protects against client default behavior and
accidental replays, NOT against deliberate abuse — a caller that
intentionally sends `confirmed=true` is treated as confirming. Stronger
API-side authorization (RBAC, audit) is out of scope for this proposal.

This Requirement guarantees same-key clean-vs-clean idempotency. Clean requests
with different idempotency keys, clean-vs-retry cross-entry concurrency
(operator triggers both retry and clean rebuild on the same failed attempt),
and retry-vs-retry concurrency inherit the existing retry transaction shape and
are NOT additionally covered here. Proposal #3
`add-execution-lease-and-fencing` closes that window via the execution-row
lease.

Cross-feature concurrency with `resource_deletion`: deleting a challenge
via the existing `resource_deletion` flow does NOT take the publisher's
`(category, id)` lock. A delete fired during an in-flight publish for the
same id may therefore observe a half-renamed canonical tree or remove a
canonical directory the publisher just placed. Within this proposal that
window is acknowledged as a pre-existing race; aligning
`resource_deletion` with the publisher locks is out of scope and is
expected to land alongside proposal #3's lease/fencing work.

#### Scenario: Concurrent clean rebuild requests create one attempt

- **GIVEN** two clean-rebuild requests target the same latest failed attempt
- **AND** both use the same idempotency key and explicit confirmation
- **WHEN** they execute concurrently
- **THEN** both resolve to one new clean build attempt
- **AND** no request deletes or mutates the canonical predecessor

#### Scenario: Different clean rebuild keys are separate submissions

- **GIVEN** two clean-rebuild requests target the same latest failed attempt
- **AND** they use different idempotency keys
- **WHEN** proposal #3's execution lease is not yet available
- **THEN** this proposal does not promise source-attempt-scoped collapse
- **AND** stale-source detection or later lease/fencing rules decide whether
  the second request is accepted or rejected

### Requirement: Publish is serialized, failure-atomic, and crash-recoverable

The publisher SHALL acquire cross-process locks for every claimed
`(category, id)` in deterministic sorted order and hold them until commit or
rollback completes. Locks live in `paths.build_publisher_locks`, which
SHALL be defined as `paths.locks_root / "build-publisher"`; both are
registered on `ProjectPaths` and created by `initialize()`. Future
proposals adding their own locks (e.g. lease locks in proposal #3) SHALL
add sibling directories under `paths.locks_root`, never nest under
`build-publisher/`. Missing or unwritable lock root SHALL fail preflight
before any canonical mutation. Lock filenames SHALL be a hex digest
derived from `(category, claimed_id)` and SHALL NOT embed host paths.

Lock files are decoupled from challenge lifecycle: the publisher does not
delete the lock file when a publication finishes, and does not delete it
when the underlying challenge is later removed via `resource_deletion`.
Orphan lock files (claim ids that no longer exist) are harmless — they
cost one inode each and never block correct publications. The
primitive is `fcntl.flock(LOCK_EX)` with the configured timeout;
`fcntl.lockf` / `F_SETLK` is NOT used because lock ownership in this
proposal must be inherited across `fork()` (the supervisor in proposal #5
forks worker processes that own the publisher lock; flock's
fd-inheritance semantics make trylock from outside the worker's process
tree correctly report the lock as held). Non-POSIX hosts SHALL fail
preflight with an explicit unsupported-platform error rather than
silently degrading. The lock root MAY reside on a different filesystem
than canonical or quarantine; only temp, canonical, and quarantine are
required to be same-filesystem.

It SHALL validate and copy the entire claimed set to temporary siblings before
changing canonical state. Temp, canonical, and quarantine paths SHALL be
verified as same-filesystem before commit.

Before the first canonical rename, the publisher SHALL write and fsync a
durable batch journal listing every temp, canonical, quarantine, expected
hash, and current phase. It SHALL update and fsync the journal after every
rename. If a canonical predecessor exists, it SHALL move it to the fixed
per-workspace quarantine tree, using a unique transaction suffix on collision.

Any ordinary mid-batch failure SHALL roll back every already-changed claimed
id in reverse journal order and leave the canonical set identical to its
pre-publish state. Workspace bootstrap SHALL reconcile incomplete journals
under the same locks after process death. The publisher SHALL NOT touch
unrelated challenge directories.

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

#### Scenario: Concurrent publishers for the same id are serialized

- **GIVEN** two processes attempt to publish the same claimed id
- **WHEN** both acquire publisher locks
- **THEN** only one process can enter the canonical commit phase at a time
- **AND** the second re-evaluates canonical predecessor state after acquiring
  the lock

#### Scenario: Bootstrap recovers an interrupted batch journal

- **GIVEN** a process dies after quarantining one predecessor but before batch
  manifest commit
- **WHEN** workspace bootstrap reconciles the incomplete journal
- **THEN** it acquires the same claimed-id locks
- **AND** deterministically completes rollback or finalization from journal
  state

#### Scenario: Recovery pushes high-water forward after manifest-then-crash

- **GIVEN** the prior publication advanced the canonical tree and wrote
  `manifest.json` at `publish_generation = 5`
- **AND** the journal is `committed` at generation 5
- **AND** the process died before `state/highest-committed-generation.json`
  was updated; it still records generation 4
- **WHEN** workspace bootstrap runs
- **THEN** recovery atomically updates the high-water file to 5
- **AND** the journal is archived/removed
- **AND** the canonical and manifest are not rolled back (the journal
  recorded `committed`)
- **AND** repeating the same recovery is a no-op

### Requirement: Change-policy file enforces preserve and forbid rules

The runner SHALL capture an optional `change-policy.json` file before Hermes
invocation and the publisher SHALL honor that captured policy. When
`work/executions/<workspace_id>/input/change-policy.json` exists, the
publisher SHALL load it and SHALL enforce its `preserve` and `forbid`
rules against `work/executions/<workspace_id>/input/base-artifact/`.
Schema:

```json
{
  "base_artifact_relpath": "challenges/web/web-abcdef12-0001-demo",
  "preserve": ["metadata.json#id", "validate.sh", ...],
  "forbid":   ["secrets/", ...]
}
```

For each `preserve` entry:

- A bare path (no `#`) is compared byte-for-byte against the same relative
  path under `base-artifact/`.
- A path with `#json_field` selects the named top-level field in both files
  (parsed as JSON) and compares for equality.

Selected files and JSON fields MUST exist on both sides. Policy and path
validation SHALL reject unknown schema keys, duplicate entries, wrong types,
empty path components, `.`, `..`, absolute paths, backslashes, NUL, symlinks,
and any resolution outside the candidate/base roots.

Each `forbid` entry is a relative path prefix. The publisher SHALL recursively
compare descendant inventories: if any staging descendant under that prefix
did NOT exist at the same relative path under `base-artifact/`, publication
fails, including when the forbidden directory itself existed in the base.

If `change-policy.json` exists but `base-artifact/` does not, contract
preparation fails before Hermes with a clear infrastructure-error message: the
revision flow that created the change-policy is responsible for materializing
base-artifact beforehand.

When `change-policy.json` is absent (initial-run case), the publisher
SHALL skip change-policy enforcement.

#### Scenario: Preserve byte-mismatch fails closed

- **GIVEN** `change-policy.json` lists `validate.sh` under `preserve`
- **AND** the staging `validate.sh` differs from `base-artifact/.../validate.sh`
- **WHEN** the publisher runs
- **THEN** publication fails
- **AND** the canonical tree is unchanged

#### Scenario: Preserve JSON-field mismatch fails closed

- **GIVEN** `change-policy.json` lists `metadata.json#id` under `preserve`
- **AND** the staging metadata's `id` differs from base-artifact's
- **WHEN** the publisher runs
- **THEN** publication fails

#### Scenario: Forbid entry newly added fails closed

- **GIVEN** `change-policy.json` lists `secrets/` under `forbid`
- **AND** the staging output newly contains `secrets/key.pem`
- **WHEN** the publisher runs
- **THEN** publication fails

#### Scenario: Existing forbidden directory does not permit new descendants

- **GIVEN** both base and staging contain `secrets/`
- **AND** only staging contains `secrets/new-key.pem`
- **WHEN** `secrets/` is a forbid prefix
- **THEN** publication fails even though the prefix existed in the base

#### Scenario: Traversal in policy entry fails closed

- **GIVEN** a preserve or forbid entry contains `..`, an absolute path, or a
  backslash separator
- **WHEN** the runner prepares the publication contract
- **THEN** contract preparation fails before Hermes invocation

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

Before canonical mutation, the publisher SHALL compute a deterministic batch
`output_manifest_hash` over all validated temporary trees. The canonical
encoding SHALL include claimed id, relative POSIX path, entry type, normalized
permission mode, and file content hash using length-prefixed records. Empty
directories and executable-bit changes MUST affect the hash.
Normalized permission mode SHALL be `st_mode & 0o777`; ownership, timestamps,
inode values, ACLs, and other host-specific metadata SHALL not affect it.

After canonical rename, the publisher SHALL re-hash canonical trees and require
equality with the staged hash, then atomically update
`work/executions/<workspace_id>/input/manifest.json` while publication locks
remain held. Manifest-write or hash-verification failure SHALL roll back the
canonical batch. The durable journal SHALL support recovery if the process
dies between canonical rename and manifest update.

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

#### Scenario: Manifest hash changes when executable mode changes

- **GIVEN** two trees contain byte-identical `validate.sh` files
- **AND** only one file has its executable permission bits set
- **WHEN** both manifest hashes are computed
- **THEN** their `output_manifest_hash` values are different

#### Scenario: Manifest update failure rolls back canonical batch

- **GIVEN** canonical renames completed under publication locks
- **WHEN** atomic manifest replacement fails
- **THEN** the journal-driven rollback restores every predecessor
- **AND** the run reports the `manifest` or `rollback` phase

### Requirement: Bounded retention sweep keeps retained workspace artifacts disk-bounded

On publish calls (success or failure), the publisher SHALL perform an
opportunistic sweep over replaced-canonical quarantine and failed-workspace
`output/` and `logs/` staging, subject to a per-process throttle: at most one
sweep per `BUILD_PUBLISH_SWEEP_INTERVAL_SECONDS` (default 60 seconds). When a
sweep is suppressed by the throttle the publish path SHALL still record that
a sweep is due, so the next eligible call (or bootstrap) runs it. The
throttle SHALL be configurable as a positive integer; invalid values fail
preflight.

- Treat each terminal workspace containing replaced-canonical quarantine or
  failed output/log staging as one retention root.
- Use the host-owned terminal marker timestamp, not descendant mtime.
- Delete all retained artifacts in a root older than 7 days.
- If more than 20 retention roots remain (any age), delete retained artifacts
  from the oldest roots until at most 20 remain.
- Skip a workspace with an incomplete journal or whose publication locks
  cannot all be acquired non-blockingly.

Sweep errors (permission/busy) MUST be logged and MUST NOT block the publish
result. The sweep is independent of the manual-workspace GC introduced in the
previous proposal; both run together at workspace bootstrap.

The publisher SHALL NOT clear the current workspace after publication because
host validation and validation repair still consume `./output/` and `./logs/`.
Only the runner, after terminal validation success, SHALL clear those trees
while retaining `./input/` and its committed manifest. Publisher failure or
validation failure SHALL retain them. Every terminal success or failure SHALL
write the terminal marker used by the sweep.

#### Scenario: Old failed staging is reclaimed

- **GIVEN** failed workspace W1 has a terminal marker older than 7 days
- **AND** failed workspace W2 has a terminal marker within 7 days
- **WHEN** the publisher's retention sweep runs
- **THEN** W1 failed output/log staging is deleted
- **AND** W2 failed output/log staging is kept

#### Scenario: Retention-root count cap evicts oldest artifacts

- **GIVEN** 21 terminal workspace retention roots exist (all fresher than 7
  days), including successful quarantine and failed staging roots
- **WHEN** the publisher's retention sweep runs
- **THEN** retained artifacts in the oldest root are deleted, leaving 20 roots

#### Scenario: Active journal is not swept

- **GIVEN** a workspace has an incomplete publish journal
- **WHEN** the retention sweep runs
- **THEN** its output, logs, temps, and quarantine are not deleted

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

Publisher failures SHALL also expose one stable phase from `contract`,
`allowlist`, `policy`, `limits`, `stage`, `commit`, `manifest`, `rollback`, or
`recovery`, plus the offending claimed id and workspace-relative path when it
is safe to do so. The runner SHALL preserve that structured phase in its error
message and terminal marker. This proposal does not introduce a new global
failure taxonomy.

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

#### Scenario: Duplicate output is diagnosable without free-text parsing

- **GIVEN** two workspace directories match one claimed id
- **WHEN** publisher allowlist validation rejects the batch
- **THEN** the failure reports phase `allowlist`
- **AND** it identifies the claimed id without exposing a host-absolute path
