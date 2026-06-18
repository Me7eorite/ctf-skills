## Context

After `drop-sqlite-progress-store` shipped, the platform's relational
substrate is fully on PostgreSQL: research, design tasks, design attempts,
challenge designs, progress events. The execution side of the pipeline —
shard claim, hermes invocation, validate.sh, packing — is still file-system
backed under `work/shards/{pending,running,done,failed}/` and
`work/challenges/`, driven by the shard prompt contract that is part of the
Hermes execution protocol.

The next piece is connecting those two worlds. Operators today have:

- A `design_tasks` row reaching status `designed` after a structured design
  passes validation.
- A working `challenge-factory run --worker ...` CLI that drains
  `work/shards/pending/`.
- No UI to say "take these three designed tasks and build them," no row
  that records "design X was attempted N times and produced artifact path
  Y," and no automated way to mark a built artifact unavailable when an
  operator manually deletes its directory.

A naive approach would either:

1. Make hermes runner write to `build_attempts` directly (couples hermes to
   persistence, violates the dependency direction matrix), or
2. Put everything in PostgreSQL — shard queue, claim, lease, the works
   (rewrites the runner, duplicates progress events, abandons the working
   file-backed pipeline).

This change instead adds a thin **editorial table** (`build_attempts`)
plus a **one-way mirror** (the `BuildReconciler`) that watches the
filesystem and reflects what it sees into PostgreSQL. The Hermes runner stays
oblivious to PostgreSQL and `build_attempts`, but understands the optional
resume-source field in attributed shard payloads. Hand-written matrix shards
that omit the new fields remain unchanged.

## Goals / Non-Goals

**Goals:**

- A PG-side editorial unit for "build this design" with an attempt-number
  audit chain.
- A reconciler that maps file-system state to `build_attempts.status`
  without coupling hermes to PG.
- A dashboard surface (`构建任务` view) styled like the existing Design
  Tasks page, so operators can monitor and retry builds.
- Retry that extends the runner's resume protocol with an explicit previous-
  shard source — no token re-burn on stages whose evidence is still valid.
- Configuration knobs (env vars) for the reconciler tick interval and
  the build-attempts list limits, so deployment can tune them without
  code changes.
- A clean migration path for the dashboard chrome that has accreted into
  the global header — the build-stage actions (启动 Worker, 重新验证,
  sync indicator, refresh) move to the build-tasks view.

**Non-Goals:**

- No worker pool or PG-driven scheduling. Workers are still operator-
  started.
- No automated cleanup of `work/challenges/<category>/<id>-<slug>/`. Operator-
  initiated deletes are detected as `artifact_status = missing`, not
  auto-deleted and not rewritten as a different historical build outcome.
- No delivery-bundle download UI. That is a separate change
  (`add-delivery-bundles`).
- No batching multiple challenges per shard. One challenge per shard
  remains the convention.
- No schema or state-machine changes to `progress_events`. Build orchestration
  reads progress but does not write it.
- Hermes runner remains focused on shard execution and resume planning; it
  gains only a selectable previous-shard key for resume reads.

## Decisions

### Decision 1: build_attempts is the editorial unit, not the executor

`build_attempts` is the place where "submit this task for build" lives.
It does NOT model what the runner is doing right now. The runner's
truth lives in shard files and `progress_events`. The reconciler
reflects that truth into `build_attempts.status` on a fixed cadence.

**Why:** This keeps Hermes independent of PostgreSQL while making one bounded
resume-protocol extension, and preserves the dependency direction matrix
(`hermes` does not import `persistence`). It also means that even when PG is down, shards still
execute and produce artifacts — `build_attempts` rows simply get
catch-up updates on the next reconciliation.

**Alternatives considered:**

- *Have hermes runner write `build_attempts.status` directly.* Would
  couple the runner to persistence and require yet another
  `ProgressStore`-style protocol. Rejected.
- *Replace the file queue with PG.* Would rewrite the runner and
  invalidate parts of the hermes execution protocol spec. Not in
  scope.

### Decision 2: Build outcome and artifact availability are separate

Status enum: `queued`, `running`, `succeeded`, `failed`, `lost`.

Three terminals (`succeeded`, `failed`, `lost`) are kept distinct
because they have different operator diagnostics:

- `failed` says "the build attempt ran and the artifact failed
  validation" — debug the design or the prompt.
