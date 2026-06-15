## Context

`add-postgres-persistence` shipped an empty Alembic baseline and a `src/persistence/` package with engine, session, and transaction primitives. No business tables exist yet. The shard execution pipeline (matrix → `work/shards/pending/` → `HermesRunner` → validation) still requires every challenge spec to be hand-filled in a JSONL matrix.

This change adds the first piece of the research-planning workflow: an operator says "I want N challenges on topic T with difficulty distribution D" and the system researches and persists sources + findings that downstream changes can plan, evaluate, approve, and turn into matrix rows. The actual planning, evaluation, and approval are explicitly **not** in scope here — they need a clean research substrate first.

Hermes itself is external (a `hermes chat` subprocess). The project owns the prompt and the parsing of stdout. Today only `HermesRunner` invokes Hermes, and it's wired tightly to the five-stage shard pipeline. The Research Agent needs a different prompt and a different output contract (one JSON blob, not five-stage progress events), so the cleanest path is to extract the reusable subprocess-invocation plumbing and let two call sites share it.

## Goals / Non-Goals

**Goals:**

- Land the eight research-stage tables behind one Alembic revision, with referential integrity and reversible down-migration.
- Provide typed DTOs and repositories so downstream changes (`add-plan-evaluation-and-approval`) can compose on top without re-deriving the schema.
- Introduce a `services` layer that owns transactions spanning multiple subsystems. `cli` and `web` call into `services`; `services` calls into `persistence`, `hermes`, and `domain`. `hermes` is still forbidden from touching the database.
- Give operators an end-to-end queue workflow: `research submit` enqueues work immediately, and one or more `research worker` processes claim runs, invoke Hermes outside database transactions, heartbeat leases, and persist terminal results.
- Keep `HermesRunner` and the shard pipeline behavior-identical.

**Non-Goals:**

- Plan generation, plan evaluation, approval workflow, versioned challenge specs, promotion to `work/shards/pending/`. All in `add-plan-evaluation-and-approval`.
- UI for inspecting research runs or approving candidate problems. In `add-research-planning-ui`.
- Automated web crawling. Operators supply seed URLs via `--seed-url`; this change persists those URLs on `generation_requests` so workers can render the prompt later. Auto-fetch is a separate change with its own robots.txt + rate-limit story.
- External queue systems or distributed schedulers. The queue lives in PostgreSQL for this change.
- Storing raw page text or Hermes log output in PostgreSQL. Filesystem only.
- Async sessions / asyncpg. Sync only, consistent with the rest of the codebase.

## Decisions

### DEC-1: New `services` package as the orchestration layer

`web` and `cli` currently call straight into `domain` and `core`. Once Hermes invocation + PostgreSQL persistence + filesystem writes need to happen inside a single conceptual operation, neither subsystem is the natural owner of the transaction. Introduce `src/services/` for cross-subsystem orchestration.

Allowed edges (added in the `module-architecture` delta):

| Importer | Allowed targets |
| --- | --- |
| `cli` | `web`, `hermes`, `packing`, `persistence`, `services`, `domain`, `core` |
| `web` | `services`, `persistence`, `domain`, `core` |
| `services` | `persistence`, `hermes`, `domain`, `core` |
| `hermes` | `domain`, `core` (unchanged — still no DB, still no services) |
| `packing` | `core` (unchanged) |
| `persistence` | `domain`, `core` (unchanged) |
| `domain` | `core` (unchanged) |
| `core` | (stdlib + third-party) |

`hermes → services` stays forbidden. `services → web` is forbidden (web is the outermost adapter, never imported by inner layers). `domain → services` is forbidden (domain holds business rules, not orchestration).

**Why not put the worker/executor in `hermes`?** Because it needs `transaction()` from `persistence`, and the project memory + `add-postgres-persistence` design decision explicitly keep `hermes` off the database. Letting `hermes` import `persistence` would also collapse the per-stage event-write latency budget that `HermesRunner` carefully manages.

**Why not put it in `web`?** Because the CLI also needs it. `services` is the single place both adapters call.

### DEC-2: Eight tables in one Alembic revision

