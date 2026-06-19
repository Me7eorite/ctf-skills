## Context

There are two build submission sources today:

1. Legacy matrix/seed shards named with a category prefix, such as
   `web-0001-0001.json`.
2. Build-attempt shards named by UUID and attributed in the payload with
   `build_attempt_id`, `design_task_id`, and a single matrix-shaped challenge.

Both sources land in `work/shards/pending/` and are consumed by the same
`ShardQueue.claim(worker)` method. The method currently uses filename ordering
only. That is acceptable for a whole-queue worker, but it is not a valid
contract for a category-specific or build-attempt-specific operator action.

## Goals / Non-Goals

**Goals:**

- Prevent category-specific worker actions from claiming another category.
- Allow one build attempt to be executed explicitly by id.
- Preserve the current file queue and runner as the execution substrate.
- Keep hand-written shard execution compatible.
- Make dashboard wording and behavior reflect the actual claim scope.

**Non-Goals:**

- No worker pool, agent registry, or Hermes profile management.
- No replacement of the file-backed queue with a database queue in this
  change.
- No change to challenge-generation prompt content except for any needed
  context field that reports the constrained claim.
- No cancellation/kill behavior for already-running Hermes subprocesses.

## Decisions

### Decision 1: claim filters are explicit and payload-verified

`ShardQueue.claim` gains optional filters:

- `category: str | None`
- `build_attempt_id: UUID | str | None`
- `require_build_attempt: bool = False`

When either filter is present, the queue reads candidate JSON payloads before
the atomic move. A category-filtered claim accepts only shards where every
`challenges[]` entry is a dict with the requested `category`, and the list is
non-empty. A build-attempt-filtered claim accepts only a shard whose top-level
`build_attempt_id` equals the requested id. `require_build_attempt=True`
accepts only shards with a non-empty top-level `build_attempt_id`. If multiple
filters are present, all must match.

Malformed payloads are skipped for constrained claims and remain visible for
unconstrained compatibility.

The atomic move remains the lock boundary. A worker may lose the race after
reading a matching candidate; it then continues scanning for another match.

### Decision 2: runner and CLI expose the same constrained contract

`HermesRunner.run(...)` and `process_one(...)` accept the same optional filters
and pass them to `ShardQueue.claim`. The CLI exposes:

```text
challenge-factory run --worker W --category web
challenge-factory run --worker W --build-attempt <uuid>
challenge-factory run --worker W --category web --build-attempts-only
```

`--category` is validated against `core.queue.SUPPORTED_CATEGORIES` for the
legacy shard pipeline. `--build-attempt` validates UUID syntax.
`--build-attempts-only` is valid only with `--category` and sets
`require_build_attempt=True`, so a category-constrained build-attempt worker
cannot consume a legacy category shard. An empty matching queue exits normally
with `processed = 0` and no queue mutation. `--build-attempt` is a single-shard
operation and is mutually exclusive with `--loop`; category-constrained
workers may still use `--loop`.

### Decision 3: build-attempt UI no longer starts the legacy global worker

The Build Attempts view must not call `/api/actions/worker` as if it were a
build-attempt worker. It either:

1. asks the backend to start the next queued DB-known build attempt in the
   current category filter, or
2. starts a constrained single-attempt worker from a detail page.

The HTTP adapter owns request validation. For category starts, it chooses a
queued `build_attempts` row joined to a design task in that category, verifies
its attributed shard is pending after recovery, and starts the worker with the
equivalent of `--build-attempt <id>`. Selection is deterministic:
`build_attempts.created_at ASC, build_attempts.id ASC`, skipping rows whose
matching shard is not pending after recovery. For single-attempt starts, it
returns a conflict when the named attempt is not queued or has no matching
pending shard after recovery. The old `/api/actions/worker` remains for legacy
shard management surfaces only.

Before reporting "no matching pending shard", the constrained build-worker
endpoint runs build staging recovery so a committed queued attempt whose shard
is still under `work/shards/staging/build-attempts/` can be published and then
claimed.

### Decision 4: build-attempt running state stays reconciler-owned

This change does not make the HTTP start action directly set
`build_attempts.status = running`. The existing reconciler still promotes rows
after observing an attributed running shard and a queued/running progress
claim event. The start endpoint only launches a correctly constrained worker.

### Decision 5: constrained starts preserve the local single-process guard

The constrained build-worker endpoint reuses the existing local background-task
ownership rule: one dashboard process may own at most one local worker or
validation subprocess at a time. Starting a category- or attempt-constrained
worker while another local task is running returns conflict and does not start
a second process. This change fixes claim scope; it does not introduce worker
pool concurrency.

## Risks / Trade-offs

- **Payload read before rename is not a lock.** Another worker can claim the
  file first. This is already handled by the existing `FileNotFoundError`
  retry loop.
- **Category filter on UUID-named shards requires payload parsing.** This is
  necessary because UUID basenames intentionally do not encode category.
- **Category alone is not enough for Build Attempts.** Build-attempt category
  starts resolve to a DB-known queued attempt and then run by build-attempt id;
  a broad file-level category worker is reserved for CLI/legacy operation.
- **Legacy global worker can still process any category.** That is kept for
  compatibility, but category-specific UI must stop using it.
- **Still a single local process.** The constrained endpoint uses the current
  dashboard process guard and does not provide worker-pool parallelism.
- **No database lease yet.** This fixes correctness for the file queue but does
  not provide a full worker pool. That is deferred to a later change.

## Migration Plan

1. Add claim filtering and tests at the queue layer.
2. Thread filters through runner and CLI.
3. Add constrained build worker endpoints, including staging recovery before
   pending-shard matching.
4. Update the build-attempts view to call constrained endpoints.
5. Keep the legacy global worker endpoint available only where its global
   behavior is explicit.
