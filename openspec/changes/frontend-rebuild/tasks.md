## 1. Backend precursor (lands against legacy UI)

- [ ] 1.1 Add `src/web/trace.py` module exposing a `trace_router` with `GET /api/trace/stream` returning `StreamingResponse(media_type="text/event-stream")`
- [ ] 1.2 Define `TraceEvent` dataclass with required fields `event_id (int), worker, shard, stage, status, message, ts (float)` matching the `agent-trace-stream` spec. Provide a `from_row(row) -> TraceEvent` mapper that (a) converts `progress_events.created_at` (ISO string) to unix epoch seconds for `ts`, and (b) truncates `message` to 240 characters. Add a `to_sse()` serializer that emits a single-line `data:` JSON payload. v1 does NOT emit file/tool/log fields — they will be added once `progress_events` grows the columns
- [ ] 1.3 Implement an async trace stream generator that, on each new client connection, captures the current `MAX(progress_events.id)` as the starting cursor, then tails the table by polling `StateStore.trace_events_after(cursor)`, emitting new rows as SSE data events and a 15s keep-alive ping when no new rows arrive. Reconnects MUST NOT replay history — the cursor always starts at the current max at connect time
- [ ] 1.3a Add a `StateStore.trace_events_after(event_id: int, limit: int = 100) -> list[dict]` query helper returning `progress_events` rows ordered by id ASC with id > `event_id`. Add a `StateStore.max_event_id() -> int` helper that returns the current max id (used by the SSE generator to seed the cursor). Do not add in-process observer hooks; they would miss events written by worker subprocesses
- [ ] 1.4 Wire `trace_router` into `src/web/server.py`'s FastAPI app
- [ ] 1.5 Add `src/hermes/fake.py` with `FakeHermesRunner` that (a) writes one `metadata.json` per demo challenge containing at least `id, title, category, difficulty, build_status, solve_status, flag` plus a runtime descriptor (`runtime` / `framework` / `language` / `target_format`); (b) splits a built-in ≥3-challenge matrix spanning ≥2 categories into shards; (c) assigns each shard a worker named with the `demo-` prefix (e.g., `demo-01`, `demo-02`) so the idempotency reset in 1.7 can target them; (d) walks each shard through every `core.state.STAGES` value via `StateStore.record(...)`; (e) finishes the full batch in ≤5 seconds wall-clock
- [ ] 1.6 Add `--demo` boolean flag to the `serve` subcommand in `src/cli.py`; when set, start `FakeHermesRunner` in a daemon thread before `uvicorn.run`
- [ ] 1.7 Make demo mode idempotent: on start, clear any shard whose worker name starts with `demo-` and re-split the built-in matrix into `pending/`
- [ ] 1.8 Add `GET /api/mode` endpoint (registered directly on `src/web/server.py`'s app) returning `{"demo": bool}` so the SPA can render the DEMO badge
- [ ] 1.8a Add a `require_writable` FastAPI dependency in `src/web/server.py` that, when demo mode is active, aborts with status 409 and a JSON body exactly equal to `{"ok": false, "message": "Demo mode is read-only"}`. Do not use a plain `HTTPException(detail={...})`, because that would produce `{"detail": {...}}`. Attach this dependency to all six mutating routes: `POST /api/actions/worker`, `POST /api/actions/validate`, `POST /api/seeds`, `DELETE /api/seeds/{challenge_id}`, `POST /api/seeds/enqueue`, `POST /api/shards/{state}/{name}/requeue`. The dependency MUST be a no-op when demo mode is off, preserving pre-change behavior
- [ ] 1.9 Tests under `tests/web/test_trace.py`: (a) SSE response sets `text/event-stream` and emits at least one ping within 15s; (b) `data:` lines parse as JSON with the seven required keys; (c) end-to-end: call `StateStore.record(...)` after a client connects → the row appears as an SSE event; (d) reconnect after history rows exist → the new connection emits NO historical events (cursor starts at current max id); (e) `message` longer than 240 chars is truncated in the emitted event
- [ ] 1.9a Tests under `tests/web/test_mode.py`: `GET /api/mode` returns `{"demo": true}` when server is started with `--demo` and `{"demo": false}` otherwise (covers both scenarios in `demo-mode` spec)
- [ ] 1.9b Tests under `tests/web/test_readonly.py`: parametrize over the six mutating endpoints and assert (a) demo mode returns 409 with body `{"ok": false, "message": "Demo mode is read-only"}`; (b) non-demo mode does NOT return the demo body (status and body match pre-change behavior). Also assert that `GET /api/state`, `GET /api/mode`, `GET /api/logs/<name>`, and `GET /api/trace/stream` all succeed in demo mode
- [ ] 1.10 Tests under `tests/hermes/test_fake.py`: replay completes inside 5s, every stage in `STAGES` is recorded, second start replays from scratch

## 2. Frontend scaffold

- [ ] 2.1 Create top-level `web/` directory with `npx create-next-app@14 web --typescript --app --tailwind --eslint --src-dir=false --import-alias '@/*'`
- [ ] 2.2 Run `npx shadcn@latest init` in `web/`, accept dark theme defaults, confirm components install under `web/components/ui/`
- [ ] 2.3 Install `@tremor/react`, `framer-motion`, `lucide-react`
- [ ] 2.4 Configure `web/next.config.mjs` rewrites: `/api/:path*` → `http://127.0.0.1:4173/api/:path*` for dev
- [ ] 2.5 Configure `next.config.mjs` `output: "export"` so `npm run build` emits `web/out/`; `scripts/build_frontend.sh` will copy `web/out/` to `src/web/static/dist/`
- [ ] 2.6 Set theme tokens in `web/app/globals.css`: surface `#0A0B0F`, card `#13151B`, border `#1B1E26`, text `#E6E8EE`/`#8B92A1`, accent `#22D3EE`
- [ ] 2.7 Add `web/lib/api/client.ts` with typed `apiGet<T>(path)` wrapper; lint rule or grep-test forbidding raw `fetch("/api` in `web/app/` and `web/components/`
- [ ] 2.8 Add `web/lib/api/trace.ts` using native `EventSource` against `/api/trace/stream` with auto-reconnect (≤5s backoff) and typed event payload

## 3. Six views ported to React

- [ ] 3.1 Build `web/app/layout.tsx` with sidebar (six nav items) + header (refresh button, worker button, validate button, DEMO badge from `/api/mode`)
- [ ] 3.2 Port Overview view → `web/app/page.tsx`, consuming `/api/state` via the shared API client on a 5s polling loop; render Tremor KPI cards for queued/running/done/failed
- [ ] 3.3 Port Live Progress view → `web/app/progress/page.tsx`; add embedded Trace panel that subscribes to `/api/trace/stream`
- [ ] 3.4 Port Seeds view → `web/app/seeds/page.tsx`, consuming existing seeds endpoints unchanged
- [ ] 3.5 Build Challenges view as card grid → `web/app/challenges/page.tsx`. Card data source: the `challenges` array from `/api/state`. Card renders category icon (Lucide), difficulty stars, animated Framer Motion stage badge, and masked flag preview (regex masks all chars between the outermost braces → `flag{****}`). Stage value is derived by joining each `challenge.id` to entries in `progress.snapshots`; when no snapshot exists, fall back to `build_status` / `solve_status` per `dashboard-frontend` spec
- [ ] 3.6 Port Shards view → `web/app/shards/page.tsx`; preserve the legacy requeue action
- [ ] 3.7 Port Logs view → `web/app/logs/page.tsx`; tail with Tremor list primitives
- [ ] 3.8 Implement view-transition animations between sidebar nav items via Framer Motion `AnimatePresence`

## 4. Trace panel and demo polish

- [ ] 4.1 Build `<TracePanel />` under `web/components/trace/`: auto-scrolling chronological feed grouped by worker. Each row renders worker name, current stage, current status, and the latest message. The panel maintains a Set of seen `event_id`s and ignores any duplicate that arrives after a reconnect. Older entries are dimmed
- [ ] 4.2 Handle SSE disconnect by retrying within 5s; show an inline "reconnecting…" pill while disconnected, never a full-screen error
- [ ] 4.3 Render a header `<DemoBadge />` that reserves badge-sized space on first paint (before `/api/mode` resolves), then populates the literal text "DEMO" within 500ms after `GET /api/mode` returns `{"demo": true}` (per `demo-mode` spec); when `{"demo": false}`, the placeholder collapses. Use the accent color and a subtle Framer Motion pulse
- [ ] 4.4 Demo Mode: lazy-load and animate a "first paint within 1s" hero — Overview KPIs animate from 0 to current values via Framer Motion when the page mounts

## 5. Integration and cutover

- [ ] 5.1 Add `scripts/build_frontend.sh` running `cd web && npm ci` when `package-lock.json` exists, otherwise `npm install`, followed by `npm run build`, then replacing `src/web/static/dist/` with the contents of `web/out/`
- [ ] 5.1a Add `build-ui` subcommand to `src/cli.py` that shells out to `scripts/build_frontend.sh`, streaming stdout/stderr and propagating exit code. Resolve script path relative to the package root so it works regardless of cwd
- [ ] 5.1b Tests under `tests/app/test_cli.py` (or new file): `build-ui --help` includes a description; missing-script path yields a clear error. Avoid invoking the real `npm` — patch the subprocess call
- [ ] 5.2 Add `.gitignore` entries for `web/node_modules/`, `web/.next/`, `web/out/`, `src/web/static/dist/`. v1 explicitly does NOT commit `dist/` to `main`; release artifacts may bundle it later (per design D4)
- [ ] 5.3 Update `src/web/server.py` to serve files from `src/web/static/dist/`; if `dist/` is missing, emit a clear human-readable response on `GET /` naming the exact command `uv run challenge-factory build-ui`
- [ ] 5.4 Delete `src/web/static/index.html` and `src/web/static/app.js`
- [ ] 5.5 Update README with a "Frontend dev" section: `cd web && npm install && npm run dev` (port 3000), with FastAPI running separately via `uv run challenge-factory serve`
- [ ] 5.6 Update README "Quick smoke run" to include `uv run challenge-factory build-ui` before `serve` (first-run requirement; dist is not in git)
- [ ] 5.7 Add a README "Live demo" section explaining `uv run challenge-factory serve --demo`, including a note that mutating endpoints return 409 in demo mode

## 6. Validation

- [ ] 6.1 Run `pytest tests/web/ tests/hermes/test_fake.py` — all new backend tests pass
- [ ] 6.2 Run full `pytest tests/` — no existing test regresses
- [ ] 6.3 Manual: `uv run challenge-factory serve --demo` → open `http://127.0.0.1:4173/`, verify badge placeholder is reserved on first paint and the "DEMO" label appears within 500ms of `/api/mode` resolving, all six views render, Trace panel shows events with worker/stage/status/message visible, every challenge reaches `complete` within 5s
- [ ] 6.4 Manual: kill demo process, restart `serve --demo` — verify second run replays from `queued`, not stuck at `complete`
- [ ] 6.5 Manual: `cd web && npm run dev` + separate `uv run challenge-factory serve` — verify dev proxy round-trips `/api/state` correctly
- [ ] 6.5a Manual: in demo mode, click every action button in the SPA that would mutate state; verify each surfaces the "Demo mode is read-only" message (sourced from the 409 body) without corrupting on-screen state
- [ ] 6.6 Run `openspec validate frontend-rebuild --strict` — proposal, design, specs, tasks all valid
- [ ] 6.7 Bundle size check: initial JS load ≤200KB gzipped per design budget
