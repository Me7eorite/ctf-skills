## Context

`add-postgres-persistence` shipped an empty Alembic baseline and a `src/persistence/` package with engine, session, and transaction primitives. No business tables exist yet. The shard execution pipeline (matrix → `work/shards/pending/` → `HermesRunner` → validation) still requires every challenge spec to be hand-filled in a JSONL matrix.

This change adds the first piece of the research-planning workflow: an operator says "I want N challenges on topic T with difficulty distribution D" and the system researches and persists sources + findings that downstream changes can plan, evaluate, approve, and turn into matrix rows. The actual planning, evaluation, and approval are explicitly **not** in scope here — they need a clean research substrate first.

Hermes itself is external (a `hermes chat` subprocess). The project owns the prompt and the parsing of stdout. Today only `HermesRunner` invokes Hermes, and it's wired tightly to the five-stage shard pipeline. The Research Agent needs a different prompt and a different output contract (one JSON blob, not five-stage progress events), so the cleanest path is to extract the reusable subprocess-invocation plumbing and let two call sites share it.

## Goals / Non-Goals

**Goals:**

- Land all five research-stage tables behind one Alembic revision, with referential integrity and reversible down-migration.
- Provide typed DTOs and repositories so downstream changes (`add-plan-evaluation-and-approval`) can compose on top without re-deriving the schema.
- Introduce a `services` layer that owns transactions spanning multiple subsystems. `cli` and `web` call into `services`; `services` calls into `persistence`, `hermes`, and `domain`. `hermes` is still forbidden from touching the database.
- Give operators an end-to-end CLI command (`research submit`) that runs synchronously and persists either a `completed` or `failed` research run.
- Keep `HermesRunner` and the shard pipeline behavior-identical.

**Non-Goals:**

- Plan generation, plan evaluation, approval workflow, versioned challenge specs, promotion to `work/shards/pending/`. All in `add-plan-evaluation-and-approval`.
- UI for inspecting research runs or approving candidate problems. In `add-research-planning-ui`.
- Automated web crawling. Operators supply seed URLs via `--seed-url`. Auto-fetch is a separate change with its own robots.txt + rate-limit story.
- Background queueing or worker pools for research runs. `research submit` is synchronous.
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

**Why not put `ResearchRunner` in `hermes`?** Because it needs `transaction()` from `persistence`, and the project memory + `add-postgres-persistence` design decision explicitly keep `hermes` off the database. Letting `hermes` import `persistence` would also collapse the per-stage event-write latency budget that `HermesRunner` carefully manages.

**Why not put it in `web`?** Because the CLI also needs it. `services` is the single place both adapters call.

### DEC-2: Five tables in one Alembic revision

`generation_requests`, `research_runs`, `research_sources`, `research_findings`, and the `research_finding_sources` join table all land in one revision (`0002_research_tables`). They form one referentially-coherent unit; splitting them into separate revisions has no benefit and introduces intermediate states that aren't useful.

Down-migration drops the tables in reverse-dependency order (join table → findings → sources → runs → requests) and drops the two enum types (`generation_request_status`, `research_run_status`, `research_finding_kind`).

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

The shard prompt instructs Hermes to write progress events via a CLI subcommand and to produce on-disk artifacts. That contract fits five-stage execution; it does not fit "give me a structured research result." For research, the Agent writes exactly one JSON object to stdout with `sources[]` and `findings[]`, then exits. `services/research_runner.py` reads stdout, parses JSON, and persists inside one transaction.

Every entry in `findings[]` must declare `source_indices: int[]` referencing positions in `sources[]`. The repository rejects findings whose index array is empty. The prompt sample shows both arrays with at least one mutual reference.

If Hermes exits non-zero or writes invalid JSON, the run is persisted as `failed` with the error string; nothing partial lands in the DB. Wrapped by `transaction()` so a parse failure leaves zero rows.

### DEC-6: `research submit` is synchronous

Asynchronous queueing would need a worker pool, lease handling, and visibility into running jobs — all explicit non-goals in this change. For the MVP, `research submit` blocks until Hermes returns or the timeout fires, then exits with the run id printed. Operators who need parallelism can run multiple `research submit` commands in different terminals.

### DEC-7: Filesystem layout for raw text and logs

