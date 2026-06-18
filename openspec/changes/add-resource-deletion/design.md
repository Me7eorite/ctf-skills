## Context

The control plane spans two consistency domains. PostgreSQL owns generation
requests, research runs, design tasks, design attempts, challenge designs, and
build attempts. The filesystem owns shard queue placement and generated files;
progress rows are stored in PostgreSQL but keyed only by shard basename, with
no foreign key to `build_attempts`.

Database cascades alone are therefore insufficient. Deleting a task directly
can leave a queued shard that a worker will still claim, and deleting a build
attempt can leave progress rows that continue to appear in aggregate views.
Conversely, recursively deleting a challenge directory as an implicit cascade
is too destructive: operators commonly want to remove control-plane noise
while retaining a generated challenge for inspection or manual recovery.

This change builds on the archived `add-build-attempts` capability, including
its attributed shard payloads and staging recovery. The implementation must preserve the existing
dependency direction: web adapters call services; services may call
persistence/core; services do not import web; Hermes does not import services
or persistence.

## Goals / Non-Goals

**Goals:**

- Delete generation requests, design tasks, and build attempts through one
  consistent service and HTTP contract.
- Prevent deletion while work is executing.
- Withdraw queued work and remove orphanable progress state.
- Preserve all non-operational artifacts by default and make artifact deletion
  an explicit, reviewable choice.
- Constrain every filesystem deletion to known project roots and report paths
  that were retained or skipped.
- Keep parent design-task state correct after an individual build attempt is
  deleted.

**Non-Goals:**

- No bulk-selection delete endpoint in this change.
- No restore/trash UI after a successful deletion.
- No soft-delete columns or tombstone history.
- No deletion of challenge categories, profiles, arbitrary shard files, or
  delivery bundles.
- No cancellation of a running Hermes/research subprocess; operators must stop
  or let active work finish before deleting it.

## Decisions

### Decision 1: one service owns cross-store deletion

`ResourceDeletionService` exposes one method per root resource and computes a
deletion scope before mutating either store. It locks the root plus affected
run/task/attempt rows, validates active states, collects referenced paths and
shard basenames, then applies database and filesystem cleanup as one
orchestrated operation.

Relational deletion is explicit where constraint ordering matters:
`challenge_designs` rows are deleted before their referenced
`design_attempts`, then the Design Task or request root is removed. Remaining
children may use their existing `CASCADE`/`SET NULL` actions. This avoids
depending on PostgreSQL cascade scheduling across the
`challenge_designs.design_attempt_id ON DELETE RESTRICT` edge.

Progress cleanup goes through the extended `ProgressStore.purge_shards(...)`
contract. That method accepts an optional opaque caller transaction context:
the PostgreSQL implementation joins the supplied SQLAlchemy transaction, while
ordinary callers that omit it receive the existing short-transaction behavior;
the in-memory implementation ignores the context and purges atomically. Core
does not import SQLAlchemy—the context remains typed as an opaque object at the
protocol boundary.

Web handlers remain thin translators for UUID parsing, the
`delete_artifacts` query parameter, and `404`/`409` responses. This avoids
three endpoint implementations drifting on cascade or retention semantics.

**Alternative considered:** direct repository deletes in each endpoint. This
is smaller initially but cannot safely coordinate queue files, progress rows,
and artifacts, and would duplicate the active-state rules.

### Decision 2: active execution blocks; queued execution is withdrawn

Deletion returns `409` when its cascade scope contains a running research run,
a running design attempt, or a running build attempt. The parent
`design_tasks.status` is used for diagnostics but is not sufficient by itself:
`designing` conflicts unless all design attempts are terminal, and `building`
conflicts only when a child build attempt is actually running. A queued
research run is database-only and can be removed by cascade. A queued build
attempt can be deleted only after its staging/pending shard has been moved out
of the worker-visible queue.

For individual Build Attempt deletion, active-state discovery includes sibling
attempts. The target itself may be queued and withdrawn, but any *different*
queued or running sibling causes `409`: it may read the target's shard/progress
as `resume_from_shard_basename`, or may be writing the shared challenge
directory. Parent request/task deletion may withdraw multiple queued siblings
because every dependent attempt is inside the same deletion scope; any running
child still blocks the operation.

The service locks affected rows before the final active-state check. For a
queued build it verifies that no attributed matching shard has moved into
`running/` both before and after queue withdrawal. A detected claim aborts and
restores the withdrawn file.

**Alternative considered:** reject every queued resource. This is safer but
defeats the common requirement to cancel accidental submissions before they
start, even though the queue's atomic rename protocol gives a clear claim
boundary.

### Decision 3: use filesystem quarantine around the database transaction

Files that must disappear as part of deletion are atomically renamed into a
private deletion-quarantine directory under `work/` before the database commit.
If validation or commit fails, they are renamed back. After a successful
commit, quarantined files are removed.