`challenge_categories`, `agent_roles`, `hermes_profile_bindings`, `generation_requests`, `research_runs`, `research_sources`, `research_findings`, and the `research_finding_sources` join table all land in one revision (`0002_research_tables`). They form one referentially-coherent unit; splitting them into separate revisions has no benefit and introduces intermediate states that aren't useful.

Down-migration drops the tables in reverse-dependency order (join table → findings → sources → profile bindings → runs → requests → agent roles → categories) and drops the three enum types (`generation_request_status`, `research_run_status`, `research_finding_kind`).

### DEC-3: "Finding has ≥ 1 source" is enforced at the repository, not in the DB

PostgreSQL can express foreign-key cardinality (≥ 0) but cannot natively express "this row has ≥ 1 row in another table" without a deferred trigger. Options considered:

1. **Deferrable BEFORE-COMMIT trigger** that checks each finding for at least one join row. Works, but adds a layer of behavior that's invisible from the table schema and hard to test.
2. **CHECK constraint via `source_count` materialized column** updated by triggers. Even more machinery.
3. **Repository-level invariant**: `ResearchRepository.create_finding(...)` requires a `source_ids` argument of length ≥ 1; insertion happens inside the same transaction as the join rows. Tests cover both success and rejection paths.

Picked option 3. Rationale: the invariant is enforced everywhere the only entry point lives, and the schema stays self-explanatory. Future readers see "ah, the join table exists, must read repository code for the constraint" rather than chasing a hidden trigger.

### DEC-4: Extract shared Hermes subprocess plumbing into `hermes/process.py`

`HermesRunner._invoke`, `_hermes_arguments`, `_apply_legacy_custom_provider`, and `_remove_conflicting_custom_pool` are tied to shard execution today but actually solve a generic problem: "find the Hermes binary, set up env vars, run with a timeout, capture output to a log file." Extract those into `hermes/process.py`. `HermesRunner` uses them as before; the new `hermes/research.py::invoke_research_agent` uses them too.

This is the minimum extraction that lets both call sites share one source of truth. **No behavior change to shard execution.** The existing `runner-resume-and-metrics` tests are the safety net.

### DEC-5: Hermes Research Agent returns one JSON object on stdout

The shard prompt instructs Hermes to write progress events via a CLI subcommand and to produce on-disk artifacts. That contract fits five-stage execution; it does not fit "give me a structured research result." For research, the Agent writes exactly one JSON object to stdout with `sources[]` and `findings[]`, then exits. `services/research_agent_executor.py` reads stdout, parses JSON, and persists sources/findings plus the terminal run transition inside one short transaction.

Every entry in `findings[]` must declare `source_indices: int[]` referencing positions in `sources[]`. The repository rejects findings whose index array is empty. The prompt sample shows both arrays with at least one mutual reference.