- `lost` says "the non-terminal shard vanished before an execution outcome
  was observed" — the file queue ownership story is broken.

Artifact availability is tracked separately as `unknown`, `present`, or
`missing`. A successful attempt starts as `present`; if its directory later
disappears, only `artifact_status` changes to `missing`. The attempt remains
`succeeded` and its parent remains `built`, preserving the audit fact while
still surfacing the operational problem immediately.

**Alternatives considered:**

- *Three states (queued, succeeded, failed) folding a vanished active shard
  into failed.*
  Simpler schema, but loses the operator-actionable distinction.
- *Add a grace period for `lost`.* Pushes complexity into the
  reconciler for a case that is rare in practice.

### Decision 3: Partial unique index for concurrency guard

A `UNIQUE (design_task_id) WHERE status IN ('queued', 'running')`
partial index enforces "at most one active build per design task."
Two concurrent submissions race to the index and only one survives;
the orchestration service catches the unique-violation and surfaces a
validation error.

**Alternatives considered:**

- *Service-level locking via `SELECT FOR UPDATE`.* Race-free against
  concurrent transactions in the same process but does not survive
  multi-process scenarios. The partial index makes the database the
  authority.
- *No concurrency guard.* Would let two attempts race on the same
  shard basename, eventually causing one to overwrite the other's
  shard file. Not acceptable.

### Decision 4: Reconciler is a daemon thread inside the server process

`web/server.py` starts the reconciler as a `threading.Thread(daemon=True)`
during `serve(...)`. It runs forever, reads
`BUILD_RECONCILER_POLL_SECONDS` once at startup, and ticks at that
interval. The CLI `run` subcommand does NOT start a reconciler — the
worker subprocess only writes shard transitions and progress events; it
should not be racing the server on `build_attempts.status` updates.

`/api/state` synchronously triggers one tick before returning so the
operator's first poll after clicking `构建` sees the queued row already
reflected. This is cheap because the tick is a bounded scan of the
queue directories.

**Alternatives considered:**

- *Separate subprocess for the reconciler.* Adds process supervision
  for no benefit at this scale.
- *Sync-only (no daemon, only `/api/state`).* The dashboard polls every
  few seconds anyway, but `progress_events` published by the runner
  during long builds would not propagate until the next API call.
  Idle dashboards would miss state transitions.
- *Callback from the runner to the reconciler.* Reintroduces the
  hermes-to-persistence coupling we are explicitly avoiding.

### Decision 5: Retry explicitly links to the previous shard's resume window

When `retry` is called, the orchestration service emits a fresh
attempt-specific `shard_basename` to `work/shards/pending/` without touching
`work/challenges/<category>/<id>-<slug>/`. Its payload sets
`resume_from_shard_basename` to the source attempt's basename. The runner
uses that basename only to read the previous claim window; current claim,
carry-forward, and execution events are written under the fresh basename.
It then inspects evidence, skips passing stages, and re-runs failed stages.

**Rationale:** The current resume implementation queries historical events by
shard basename before checking challenge evidence. An explicit source key is
required when attempt filenames differ. This small protocol extension keeps
Hermes token cost near the marginal failed-stage cost while isolating each
attempt's newly written events.

**Alternatives considered:**

- *Wipe `work/challenges/<category>/<id>-<slug>/` on retry.* Trivial to
  implement, but burns tokens regenerating already-passing artifacts.
- *Reuse the same shard basename on every retry.* This looks simpler,
  but terminal shard files in `done/` or `failed/` can be matched to
  the new non-terminal attempt, and progress events from multiple
  attempts share one shard key. Rejected.
- *Selective wipe (only failed stage dirs).* The resume protocol
  already does this via evidence checks; manual selection would
  duplicate logic and risk drift.

### Decision 6: Shard JSON adds traceability/resume fields + per-challenge design

The shard JSON envelope grows three optional top-level fields
(`build_attempt_id`, `design_task_id`, `resume_from_shard_basename`) and each
challenges entry grows a `design` sub-object. Existing hand-written matrix shards keep
working because they omit these fields. The runner consumes only
`resume_from_shard_basename`; it otherwise ignores build attribution fields and
the reconciler treats shards lacking `build_attempt_id` as un-attributed. The
reconciler attributes generated shards by the payload's
`build_attempt_id`, not by filename alone, so a hand-written shard with
the same basename cannot move a `build_attempts` row.