This is not a distributed transaction, but same-filesystem atomic renames give
the required property at the worker boundary: a queued shard is either visible
and owned by a database row, or hidden while that row is being deleted.
Post-commit removal failures are logged and returned as cleanup warnings; the
quarantined path remains outside all worker-scanned directories.

### Decision 4: distinguish operational state from retained artifacts

Attributed shard JSON, staging files, claim sidecars, and progress rows are
operational state and are always cleaned with their owning build attempt.

Challenge directories and directly referenced research source, research log,
design prompt, and design log files are retained when
`delete_artifacts=false` (the default). When it is true, the service
quarantines and removes only paths that:

1. are directly referenced by rows in the deletion scope
   (`resulting_challenge_dir`, `raw_text_path`, `hermes_log_path`, or
   `prompt_path`); directory-name inference alone is never ownership proof;
2. resolve beneath an allowlisted project root (`work/challenges`,
   `work/research`, or `work/design`); and
3. are not referenced by a surviving row outside the deletion scope.

Symlinks and `..` traversal cannot escape the resolved allowlisted roots.
Unsafe, missing, or shared paths are reported as skipped rather than deleted.
For explicit artifact deletion, the service locks the reference-bearing tables
while it performs the final shared-reference check and quarantines candidates.
This prevents a concurrent reconciler/repository update from creating a new
surviving reference between the check and the atomic rename. Writers that
resume after commit observe either a newly recreated path or the path missing;
they never acquire the quarantined inode by stale reference.

**Alternative considered:** scan `<category>/<challenge_id>-*` to find partial
artifacts from failed reconciliation. This cannot distinguish an untracked
legacy shard or a same-id task from another request, so it is rejected. An
untracked candidate is reported as skipped/unowned and requires explicit
operator cleanup; tracked research/design paths remain eligible through their
own database columns.

### Decision 5: recompute design-task build state and preserve attempt numbering

After deleting one build attempt, the parent state is derived from the highest
remaining `attempt_no`: `queued|running -> building`, `succeeded -> built`,
`failed|lost -> build_failed`. With no remaining attempt, it returns to
`designed`. This update occurs in the same database transaction.

`design_tasks.next_build_attempt_no` is the durable allocator. The migration
backfills it to `COALESCE(MAX(build_attempts.attempt_no), 0) + 1`. Submission
locks the task row, assigns the current counter, and increments it in the same
transaction. Deletion never decrements it. Attempt UUIDs and shard basenames
remain the trace identifiers, while display numbers stay monotonically
increasing even when history is deliberately removed.

### Decision 6: destructive UI uses a real explicit option

The three list/detail views expose Delete actions. A reusable confirmation
dialog names the resource, describes cascading child deletion, and includes an
unchecked `同时删除产物` checkbox. Confirmation submits
`DELETE ...?delete_artifacts=<checked>`; cancel performs no request. Native
two-step confirm dialogs are rejected because treating “Cancel” on the second
dialog as “continue but retain artifacts” is ambiguous.

Successful list deletion refreshes the list. Successful detail deletion
returns to the corresponding list. Conflicts and cleanup warnings are surfaced
without optimistic removal.

## Risks / Trade-offs

- **[Race with worker claim]** A worker can rename a queued shard while delete
  begins. → Quarantine by atomic rename, re-check attributed running files, and
  abort/restore on a detected claim.
- **[PostgreSQL/filesystem cannot commit atomically]** A process can die after
  one side changes. → Keep worker-invisible quarantines, use deterministic
  deletion manifests, and perform startup/before-delete quarantine recovery.
- **[Artifact path is malicious or stale]** A persisted path could escape the
  project. → Resolve paths, require allowlisted ancestry, do not follow an
  escaping symlink, and report skipped paths.
- **[Shared artifact path]** Two attempts can reference the same directory. →
  Query surviving references and retain shared paths even when explicit
  deletion was requested.
- **[Large cascades hold locks]** Request deletion may span many tasks. → Keep
  file discovery bounded to referenced paths and attributed basenames, and keep
  irreversible removal after the short database commit.
- **[Artifact deletion partially fails]** Permissions may prevent final
  removal. → Database deletion remains successful, the response classifies the
  path as `quarantined`, includes a cleanup warning, and leaves it outside
  runtime paths for deterministic recovery.

## Migration Plan

1. Apply the Alembic migration that adds and backfills
   `design_tasks.next_build_attempt_no`.
2. Deploy the service and endpoints before exposing dashboard buttons.
3. Deploy the UI confirmation dialog and actions.
4. Routine application rollback disables deletion but retains the migration and
   uses a compatibility build submitter that continues consuming the counter.
   Schema downgrade is supported for empty/test databases; production may drop
   the counter only after proving no deleted-number gap can be reused.
5. Existing quarantine entries
   are recovered or purged by the service recovery routine before rollback.

## Open Questions

None. Artifact retention and the active-state policy are fixed by this
proposal; additional bulk deletion or soft-delete history requires a separate
change.