If Hermes exits non-zero or writes invalid JSON, the run is persisted as `failed` with the error string — unless the worker has lost the lease / claim token in the interim. A worker that observes a stale claim during the failure write swallows `StaleClaimError` and exits the iteration without further writes; the new owner (created by `claim_next_run`'s lease-recovery path) is responsible for the next attempt. The Hermes subprocess is never inside a database transaction.

### DEC-6: `research submit` is async; execution lives in a PG-backed job queue

`research submit` creates the `generation_requests` row and one `research_runs` row in status `queued`, then exits immediately printing the request id and run id. It does **not** invoke Hermes. Hermes is invoked by a separate long-running process: `challenge-factory research worker --agent-id <W> --loop [--max-jobs N] [--poll-interval-seconds S] [--lease-seconds T] [--hermes-timeout-seconds H]`. The worker enforces `H < T` at startup so Hermes cannot outlive the lease window — see DEC-11 for why that invariant is load-bearing. Multiple workers may run concurrently on the same or different machines; they share state through `research_runs` and coordinate via PostgreSQL row locks.

**Why not synchronous:** a synchronous `submit` ties the operator's terminal to a 5-25 minute Hermes run, holds a database connection across the entire span, makes operator-visible failure modes much worse (lid closed → run lost without trace), and forecloses every multi-agent / multi-machine / retry pattern downstream. Cost of doing async correctly at this stage of the project: one well-known pattern (SKIP LOCKED), a handful of new columns, and one worker subcommand. Cost of bolting async on later: rewriting the runner, migrating the schema, retrofitting the CLI, and re-doing every test.

**Operator UX:** `research submit` prints `run_id` and exits 0 in milliseconds. Operators who want to wait synchronously use `research wait <run_id>` (polls until terminal). Operators who want to observe activity use `research list` or the HTTP queue endpoints.

### DEC-11: Claim via FOR UPDATE SKIP LOCKED; expired leases become failed attempts

Before claiming new work, `ResearchJobService.claim_next_run(agent_id, lease_seconds)` performs lazy lease recovery in the same short transaction: every expired `running` run that it locks is marked `failed` with `last_error='lease expired'`. If the failed attempt is below `generation_requests.max_attempts`, the service inserts a new `queued` run with `parent_run_id` pointing at the expired run and `attempt = expired.attempt + 1`; otherwise it marks the parent request `failed`. This preserves one row per actual Hermes attempt.

After recovery, `claim_next_run` atomically transitions one `queued` row to `running` for the calling worker. It generates a fresh `claim_token` UUID for every claim and returns it with the run snapshot:

```sql
UPDATE research_runs
SET status='running', claimed_by=:agent, claim_token=:claim_token, claimed_at=now(),
    lease_expires_at=now() + (:lease_seconds || ' seconds')::interval,
    heartbeat_at=now()
WHERE id = (
    SELECT id FROM research_runs
    WHERE status='queued'
    ORDER BY created_at
    FOR UPDATE SKIP LOCKED
    LIMIT 1
)
RETURNING *;
```

**SKIP LOCKED** means a second worker running the same query at the same instant naturally takes a different row instead of blocking. **Lazy lease recovery** means a worker that died mid-Hermes is automatically turned into a failed audit row by the next worker to come along — no separate reaper daemon is needed. **claim_token** fences stale writers: even if two processes accidentally use the same `agent_id`, a worker can heartbeat or finalize only the exact lease it received.

**Why no separate reaper:** a dedicated "scan for expired leases and re-queue" cron worker adds an extra moving part. The lazy approach folds recovery into the normal claim path: as long as workers are polling, expired runs get picked up. The cost is up to `lease_seconds` of idle time between worker death and re-claim, which we accept (15 min default).

### DEC-12: Heartbeat from a daemon thread, every 30 seconds

A worker that has claimed a run starts a Python `threading.Thread(daemon=True)` whose only job is to wake every 30 seconds and run:

```sql
UPDATE research_runs
SET heartbeat_at=now(),
    lease_expires_at=now() + (:lease_seconds || ' seconds')::interval
WHERE id=:run_id
  AND status='running'
  AND claimed_by=:agent_id
  AND claim_token=:claim_token;
```

The `AND claimed_by=:agent_id AND claim_token=:claim_token` defensive clause means: if for any reason this worker no longer owns the claim token (its lease expired earlier and `claim_next_run`'s recovery path already marked this row `failed` and possibly created a fresh retry row owned by another worker, our heartbeat thread was paused too long, a duplicate agent id is in play, etc.), our heartbeat updates **nothing** instead of corrupting state. The thread exits when the main worker thread sets a `stop_event`.

If the heartbeat loop observes a lost lease (`heartbeat(...) == False`), it sets a shared `lost_lease` event. The executor must then terminate the Hermes subprocess when possible; if termination is not possible, it must discard the result and skip terminal persistence. `mark_run_completed` and `mark_run_failed` also require the same `claim_token`, so a stale worker cannot complete or fail a run it no longer owns.

**Why a thread, not subprocess polling:** the heartbeat needs to fire continuously while the Hermes subprocess is blocking on stdout. A separate thread is the simplest mechanism that doesn't add complexity to the subprocess wait. SQLAlchemy 2 sessions are not thread-safe across threads, so the heartbeat thread opens its own short-lived session per beat — no shared connection with the main thread.

**Why 30 seconds:** with a 15-minute lease, that's 30 heartbeats per Hermes run. Two missed heartbeats (1 minute of clock drift / network blip) does not expire the lease; nine consecutive misses do. The ratio is loose enough to tolerate transient hiccups and tight enough to detect a truly dead worker within the lease window.

### DEC-13: Retry via new rows chained by `parent_run_id`; max_attempts on generation_requests

Each Hermes attempt is a new `research_runs` row, never a re-used one. On failure:

1. The current run is marked `failed` with `last_error` set; it is now immutable.
2. If `current.attempt < generation_request.max_attempts`, the worker (inside the same short failure transaction) inserts a new `research_runs` row with `parent_run_id = current.id`, `attempt = current.attempt + 1`, `status = 'queued'`. The next worker to claim picks it up automatically.
3. If `current.attempt >= max_attempts`, no new row is created. The `generation_requests.status` becomes `failed`.

**Why a new row per attempt, not in-place update:** preserves the full audit trail. "Why did this request fail?" is answered by walking the chain: attempt 1 timed out at 12 min, attempt 2 returned invalid JSON, attempt 3 succeeded. An in-place model destroys this history.

**Why max_attempts on the request, not the run:** operator intent ("I'm willing to retry this topic up to 3 times") belongs on the intent table. Each run inherits the cap from its parent generation_request; runs do not carry their own limit.

**Failure categories:** the `last_error` text column holds the human-readable reason. A future change MAY add a `failure_category` enum (timeout / invalid_json / hermes_crash / lease_expired_during_persistence / unknown) for finer-grained retry policy; out of scope here.

### DEC-14: generation_requests.status reflects the LATEST run; request is terminal only when no more attempts can run

`generation_requests.status` is a denormalized view of "what's happening with this request right now". `generation_requests.seed_urls` stores the operator-supplied URL list used by every attempt in the chain:

| Latest run status                            | Request status |
|----------------------------------------------|----------------|
| (no runs yet)                                | `draft`        |
| any run in `queued` or `running`             | `researching`  |
| latest run `completed`                       | `researched`   |
| latest run `failed`, attempt < max_attempts  | `researching` (a retry row was just created) |
| latest run `failed`, attempt = max_attempts  | `failed`       |

Synchronization is done by the job service inside each terminal transition: when `mark_run_completed(...)`, `mark_run_failed(..., retry=...)`, or `complete_run_with_results(...)` runs with the current `claim_token`, it updates the parent request's status atomically inside the same short transaction that writes the terminal state on the `research_runs` row (and, for `complete_run_with_results`, the sources/findings as well). No triggers, no background sync. The Requirement spec encodes this so the rule is testable.

**A request status of `researched` is not immutable.** An operator MAY (in a future change, not this one) re-submit a fresh attempt that lands as a new queued run — at which point the request flips back to `researching`. We don't implement re-submit now, but the schema and state machine permit it.

### DEC-7: Filesystem layout for raw text and logs

- `work/research/sources/<run_id>/<source_index>.txt` — raw fetched page text (when present). Path stored in `research_sources.raw_text_path`.
- `work/research/logs/<run_id>.log` — Hermes stdout/stderr capture. Path stored in `research_runs.hermes_log_path`.

`work/` is already where shard state lives; reusing it keeps a single mental model for "transient operational state." Nothing in `work/research/` is required for PostgreSQL queries.

### DEC-9: One generation request, one challenge category — backed by a lookup table

Every `generation_requests` row carries a required `category text` column with a foreign key to `challenge_categories.code`. The lookup table is seeded by `0002_research_tables` with the three currently-supported codes (`web`, `pwn`, `re`) plus a `display_name` and `description` per row.

**Why required, not nullable:** without category, a downstream consumer would have to infer category from the topic string ("SQL injection" → web?), which is exactly the kind of guessing this change exists to eliminate. The matrix system, `ShardQueue.split_*`, and the seed editor already segregate by category; the research stage must do the same so its output can be promoted to a category-specific shard.

**Why one category per request, not many-to-many:** the operator's intent is "give me N web challenges on SQL injection" or "give me N re challenges on anti-debug," not "give me a mix." Mixed-category research runs would also break the dedup story: a finding labeled "anti-debug" only makes sense in re. If an operator wants both, they submit two requests.

**Why a lookup table, not a PG enum:**

1. **Display metadata.** UI and CLI need a human-readable name (`"Web 安全"`, `"Reverse"`). A lookup table holds that directly; an enum forces a parallel hardcoded mapping in Python.
2. **Operational extensibility.** Adding `crypto`, `ai`, `misc`, `iot` later is an `INSERT INTO challenge_categories` — no schema migration, no code change. Enum extension requires `ALTER TYPE ... ADD VALUE` in a migration, which is awkward to roll back.
3. **No real downside.** The lookup table is read-mostly (~5 rows in the foreseeable future), small, and FK-joined with the same access patterns as an enum would be. PG enforces the whitelist via the FK constraint exactly the way an enum would via type-checking.
4. **Future-proof for category attributes.** When categories grow attributes (default runtime caps, base docker images, allowed network egress), they get columns on `challenge_categories` rather than scattered Python constants.

**Decoupling from `core.queue.SUPPORTED_CATEGORIES`:** the existing Python set remains the source of truth for the **shard execution pipeline** (which categories the prompt, validation, and image-build steps actually know how to render). The new lookup table is the source of truth for the **research-planning layer**. They start equal (both = `{web, pwn, re}`). When an operator adds `crypto` to the lookup table, research can begin collecting crypto sources/findings immediately, even though the shard pipeline does not yet know how to generate a crypto challenge. That's a feature, not a bug — research output can pile up while a follow-up change teaches the shard pipeline a new category. A startup-time warning logs if `challenge_categories` contains codes outside `SUPPORTED_CATEGORIES`, but it does not block boot.

**Validation point:** service/repository-level (`ResearchJobService.submit_request` rejects an unknown category by checking the lookup table before INSERT — saves a wasted FK round-trip) plus the DB FK constraint (defense in depth). The CLI `--category` flag is required; the HTTP API rejects missing/unknown category with 400. The argparse `choices=` cannot be hardcoded; it loads from the DB at startup.

### DEC-10: Hermes profile binding — own the mapping, not the contents

Hermes profiles are a Hermes-side concept: each profile is a complete independent Hermes home directory (`~/.hermes/profiles/<name>/`) containing `config.yaml`, `.env`, `SOUL.md`, skills, sessions, memory, cron tasks, gateway state, and its own state DB. Hermes already provides the full lifecycle (`hermes profile create/list/delete/clone/export/import`).

This change introduces **just enough** schema and code so the project knows **which profile to invoke for which agent role** without re-implementing Hermes' profile management.

**What we own (in PostgreSQL):**

- `agent_roles` lookup table — the canonical set of project-internal agent roles. Seeded with `research`. `planning` joins in `add-candidate-challenges`; `shard_execution` may join later if/when `HermesRunner` is taught to consult the binding table.
- `hermes_profile_bindings` — one row per role, holding `profile_name`, `status (enabled|disabled)`, forensic metadata (`last_used_at`, `last_used_run_id`).
- `research_runs.profile_name_used` — denormalized snapshot of which profile was active when this specific run executed, written once and never updated. Forensic value: a binding change six months from now does not destroy the audit trail of what produced a given run's sources/findings.

**What we explicitly do NOT own:**

- Profile contents (`SOUL.md`, `config.yaml`, skills, sessions, memory, cron, state DB). These remain in `~/.hermes/profiles/<name>/` and continue to be created/edited/deleted via `hermes profile *` and direct file editing (under version control or Hermes' own profile-distribution mechanism).
- Profile CRUD. We never create, delete, clone, or modify Hermes profiles. The `bind` command verifies the named profile exists (by shelling out to `hermes profile show <name>`) and persists the binding; if the profile is absent, `bind` refuses with `"profile <name> does not exist; create it with 'hermes profile create <name>' first"`.
- Hermes Kanban auto-routing. Hermes has its own orchestrator that routes work to profiles by their `--description`. We deliberately bypass that — every invocation explicitly passes `-p <profile_name>` resolved from our binding table. This is intentional: DB-driven explicit binding is more auditable than description-based routing for a CTF pipeline.
- Honcho memory sharing, gateway/bot tokens, s6 supervision. These Hermes-side features are out of our scope.

**Resolution at execution time** (sketch; real flow uses claim_token, short transactions, and a heartbeat thread — see DEC-11/12):

```
binding = job_service.get_binding("research")
profile_name = binding.profile_name if binding and binding.enabled else "default"
if profile_name == "default" and (binding is None or not binding.enabled):
    log.warning("research binding missing or disabled; falling back to default profile")

# inside a short transaction; the run row was already claimed with claim_token
job_service.set_profile_name_used(run.id, agent_id, claim_token, profile_name)

# OUTSIDE any transaction
returncode, stdout = invoke_research_agent(prompt, profile_name=profile_name, ...)

# inside a short transaction; commit only if claim_token is still ours
job_service.mark_run_completed(run.id, agent_id, claim_token, sources=..., findings=...)
job_service.touch_binding("research", last_used_run_id=run.id)
```

The fallback to `default` keeps the system working when bindings haven't been configured; the WARNING makes the situation visible. `touch_binding` advances `last_used_at` and `last_used_run_id` only on success — failed runs do not bump them.

### DEC-8: CLI subcommand group, not a flat command

`challenge-factory` already has many top-level subcommands (`init`, `split`, `run`, `serve`, `validate`, `durations`). Adding three more (`research-submit`, `research-show`, `research-list`) bloats `--help`. Group them under `challenge-factory research <verb>` instead. Existing subcommands are not renamed.

## Risks / Trade-offs

- **`hermes/process.py` extraction risks regressing shard execution.** → Mitigated by keeping the extraction behavior-preserving and relying on the existing `tests/app/test_hermes_runner*.py` + `tests/app/test_runner_*.py` suite as a safety net. The runner's `process_one` flow is unchanged; only the helper imports move.
- **PostgreSQL-backed queue is more code than a synchronous MVP.** → Accepted because the operator model requires multiple long-running Hermes workers. The added complexity is bounded to queue columns, short transaction methods, and worker tests.
- **A stale worker may finish Hermes after losing its lease.** → Mitigated by `claim_token` fencing on heartbeat and terminal transitions. The executor also discards results when the heartbeat loop reports lease loss.
- **Repository-enforced "≥1 source" invariant can be bypassed by raw SQL.** → Accepted. Anyone running raw SQL against production tables is already off the rails; the invariant is documented in `docs/persistence.md` (updated in tasks) and in `ResearchRepository` docstrings.
- **Operators may submit no seed URLs.** → The prompt instructs Hermes to acknowledge an empty seed-URL list with at least one finding labeled `needs_operator_seed_urls`, so the failure mode is visible rather than silent. Auto-fetch is a separate change.
- **Adding `services` introduces a new layer that future contributors might overuse.** → Documented in `openspec/project.md` (update in tasks) with one paragraph: "services owns cross-subsystem transactions and orchestration; if your code does not need both `persistence` and `hermes` or both `persistence` and a multi-step domain operation, it does not belong in `services`."

## Migration Plan

1. Apply Alembic revision against `challenge_factory`: `tools/scripts/db.sh new "research tables"` (autogenerate scaffold) then hand-edit + `tools/scripts/db.sh up`.
2. The new `services` package starts with `ResearchJobService`, `ResearchAgentExecutor`, and `ResearchWorker`. No existing imports break.
3. CLI subcommand group is additive; existing subcommands keep working.
4. `hermes/process.py` extraction is behavior-preserving; `HermesRunner.process_one` continues to pass existing tests.
5. **Start at least one research worker process** before treating the deployment as complete. A submitted request stays `queued` until a worker claims it; without an active worker, operators see "submitted but never starts" and assume the system is broken. Example: `challenge-factory research worker --agent-id $(hostname) --loop --lease-seconds 900 --hermes-timeout-seconds 810`. Operators are expected to wrap this in their preferred process supervisor (systemd, supervisord, tmux, etc.) — out of scope for this change.

**Rollback:** stop the worker processes first (`SIGTERM` is enough — workers exit between jobs), then `tools/scripts/db.sh down` to revert the schema. Reverting the commit removes the code. The previous change's `0001_baseline` baseline remains stamped.

## Open Questions

- None blocking. One explicit default remains: "≥ 1 source" is enforced in repository, not as a DB trigger (DEC-3).
