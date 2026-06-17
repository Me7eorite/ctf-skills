# Architecture

Challenge Factory uses a layered `src` layout. `cli.py` is the composition
root, while package-level dependency direction keeps infrastructure, business
rules, subprocess execution, packing, and HTTP transport from importing across
sideways boundaries.

## Dependency Direction

```text
cli          -> {services, web, hermes, packing, persistence, domain, core}
web          -> {services, persistence, domain, core}
services     -> {persistence, hermes, domain, core}
hermes       -> {domain, core}
packing      -> {core}
persistence  -> {domain, core}
domain       -> {core}
core         -> stdlib / third-party only
```

`tests/app/test_dependency_direction.py` enforces this matrix by parsing
`src/**/*.py` imports with `ast`. Notable bans encoded there:

- `hermes` MUST NOT import `persistence` (the runner stays storage-agnostic).
- `persistence` MUST NOT import `web` or `services` (only the relational
  layer's own dependencies).

## Packages

| Package | Responsibility |
| --- | --- |
| `src/cli.py` | Parses commands and composes package APIs; PG lookups are deferred to the `research`/`profile` dispatchers |
| `src/core/paths.py` | Defines every project path through `ProjectPaths` |
| `src/core/jsonio.py` | Reads and writes JSON/JSONL files |
| `src/core/queue.py` | Splits matrices and transitions shard queue state |
| `src/core/state.py` | Stores progress events and latest snapshots in SQLite |
| `src/domain/seeds.py` | Validates and persists generation seed inputs |
| `src/domain/validation.py` | Checks artifacts and runs `validate.sh` |
| `src/domain/reports.py` | Aggregates per-shard reports |
| `src/domain/research.py` / `domain/research_validators.py` | Research DTOs and pure validation rules |
| `src/domain/design_tasks.py` / `domain/challenge_designs.py` | Design task and challenge design DTOs |
| `src/hermes/` | Renders prompts, invokes Hermes, and records runner progress |
| `src/packing/` | Builds delivery bundle v2 artifacts, PDFs, zips, Docker tars, and workbooks |
| `src/persistence/` | PostgreSQL engine/session, SQLAlchemy models, Alembic-backed repositories |
| `src/services/` | Cross-subsystem orchestration with transaction boundaries (research submit/claim/execute, design task planning, challenge design execution) |
| `src/web/` | Builds dashboard data, exposes FastAPI routes, serves static assets, and hosts the research/design HTTP adapters |

`packing` and `hermes` expose their public APIs through package re-exports, so
callers use `from packing import Packer` and `from hermes import HermesRunner`.
Core/domain internals use explicit package paths such as
`from core.paths import ProjectPaths` and
`from domain.validation import ChallengeValidator`.

## Generation Pipeline

```text
matrix
  -> split into category shards
  -> atomically claim one shard
  -> render skill-aware Hermes prompt
  -> publish per-challenge stage events to SQLite
  -> generate and build challenges
  -> run artifact and EXP validation
  -> move shard to done or failed
  -> aggregate reports
```

## Runtime State

`work/` is deliberately outside the package:

```text
work/
├── shards/
│   ├── pending/
│   ├── running/
│   ├── done/
│   └── failed/
├── challenges/
├── logs/
└── reports/
```

The shard directory is the source of truth for queue state. Workers never
modify one shared queue document. `work/state.sqlite3` is a query-oriented
event store for frontend synchronization; losing it does not lose generated
artifacts or queue ownership.

## Future Growth

Future worker pool and task-level persistence work should preserve these
boundaries:

- `core.queue` owns shard queue storage mechanics and file-format compatibility.
- `domain.tasks` should own task models, legal state transitions, and business
  validation when tasks become first-class.
- `worker.*` should own concurrency, scheduling, leases, retries, and timeout
  recovery when a worker pool is introduced.
- `hermes.runner` should execute an already claimed shard/task and avoid direct
  queue-directory manipulation.

## Testing

Tests construct `ProjectPaths` with temporary directories. This avoids touching
real generated challenges and makes queue, dashboard, and validation behavior
deterministic.
