## Why

The current dashboard (`src/web/static/index.html` + `app.js`, ~620 lines of
vanilla JS with Tailwind CDN) is competent as an internal control panel but
has an "internal tooling" aesthetic — flat black-and-white tables, 5-second
polling, no animation, no AI-product feel. We want to enter this project in
the company AI Innovation competition, where judges decide in 2–3 minutes
based on visual impact, "wow" interactions, and a clean demo story. The
existing surface cannot carry that story even with cosmetic polish: vanilla
JS hits a ceiling on the kind of agent-trace visualization and motion design
the competition demands.

This change rebuilds the dashboard on a modern SPA stack and adds three
demo-grade capabilities specifically chosen to win competition airtime:
live AI agent traces, a self-contained Demo Mode that doesn't require a
real Hermes run, and a challenge-card view that turns dry `metadata.json`
rows into something a judge can fall in love with at a glance.

## What Changes

- **BREAKING**: replace `src/web/static/{index.html,app.js}` with a
  Next.js 14 (App Router) + TypeScript application. The vanilla SPA is
  removed entirely — no dual-maintenance period. FastAPI continues to
  serve the bundled static Next export in production.
- Adopt **shadcn/ui + Tremor + Framer Motion** as the design system,
  with a dark base + single accent color tuned for an "AI control plane"
  look. shadcn components are copied into the repo (not vendored as a
  black box) so they can be styled in place.
- Add a **dev proxy**: Next dev server runs on port 3000 and proxies
  `/api/*` to the existing FastAPI on 4173. Prod is single-process —
  FastAPI mounts the Next build output.
- New backend endpoint `GET /api/trace/stream` (Server-Sent Events) that
  tails the existing SQLite `progress_events` table and emits one event
  per new row, carrying `event_id`, `worker`, `shard`, `stage`,
  `status`, `message`, and `ts`. The cursor starts from the current
  max event id on each connection; reconnects do not replay history.
- New backend endpoint `GET /api/mode` that returns whether the server
  is running in Demo Mode.
- New `--demo` flag on the existing `serve` CLI subcommand that enables
  a fake-Hermes runner replaying a built-in 5-second matrix end-to-end,
  with no dependency on the real `hermes` binary or Docker. In demo
  mode, all six mutating HTTP endpoints (`POST /api/actions/worker`,
  `POST /api/actions/validate`, `POST /api/seeds`,
  `DELETE /api/seeds/{challenge_id}`, `POST /api/seeds/enqueue`,
  `POST /api/shards/{state}/{name}/requeue`) return HTTP 409 with the
  body `{"ok": false, "message": "Demo mode is read-only"}`. Read
  endpoints and `/api/trace/stream` remain fully functional.
- New `challenge-factory build-ui` CLI subcommand that shells out to
  `scripts/build_frontend.sh` (the script remains the source of truth;
  the subcommand is a discoverability wrapper).
- Replace the existing challenge table view with a **card grid** that
  renders each challenge's `metadata.json` as a card with category icon,
  difficulty stars, masked flag preview, and animated stage badge.
- Keep the existing FastAPI aggregate read endpoint (`GET /api/state`)
  unchanged in shape. The new UI consumes the same JSON; only the trace
  stream, mode endpoint, and `--demo` flag are net-new backend surface.

## Capabilities

### New Capabilities

- `dashboard-frontend`: the Next.js + TypeScript SPA that replaces
  `src/web/static/`. Covers the build pipeline, FastAPI integration
  (dev proxy + prod static mount), design system (shadcn/ui + Tremor +
  Framer Motion + dark theme), the six existing views ported to React,
  and the new challenge card grid view.
- `agent-trace-stream`: the SSE channel and frontend consumer that
  surface what Hermes is currently doing per worker. Each event
  carries `event_id`, `worker`, `shard`, `stage`, `status`, `message`,
  and `ts`. Covers both the `GET /api/trace/stream` endpoint contract
  and the frontend trace panel. Events are derived by tailing the
  existing SQLite `progress_events` table, so real external workers,
  dashboard-started worker subprocesses, and the demo replayer share
  the same observable source. Forward-compat fields (e.g., file/tool/
  log) are deferred to a later change once `progress_events` grows the
  required columns.
- `demo-mode`: the `--demo` switch on `challenge-factory serve` and its
  in-process fake Hermes runner. Replays a built-in matrix through the
  same SQLite event stream and shard queue so the dashboard sees a real
  run without needing the `hermes` binary or Docker. Used for live
  competition demos and CI screenshot tests.

### Modified Capabilities

<!-- None today. A standalone `dashboard` capability was discussed but
     never written, so this change creates dashboard-frontend fresh
     instead of modifying. -->

## Impact

- **Code**: deletes `src/web/static/index.html` and `src/web/static/app.js`.
  Adds a top-level `web/` Next.js project (separate from Python `src/`) with
  `package.json`, `next.config.mjs`, `app/`, `components/`, and `lib/`. Adds
  `src/web/static/dist/` as the build output mount point. Adds
  `src/web/trace.py` for the SSE endpoint. Adds `src/hermes/fake.py` for
  the Demo Mode replayer. Adds a `--demo` flag in `src/cli.py`.
- **APIs**: two new endpoints, `GET /api/trace/stream` (SSE) and
  `GET /api/mode`. All existing read endpoints are unchanged.
- **Dependencies**: adds Node.js (≥20) as a build-time requirement; adds
  `package.json` with `next`, `react`, `typescript`, `tailwindcss`,
  `framer-motion`, `@tremor/react`, plus shadcn components copied in.
  No new Python runtime dependencies.
- **Developer workflow**: README gains a "Frontend dev" section
  documenting `npm install && npm run dev` alongside the existing `uv`
  flow. v1 does **not** commit `src/web/static/dist/` to git; first-time
  users run `uv run challenge-factory build-ui` (or
  `scripts/build_frontend.sh` directly) before `serve`. Future release
  artifacts (e.g., GitHub Releases) MAY bundle a pre-built `dist/`,
  but `main` stays source-only.
- **Tests**: backend tests added under `tests/web/` for the SSE endpoint
  and Demo Mode replayer. Frontend tests are out of scope for v1 — visual
  regression and component tests are deferred.
- **Out of scope**: authentication, multi-tenant dashboards, mobile
  layout polish beyond responsive defaults, i18n beyond the existing
  zh-CN strings, and any change to the Hermes agent contract itself.