The `design` sub-object carries the validated
`challenge_designs.payload` content (deployment, artifacts, flag
location, validation steps, hints, operator-facing prompt). The
shard prompt template gains a single sentence describing it. Consuming the
`design` sub-object needs no execution-path change beyond prompt rendering;
the separate `resume_from_shard_basename` field does require the bounded runner
change described in Decision 5.

**Alternatives considered:**

- *Replace the shard format entirely.* Would break every hand-written
  matrix shard and existing test fixture.
- *Reference `challenge_designs` by id instead of inlining.* Reads at
  build time would require a DB round-trip from the runner, which
  currently has no persistence dependency. Inlining keeps the
  decoupling intact at the cost of duplicating data into the shard.
- *Attribute reconciler rows by basename only.* That would contradict
  the "ignore hand-written shards" requirement and allow a manual shard
  with a colliding filename to update `build_attempts`. Rejected.

### Decision 7: Build view follows the Design Tasks page template, not a special toolbar

Earlier iterations of this design proposed a page-level toolbar with
sync timestamp and the worker/validate buttons. The user feedback was
explicit: keep parity with the Design Tasks page (filter bar plus
table plus per-row actions), and place the global-header actions
into the filter bar's right side.

The global header bar's `重新验证`, `启动 Worker`, sync timestamp, and
refresh icon are removed. The mobile bottom action bar is removed.
Equivalents land inside the build-tasks filter bar
(`Apply`, `Clear`, `⟳ 刷新`, `▶ 启动 Worker`, `☑ 重新验证`). HTTP
endpoints `/api/actions/worker` and `/api/actions/validate` keep
their paths and semantics; only the UI binding moves.

**Alternatives considered:**

- *Keep the global header.* Wrong page-context: those actions are
  build-stage operations and confuse operators on research and design
  pages.
- *Add the toolbar as a separate band above the filter bar.* Breaks
  visual parity with the Design Tasks page and adds vertical chrome.

### Decision 8: Configuration knobs are module-level globals read from env at import time

Three knobs:

- `BUILD_RECONCILER_POLL_SECONDS` (default 5) — read in
  `services/build_reconciler.py`.
- `BUILD_ATTEMPTS_LIST_DEFAULT_LIMIT` (default 100) — read in
  `web/build_attempts_endpoints.py`.
- `BUILD_ATTEMPTS_LIST_MAX_LIMIT` (default 500) — read in
  `web/build_attempts_endpoints.py`.

Each is parsed once at module import. Invalid values fall back to the
default with a one-time warning. Tests can monkeypatch the module
global to exercise non-default values without touching the
environment.

No CLI flag is added; env-vars are sufficient and avoid bloating the
CLI surface.

### Decision 9: PostgreSQL commit and queue publication use recoverable staging

PostgreSQL and the filesystem cannot participate in one atomic transaction.
`submit_batch` therefore writes every payload to a private staging directory,
commits the attempt rows and task states in one PostgreSQL transaction, and
only then makes a best-effort atomic rename of all staged files into `pending/`.
The committed row is the durable acceptance point; a post-commit publication
failure is logged and left for recovery rather than reported as a rolled-back
submission.

On server startup and before each reconciliation tick, a recovery pass scans
queued attempts and staged files. A committed queued row with a matching staged
payload is published; a staged payload older than one hour without a database
row is removed. The grace interval prevents recovery from deleting a batch that
is still inside its short database transaction.
A committed queued row with neither a pending nor staged payload becomes
`lost`. This guarantees crash convergence rather than claiming impossible
cross-resource atomicity.

### Decision 10: Dry-run and legacy requeue cannot mutate attributed history

The file queue alone cannot distinguish a real worker claim from
`challenge-factory run --dry-run`, because both temporarily move a shard into
`running/`. The reconciler therefore promotes `queued -> running` only when it
also sees the current basename's shard-level `queued/running` progress claim
event. Dry-run writes no progress event and returns the file to `pending/`, so
the attempt remains queued. A real run whose best-effort progress write is
unavailable may remain visually queued during execution, but its eventual
done/failed file still drives the correct terminal transition.

The generic shard-requeue endpoint is retained only for unattributed
hand-written shards. Requeueing a shard whose payload contains
`build_attempt_id` would execute a terminal attempt again without a new audit
row, so the endpoint rejects it with `409` and directs the operator to the
build-attempt retry action.

