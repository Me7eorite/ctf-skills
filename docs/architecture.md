# Architecture

Challenge Factory uses a flat `src` application layout. The design keeps
filesystem persistence, domain operations, process execution, and HTTP
transport separate while avoiding an extra package-directory level.

## Dependency Direction

```text
CLI / HTTP
    |
    v
Application services
    |
    v
Filesystem helpers and project paths
```

Lower-level modules never import the CLI or HTTP server.

## Modules

| Module | Responsibility |
| --- | --- |
| `src/paths.py` | Defines every project path through `ProjectPaths` |
| `src/jsonio.py` | Reads and writes JSON/JSONL files |
| `src/shards.py` | Splits matrices and transitions shard queue state |
| `src/hermes.py` | Renders prompts and executes Hermes for claimed shards |
| `src/validation.py` | Checks artifacts and runs `validate.sh` |
| `src/reports.py` | Aggregates per-shard reports |
| `src/state.py` | Stores progress events and latest snapshots in SQLite |
| `src/dashboard.py` | Builds dashboard data and manages local tasks |
| `src/webserver.py` | Converts HTTP requests into dashboard service calls |
| `src/cli.py` | Parses commands and delegates to application services |

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

## Testing

Tests construct `ProjectPaths` with temporary directories. This avoids touching
real generated challenges and makes queue, dashboard, and validation behavior
deterministic.

## Future Growth

The current standard-library HTTP layer can be replaced without changing queue
or generation services. Multi-host workers would require a transactional queue
and authenticated API, but the module boundaries can remain the same.
