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

When any constraint is present, the queue reads candidate JSON payloads before
the atomic move. A category-filtered claim accepts only shards where every
`challenges[]` entry is a dict with the requested `category`, and the list is
non-empty. A build-attempt-filtered claim accepts only a shard whose top-level
`build_attempt_id` is a valid UUID equal to the normalized requested id.
`require_build_attempt=True` accepts only attributed shards: both top-level
`build_attempt_id` and `design_task_id` must be valid UUIDs. If multiple
filters are present, all must match. Filter arguments are validated before the
pending directory is scanned, so invalid filter values cannot mutate the
queue.

Malformed payloads, non-regular files, and symbolic links are skipped for
constrained claims and remain visible for unconstrained compatibility.

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
workers may still use `--loop`. `--category` and `--build-attempt` may be
combined and then both filters must match; `--build-attempts-only` is rejected
when `--build-attempt` is present because exact-attempt selection already
requires attribution.

### Decision 3: build-attempt UI no longer starts the legacy global worker

The Build Attempts view must not call `/api/actions/worker` as if it were a
build-attempt worker. It either:

1. asks the backend to start the next queued DB-known build attempt in the
   current category filter, or
2. starts a constrained single-attempt worker from a detail page.

The HTTP adapter owns request validation. For category starts, it chooses a
queued `build_attempts` row joined to a design task in that category, verifies
the exact `shard_basename` is pending and its payload matches the row's
`build_attempt_id`, `design_task_id`, and design-task category after recovery,
and starts the worker with the equivalent of `--build-attempt <id> --category
<category>`. Selection is deterministic:
`build_attempts.created_at ASC, build_attempts.id ASC`, skipping rows whose
matching shard is not pending after recovery. For single-attempt starts, it
resolves the parent design-task category and applies the same exact-basename
and payload checks before launching with both attempt and category filters. It
returns a conflict when the named attempt is not queued or has no matching
pending shard after recovery. The old `/api/actions/worker` remains for legacy
shard-management API clients only; this change does not add another dashboard
control for it.

Before reporting "no matching pending shard", the constrained build-worker
endpoint runs build staging recovery so a committed queued attempt whose shard
is still under `work/shards/staging/build-attempts/` can be published and then
claimed.

The endpoint delegates the final busy check and subprocess creation to one
atomic `TaskManager` operation. The exact-attempt subprocess does not use
`--loop`. A successful start returns `202` with the selected
`build_attempt_id`; validation errors return `400` or `404`, and eligibility or
local-task conflicts return `409`.

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

### Decision 6: this change is the compatibility layer for the worker pool

This change keeps the current file-backed runner and local dashboard process.
The later `add-agent-worker-pool-management` change may replace the HTTP launch
implementation with database-leased execution, but it must preserve the exact
attempt/category authorization established here. The two changes must not be
implemented concurrently against the same endpoint without rebasing the later
change on this contract.

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
  API compatibility, but dashboard build controls must stop using it.
- **Still a single local process.** The constrained endpoint uses the current
  dashboard process guard and does not provide worker-pool parallelism.
- **No database lease yet.** This fixes correctness for the file queue but does
  not provide a full worker pool. That is deferred to a later change.
- **Cross-process duplicate starts remain possible.** Two dashboard server
  processes can both launch an exact-attempt subprocess, but the atomic file
  rename permits only one to claim the shard; neither process may fall back to
  another attempt. Database leasing belongs to the worker-pool change.

## Migration Plan

1. Add claim filtering and tests at the queue layer.
2. Thread filters through runner and CLI.
3. Add constrained build worker endpoints, including staging recovery before
   pending-shard matching.
4. Update the build-attempts view to call constrained endpoints.
5. Keep the legacy global worker endpoint available for explicit API use, with
   no dashboard build control wired to it.