## Risks / Trade-offs

| Risk | Mitigation |
| --- | --- |
| Reconciler races with `cli.py` writing `progress_events` for the same shard | Reconciler only reads `progress_events`; writes touch only `build_attempts` and `design_tasks` rows; no shared write target |
| Reconciler thread dies silently and `build_attempts` rows stale | Thread wraps each tick in try/except and logs; if it dies, the `/api/state` synchronous tick continues to serve interactive operators |
| Artifact disappearance is observed during a temporary directory move | Only `artifact_status` changes; the successful outcome stays immutable and availability returns to `present` if the directory returns |
| Process exits between PostgreSQL commit and pending-file publication | Recoverable staging republishes the committed payload on startup or the next reconciler tick |
| Shard JSON growth (large `design` sub-object) bloats `pending/` directory | Designs are typically a few KB; comparable to the existing matrix shard size |
| Global header removal breaks muscle memory for existing operators | One-shot training cost; the actions are still discoverable via the build-tasks view, which is in the sidebar |
| The new reconciler thread inside `web/server.py` complicates testing | All file-system interactions can be unit-tested by passing a temp `ProjectPaths`; the thread itself is exercised by an integration test |
| `design_tasks.status` enum growth could ripple into unrelated endpoints | The design-task-planning capability spec already restricts which statuses each endpoint may emit; the new values are explicitly off-limits for planning endpoints |

## Migration Plan

1. Add Alembic revision `0006_build_attempts`: create the table, the
   partial unique index, the two ordinary indexes, and alter the
   `design_tasks.status` CHECK constraint to include the three new
   values. The alter is a constraint replacement; no data migration.
2. Land the ORM model and repository for `build_attempts` so
   subsequent commits can use them.
3. Implement `BuildOrchestrationService` with `submit_batch`,
   `submit_single`, `retry`, and `render_shard_payload`. Wire it into
   `services/__init__.py`.
4. Implement `BuildReconciler`. Add the `BUILD_RECONCILER_POLL_SECONDS`
   knob and the startup wiring in `web/server.py`.
5. Add `/api/design-tasks/build`, `/api/design-tasks/{id}/build`,
   `/api/build-attempts`, `/api/build-attempts/{id}`,
   `/api/build-attempts/{id}/retry`. Register them in `web/server.py`
   before the static catch-all.
6. Update `cli.py` if any reconciler triggering ever becomes needed
   from the CLI (not in scope for this change).
7. Frontend: add `web/static/js/views/build-attempts.js`. Register
   route `#/build-attempts` and the sidebar entry. Remove the global
   `<header>` toolbar elements and the mobile bottom bar. Update
   `web/static/js/views/design-tasks.js` to add the multi-select
   checkbox, the bulk `构建已选` button, and the per-row `构建`
   button.
8. Update prompts and docs: a one-sentence note about the `design`
   sub-object in `prompts/shard_prompt.md`, a build-stage row in the
   `docs/architecture.md` package table, a build-stage step in the
   `README.md` pipeline section, and an update to
   `openspec/project.md`'s pipeline flow.
9. Add tests (alembic, repository, orchestration service,
   reconciler, API, dep-direction).

**Rollback strategy:** revert the commits and run
`alembic downgrade -1`. The reverted revision drops the table and
indexes; the design-task status CHECK reverts to the pre-change
allowed values. Any existing rows in `building`, `built`, or
`build_failed` would block the downgrade; for a forced rollback,
operators would manually set such rows back to `designed` or
`failed`. In practice rollback after operators have produced built
artifacts is unlikely — the build-stage data are already valuable.

## Open Questions

- **Reconciler dedup when `/api/state` and the daemon tick overlap.**
  The first writer wins under SELECT FOR UPDATE inside the tick;
  duplicate work is bounded and idempotent. No coordination needed.
- **Should the orchestration service write a `progress_events` row at
  submission?** Currently no — progress events belong to the runner.
  The build-attempts row's `created_at` carries the equivalent signal
  for orchestration audit. Revisit if operators report missing
  visibility on "I clicked, nothing happened yet."
- **`design_tasks.status` enum growth.** Nine values is bordering on
  too many. A future cleanup could split `status` into
  `planning_status` and `build_status` columns, but that is a
  separate change after we see how the new values are used.