- `work/research/sources/<run_id>/<source_index>.txt` — raw fetched page text (when present). Path stored in `research_sources.raw_text_path`.
- `work/research/logs/<run_id>.log` — Hermes stdout/stderr capture. Path stored in `research_runs.hermes_log_path`.

`work/` is already where shard state lives; reusing it keeps a single mental model for "transient operational state." Nothing in `work/research/` is required for PostgreSQL queries.

### DEC-9: One generation request, one challenge category

Every `generation_requests` row carries a required `category` column whose values are exactly `core.queue.SUPPORTED_CATEGORIES` (`web | pwn | re`). Implemented as a PostgreSQL enum type `challenge_category` so the schema is self-explanatory and symmetric with the other enums (`generation_request_status`, `research_run_status`, `research_finding_kind`).

**Why required, not nullable:** without category, a downstream consumer would have to infer category from the topic string ("SQL injection" → web?), which is exactly the kind of guessing this change exists to eliminate. The matrix system, `ShardQueue.split_*`, and the seed editor already segregate by category; the research stage must do the same so its output can be promoted to a category-specific shard.

**Why one category per request, not many-to-many:** the operator's intent is "give me N web challenges on SQL injection" or "give me N re challenges on anti-debug," not "give me a mix." Mixed-category research runs would also break the dedup story: a finding labeled "anti-debug" only makes sense in re. If an operator wants both, they submit two requests.

**Why reuse the existing set rather than a lookup table:** the set is fixed and small; a lookup table buys nothing today. If categories later grow attributes (display names, prerequisites, default runtime caps), we promote the enum to a `challenge_categories` table in a follow-up change.

**Validation point:** repository-level (`ResearchRepository.create_generation_request` rejects an unknown category before any insert) plus the DB enum (defense in depth). The CLI `--category` flag is required; the HTTP API rejects missing/unknown category with 400.

### DEC-8: CLI subcommand group, not a flat command

`challenge-factory` already has many top-level subcommands (`init`, `split`, `run`, `serve`, `validate`, `durations`). Adding three more (`research-submit`, `research-show`, `research-list`) bloats `--help`. Group them under `challenge-factory research <verb>` instead. Existing subcommands are not renamed.

## Risks / Trade-offs

- **`hermes/process.py` extraction risks regressing shard execution.** → Mitigated by keeping the extraction behavior-preserving and relying on the existing `tests/app/test_hermes_runner*.py` + `tests/app/test_runner_*.py` suite as a safety net. The runner's `process_one` flow is unchanged; only the helper imports move.
- **Synchronous `research submit` ties up the operator's terminal for the Hermes timeout (default 1500 s).** → Acceptable for MVP. Operators who care can run multiple in parallel or set a shorter `--timeout`. A future change can add background queueing.
- **Repository-enforced "≥1 source" invariant can be bypassed by raw SQL.** → Accepted. Anyone running raw SQL against production tables is already off the rails; the invariant is documented in `docs/persistence.md` (updated in tasks) and in `ResearchRepository` docstrings.
- **Operator-supplied seed URLs are easy to forget.** → The prompt instructs Hermes to acknowledge an empty seed-URL list with at least one finding labeled `needs_operator_seed_urls`, so the failure mode is visible rather than silent. Auto-fetch is a separate change.
- **Adding `services` introduces a new layer that future contributors might overuse.** → Documented in `openspec/project.md` (update in tasks) with one paragraph: "services owns cross-subsystem transactions and orchestration; if your code does not need both `persistence` and `hermes` or both `persistence` and a multi-step domain operation, it does not belong in `services`."

## Migration Plan

1. Apply Alembic revision against `challenge_factory`: `tools/scripts/db.sh new "research tables"` (autogenerate scaffold) then hand-edit + `tools/scripts/db.sh up`.
2. The new `services` package starts empty except for `ResearchRunner`. No existing imports break.
3. CLI subcommand group is additive; existing subcommands keep working.
4. `hermes/process.py` extraction is behavior-preserving; `HermesRunner.process_one` continues to pass existing tests.

**Rollback:** `tools/scripts/db.sh down` reverts the schema. Reverting the commit removes the code. The previous change's `0001_baseline` baseline remains stamped.

## Open Questions

- None blocking. Two we have explicit defaults for and will revisit only if operators push back:
  - "≥ 1 source" enforced in repository, not as a DB trigger (DEC-3).
  - `research submit` is synchronous (DEC-6).
