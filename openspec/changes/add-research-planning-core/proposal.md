## Why

`add-postgres-persistence` landed the PostgreSQL substrate and an empty Alembic baseline. Operators still cannot say "research SQL injection coverage" and get persisted sources + findings — every matrix row is hand-filled. This change introduces the first real tables and the first end-to-end research workflow so plan generation, evaluation, approval, and the eventual UI have something to stand on.

## What Changes

- New Alembic revision `0002_research_tables` introduces five tables and three enums:
  - `generation_requests` (category enum `web|pwn|re`, topic, target_count, difficulty_distribution jsonb, runtime_constraints jsonb, status enum, timestamps). Category is required and reuses `core.queue.SUPPORTED_CATEGORIES` so research output cannot mix re and web challenges by accident.
  - `research_runs` (fk → generation_requests, status enum, started_at, finished_at, error, hermes_log_path).
  - `research_sources` (fk → research_runs, url, title, summary, content_hash indexed, fetched_at, raw_text_path).
  - `research_findings` (fk → research_runs, kind enum, label, summary).
  - `research_finding_sources` join table; every finding MUST reference ≥ 1 source.
- Add `src/domain/research.py` with dataclass DTOs and validators (difficulty distribution sums to target_count; finding-without-source rejected).
- Add `src/persistence/models/research.py` (SQLAlchemy 2 declarative) and `src/persistence/repositories/research.py` (typed CRUD + queries, no business logic).
- Add a new `src/services/` package with `services/research_runner.py::ResearchRunner` that orchestrates: load generation_request → create research_run → render Research prompt → invoke Hermes → parse stdout JSON → persist sources + findings inside one transaction → mark run completed or failed.
- Add `prompts/research_prompt.md` defining the Research Agent contract: inputs (topic, count, distribution, optional operator seed URLs), output (single JSON on stdout with `sources[]` and `findings[]`; every finding declares `source_indices: int[]`).
- Extract reusable Hermes subprocess plumbing from `hermes/runner.py` into `hermes/process.py` and add `hermes/research.py::invoke_research_agent`. `HermesRunner` stays narrow (shard execution). Hermes never touches the database.
- Add CLI subcommand group `challenge-factory research {submit,show,list}` for operator-driven invocation. `submit` runs synchronously; failures persist a `failed` research_run with an error string.
- Add read-only HTTP endpoints `GET /api/research/requests` and `GET /api/research/requests/{id}`. Existing dashboard HTML/JS is untouched.
- Raw fetched page text and Hermes log files live on disk under `work/research/sources/` and `work/research/logs/`. PostgreSQL holds metadata + content_hash only.
- Extend `tests/app/test_dependency_direction.py` with `services` and rejection scenarios (`hermes → services`, `services → web`, `domain → services`).
- Operator supplies seed URLs via `--seed-url`. Automated web crawling is out of scope for this change.

**BREAKING**: None. No existing module imports the new tables or the new `services` package.

## Capabilities

### New Capabilities
- `research-planning`: generation request lifecycle (scoped to one challenge category), research run lifecycle, source + finding model with the "every finding references ≥ 1 source" invariant, the no-direct-shard-promotion rule, and the Hermes Research Agent prompt contract.

### Modified Capabilities
- `module-architecture`: add `services` to the recognized packages list and the dependency direction matrix. New allowed edges `cli → services`, `web → services`, `services → persistence`, `services → hermes`, `services → domain`, `services → core`. Forbidden edges `hermes → services`, `domain → services`, `services → web` remain forbidden and are tested.

## Impact

- Adds `src/services/`, `src/domain/research.py`, `src/persistence/models/` (new subpackage with `__init__.py` and `research.py`), `src/persistence/repositories/` (new subpackage with `__init__.py` and `research.py`), `prompts/research_prompt.md`, `alembic/versions/0002_research_tables.py`.
- Splits Hermes subprocess plumbing: adds `src/hermes/process.py` and `src/hermes/research.py`; updates `src/hermes/runner.py` to consume the extracted helpers (no behavior change to shard execution).
- Adds CLI subcommand group in `src/cli.py`. Adds HTTP read endpoints in `src/web/server.py` and a thin read model.
- Adds tests under `tests/app/`: `test_research_domain.py`, `test_research_repository.py` (postgres-marked), `test_research_runner.py` (postgres-marked, Hermes mocked), `test_research_prompt.py`, `test_research_cli.py`, `test_research_alembic.py` (postgres-marked). Extends `test_dependency_direction.py`.
- Filesystem: creates `work/research/sources/` and `work/research/logs/` lazily on first run. Out of scope: object-storage backend.
- Dev DB `challenge_factory` on 192.168.6.150 already exists from the previous change; the new revision lands on top of `0001_baseline`. Repository and Alembic tests reuse `challenge_factory_test`.
- `src/core/state.py`, `work/state.sqlite3`, the shard queue, `work/shards/`, and any prompt other than the new `research_prompt.md` are NOT touched. The event SQLite store remains the source of truth for shard progress; PostgreSQL covers research metadata only.
