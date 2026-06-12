# Architecture

Challenge Factory uses a layered `src` layout. `cli.py` is the composition
root, while package-level dependency direction keeps infrastructure, business
rules, subprocess execution, packing, and HTTP transport from importing across
sideways boundaries.

## Dependency Direction

```text
cli      -> {web, hermes, packing, domain, core}
web      -> {domain, core}
hermes   -> {domain, core}
packing  -> {core}
domain   -> {core}
core     -> stdlib / third-party only
```

`tests/app/test_dependency_direction.py` enforces this matrix by parsing
`src/**/*.py` imports with `ast`.

## Packages

| Package | Responsibility |
| --- | --- |
| `src/cli.py` | Parses commands and composes package APIs |
| `src/core/paths.py` | Defines every project path through `ProjectPaths` |
| `src/core/jsonio.py` | Reads and writes JSON/JSONL files |
| `src/core/queue.py` | Splits matrices and transitions shard queue state |
| `src/core/state.py` | Stores progress events and latest snapshots in SQLite |
| `src/domain/seeds.py` | Validates and persists generation seed inputs |
| `src/domain/validation.py` | Checks artifacts and runs `validate.sh` |
| `src/domain/reports.py` | Aggregates per-shard reports |
| `src/hermes/` | Renders prompts, invokes Hermes, and records runner progress |
| `src/packing/` | Builds delivery bundle v2 artifacts, PDFs, zips, Docker tars, and workbooks |
| `src/web/` | Builds dashboard data, exposes FastAPI routes, and serves static assets |

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

## Frontend stack

The console SPA lives in `frontend/` at the repo root — explicitly outside
`src/` so the `module-architecture` dependency-direction guard does not apply
to it.

```text
frontend/                    # Vue 3 + Vite + TypeScript + Tailwind workspace
├── package.json             # vue, vue-router, pinia, @tanstack/vue-query, monaco-editor, …
├── vite.config.ts           # base: '/static/dist/'; outDir: ../src/web/static/dist
├── tailwind.config.ts       # 6 semantic color groups, 4 font sizes, 8 px spacing, 3 radii
├── .eslintrc.cjs            # blocks raw Tailwind palette names (bg-blue-500 …)
└── src/
    ├── pages/               # one per top-level route, lazy-loaded by the router
    ├── components/          # AppShell, CommandPalette, ToastStack, CapabilityTile, MonacoViewer
    ├── components/ui/       # shadcn-style primitives (Button, Card, Skeleton, EmptyState, …)
    ├── composables/         # useApi, useEventStream, useDirty, useTransition, …
    ├── stores/              # pinia: ui, runs, workers, settings, notifications
    ├── router/              # vue-router routes
    └── assets/empty-states/ # SVG illustrations for EmptyState + PlaceholderPage
```

The frontend cannot import Python modules and the backend cannot import
TypeScript modules. The two communicate exclusively through the documented
HTTP API. The production build emits hashed assets to `src/web/static/dist/`,
which is committed to git so operators who only run `uv sync` can serve the
SPA without Node.

## SPA fallback contract

`src/web/server.py` registers routes in this order:

1. All `/api/*` JSON routes (state, runs, capabilities, kpis, llm, presets,
   sse, …).
2. `GET /static/dist/{path:path}` — hashed asset route. Files under
   `assets/` receive `Cache-Control: public, max-age=31536000, immutable`;
   other paths receive `no-store`. Path traversal is rejected with HTTP 400.
3. `GET /{path:path}` — SPA catch-all. Returns `dist/index.html` with
   `Cache-Control: no-store` so the shell always picks up the latest asset
   hashes after a deploy.

`tests/app/test_spa_fallback.py` enforces the contract: `/api/state` returns
JSON, arbitrary client routes return the SPA HTML shell with `<div id="app">`,
hashed assets advertise the immutable cache, and traversal returns 400.

## Real-time event stream

`GET /api/events/stream` exposes a `text/event-stream` channel. The route
tails the `progress_events` SQLite table with 1 s polling, emits a
`:heartbeat` line every 15 s, and replays events with `id > Last-Event-ID`
when a client reconnects. The response sets `X-Accel-Buffering: no` so
nginx (`proxy_buffering off`) does not buffer the stream. The browser
composable `useEventStream` reconnects with 1 s → 2 s → 4 s backoff.

## Testing

Tests construct `ProjectPaths` with temporary directories. This avoids touching
real generated challenges and makes queue, dashboard, and validation behavior
deterministic.
