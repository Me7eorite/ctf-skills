## Context

The current dashboard lives in `src/web/static/{index.html,app.js}` —
~620 lines of vanilla JavaScript with Tailwind via CDN and Lucide icons.
It is served by FastAPI's static catch-all in
[src/web/server.py](../../../src/web/server.py) and consumes JSON read
endpoints declared in the same file. The UI works but reads as a
generic internal tool: flat tables, no motion, no agent visibility
beyond log strings, and a 5-second polling cadence that makes the
agent look idle when it isn't.

The competition deadline forcing this redesign is the company AI
Innovation contest. Live judging is 2–3 minutes per team; the demo
window is therefore tighter than the implementation window. Three
constraints follow:

1. The demo cannot depend on a real Hermes run completing during
   judging — Hermes shards take minutes and require Docker. There
   must be a self-contained replay path.
2. Visual impact in the first 5 seconds matters more than feature
   breadth. The architecture must make "wow" affordances cheap to
   add later, not just possible.
3. Backend stability is non-negotiable; the change must not regress
   the existing `serve`, `run`, `validate` flows that
   `pack-delivery-bundle` and `runner-resume-and-metrics` depend on.

The user has self-reported no React or Vue experience, which biases
toward the framework with the strongest AI-assisted code generation
support — Next.js + React.

## Goals / Non-Goals

**Goals:**

- Replace the vanilla SPA with a Next.js 14 App Router + TypeScript
  project under a top-level `web/` directory, separate from Python `src/`.
- Adopt shadcn/ui + Tremor + Framer Motion as the design system,
  vendoring shadcn components in-tree so they remain editable.
- Preserve every existing FastAPI read endpoint's shape; add two new
  endpoints (`GET /api/trace/stream` and `GET /api/mode`) and one new
  CLI flag (`serve --demo`).
- Ship a 5-second self-contained Demo Mode replay backed by the same
  state plane the real runner uses, so the SPA has no demo-mode
  branching.
- Keep prod deployment a single command (`uv run challenge-factory
  serve`) — Node must not be a runtime dependency.

**Non-Goals:**

- Authentication, multi-user dashboards, RBAC.
- Light theme, mobile-specific layouts beyond responsive defaults,
  full i18n.
- Replacing the polling read model with WebSocket-pushed snapshots.
  SSE is added for traces only.
- Frontend unit / visual-regression tests in v1.
- Storybook, design tokens packaging, or any framework-agnostic
  abstraction layer over shadcn components.

## Decisions

### D1. Next.js 14 App Router over Vue / SvelteKit / vanilla++

Considered:

- **Next.js 14 App Router + shadcn/ui** (chosen).
- Vue 3 + Naive UI / Arco. Rejected: user has no Vue experience, and
  AI-assisted codegen quality is materially lower than for React in
  current models. The Chinese-ecosystem advantage doesn't compensate.
- SvelteKit + skeleton. Rejected: smaller ecosystem; fewer shadcn-grade
  components; less judge familiarity.
- Vanilla JS + Web Components + Tailwind upgrade. Rejected: hits a
  ceiling on the agent-trace timeline visualization. Acceptable as a
  fallback if the deadline collapses (see Migration Plan).

App Router specifically (over Pages Router) because the dashboard is
overwhelmingly client-interactive; every page will start with
`"use client"` and we get the modern file-based routing without
inheriting legacy `getServerSideProps` patterns.

### D2. Vendor shadcn components in-tree (`web/components/ui/`)

shadcn is explicitly designed as a "copy in" library, not an npm
dependency. We follow that pattern. Rationale: the user is learning;
having component source visible and editable is more useful than
hiding it behind a black-box import. It also avoids upgrade churn
during the competition window.

### D3. Dev: separate processes; Prod: FastAPI serves built artifact

- **Dev**: `npm run dev` on `:3000`, FastAPI on `:4173`,
  `next.config.ts` rewrites `/api/:path*` → `http://127.0.0.1:4173/api/:path*`.
  No CORS configuration needed.
- **Prod**: `npm run build` produces a static export under `web/out/`;
  `scripts/build_frontend.sh` copies that export to
  `src/web/static/dist/`. FastAPI serves that directory with an
  HTML fallback. The Node toolchain is not required at runtime.

Considered and rejected: running Next as a Node server in production.
That would force Node into the production dependency footprint, which
breaks the "single `uv run` command" contract.

Considered and rejected: deferring to `next start` with a reverse proxy.
Same Node-runtime concern, plus added ops complexity.

### D4. Build artifact distribution: build script only on `main`

Decision **locked for v1**: `src/web/static/dist/` is `.gitignore`d on
`main`. First-time users run `uv run challenge-factory build-ui`
(documented in README) or `scripts/build_frontend.sh` directly before
`serve`. Future GitHub Releases MAY ship a pre-built `dist/` as a
release asset, but the source tree on `main` is never polluted with
build output.

Considered and rejected: committing `dist/` to git. Pro: zero-Node
clone-to-run. Con: noisy diffs, merge conflicts, and repo bloat that
would compound through the competition window. The release-asset path
preserves the zero-Node prod story for end users without taxing
contributors.

Mitigation for the "missing dist" footgun: `serve` detects the missing
directory at startup and responds to `GET /` with a clear message
naming the exact command (`uv run challenge-factory build-ui`).

### D5. SSE for traces; keep polling for state

Considered:

- **SSE for traces, keep 5s polling for `GET /api/state`** (chosen).
- Full WebSocket pushing all updates. Rejected for v1: doubles the
  scope of the backend change for marginal visual gain. The polling
  cadence is fine for tabular data; SSE is only justified by the
  high-frequency, low-latency trace requirement.
- Polling traces at 1Hz. Rejected: too many requests, and the visual
  feel of a streaming connection is part of the "wow" budget — judges
  see the network panel.

### D6. Demo Mode: in-process fake runner, real state plane

Demo Mode shares the SQLite event store and the shard queue with the
real runner. The "fake Hermes" is a small class under
`src/hermes/fake.py` that, on `serve --demo`, spawns a daemon thread
which:

1. Resets demo-tagged shards (any shard whose worker name starts with
   `demo-`) and re-splits the built-in matrix into `pending/`. Writes
   one `metadata.json` per demo challenge with the fields enumerated
   in the `demo-mode` spec so the Challenges card grid has realistic
   data.
2. Walks each shard through `STAGES` with deterministic per-stage
   delays summing to ≤5 seconds, calling `StateStore.record(...)`
   exactly as the real runner does.

The trace stream is fed by tailing the existing SQLite
`progress_events` table from the FastAPI process, using monotonically
increasing event ids as the cursor. This deliberately avoids an
in-process observer hook on `StateStore.record(...)`: real Hermes
workers may run as independent CLI processes, so an observer registered
inside the web server would miss their events. SQLite remains the
cross-process synchronization point for both real and demo runs.

Consequences of choosing SQLite tailing for v1:

- Only fields persisted in `progress_events` can be emitted:
  `event_id`, `worker`, `shard`, `stage`, `status`, `message`, `ts`
  (derived from `created_at` → unix epoch). The optional `file`,
  `tool`, and `log` fields originally sketched in the spec are
  **deferred** — they require either a schema migration plus new
  `progress` CLI flags, or an in-process enrichment layer that
  breaks the cross-process symmetry we just bought. Not in v1.
- Each SSE client gets its own polling tail with its own cursor.
  On connect (and reconnect) the cursor starts at the current max
  event id, so the server never replays history. Client-side
  React state owns rendered history; dedupe on `event_id`.
- Polling cadence is implementation-tunable but capped at "feels
  live": ~250ms when there's recent activity, backing off to ~1s
  during idle. This is the same order of magnitude as the demo
  replayer's per-stage delays.

Considered and rejected: a recorded JSON replay file shipped as a
fixture. Pro: deterministic. Con: it bypasses the state plane,
forcing demo-mode branches in the read model — exactly the coupling
we're trying to avoid.

Considered and rejected: standing up a real Hermes mock binary on
`$PATH` for demo mode. Far too much surface for the value.

### D7. Visual identity: dark base + cyan accent (locked)

Color palette finalized in design (not in spec to keep the spec
testable rather than aesthetic). Cyan is locked for v1; no further
A/B against violet/emerald.

- Surface: `#0A0B0F` page, `#13151B` cards, `#1B1E26` borders.
- Text: `#E6E8EE` primary, `#8B92A1` muted.
- Accent: `#22D3EE` (cyan-400) for interactive affordances and live
  indicators.
- Stage colors: amber → blue → violet → cyan along the pipeline.

Cyan over violet over emerald: cyan reads as "data" and "live" in
current AI-product visual language (think Cursor, Linear), and it
contrasts cleanly against the warm amber-to-violet pipeline colors
without color-blind issues.

### D8. Demo Mode is read-only at the HTTP boundary

Implemented as a single FastAPI dependency (`require_writable`)
attached to each mutating route, not as middleware. The dependency
MUST abort the request with a response whose body is exactly
`{"ok": false, "message": "Demo mode is read-only"}`; a plain
`HTTPException(detail={...})` is not acceptable because FastAPI wraps
that payload under `{"detail": ...}`. Rationale: a dependency keeps the
decision per-route and explicit in the route declaration, while
middleware would force the demo check on every request — including
reads and the SSE stream, which we want untouched.

Contract:

- Status: `409 Conflict` (chosen over 403). Aligns with the existing
  shard requeue path's "current state does not permit this action"
  status convention. The SPA already treats 409 as a state-mismatch
  error, so the demo message threads through existing toast/banner
  code without new handling.
- Body: exactly `{"ok": false, "message": "Demo mode is read-only"}`.
- Scope is the six endpoints enumerated in the `demo-mode` spec. Any
  future mutating endpoint MUST opt in by attaching the dependency at
  registration time — defaulting to "demo-safe" prevents writes from
  silently sneaking through.

Considered and rejected: a global middleware that allowlists read
verbs (`GET`/`HEAD`). Verb-based filtering would mis-classify any
future `POST` that is genuinely read-only (e.g., a search endpoint),
forcing exceptions. The dependency approach is explicit, greppable,
and survives refactors.

Considered and rejected: 403 Forbidden. Defensible (it's a policy
denial), but stylistically inconsistent with the rest of the API
which uses 409 for "wrong state". Consistency wins.

### D9. `build-ui` subcommand is a thin wrapper, not a reimplementation

`challenge-factory build-ui` shells out to `scripts/build_frontend.sh`.
The script remains the single source of truth for the actual build
steps (`npm ci` when a lockfile exists, otherwise `npm install`;
`npm run build`; copy `web/out/` → `src/web/static/dist/`).

Rationale: CI, contributors, and Makefile-style integrations all
already expect a shell script. Reimplementing the build in Python
would duplicate logic and create a second place to keep in sync. The
subcommand exists for **discoverability**: a user who has only ever
seen `uv run challenge-factory <verb>` finds the build step from
`--help` instead of having to spelunk `scripts/`.

Behavior:

- Resolves the script path relative to the package root.
- Streams the script's stdout/stderr to the user's terminal.
- Exits with the script's exit code unchanged.
- Errors clearly if `npm` is not on `$PATH` — the script already
  handles this; the subcommand just propagates it.

## Risks / Trade-offs

- **[Risk] User has no React experience; week 1 will be slow.**
  → Mitigation: scaffold via `create-next-app` + `shadcn init`,
  then start from the official shadcn dashboard example
  (`ui.shadcn.com/examples/dashboard`) and adapt. Avoid Server
  Components — every page is a client component.

- **[Risk] Demo Mode and real runner diverge over time, breaking
  demos.** → Mitigation: Demo Mode writes through the same
  `StateStore` API the real runner uses. A backend test runs the
  demo replay to completion and asserts `/api/state` shape parity.

- **[Risk] Build artifact is missing on a fresh clone, and `serve`
  looks broken.** → Mitigation: the "missing dist" error message must
  include the exact build command, `uv run challenge-factory build-ui`.
  D9 makes that command part of v1 rather than a follow-up.

- **[Risk] SSE connections held open block FastAPI workers under
  uvicorn's default `--workers=1`.** → Mitigation: the SSE endpoint
  uses `async def` and `StreamingResponse` so it shares the event
  loop. Document that demo and trace are I/O-bound async paths.

- **[Risk] shadcn + Tremor + Framer Motion bundle size grows past
  the "static export" budget; first-paint feels slow.** →
  Mitigation: lazy-load Tremor charts and Framer panels via
  `next/dynamic`; budget budget the initial bundle at ≤200KB
  gzipped at v1 and treat regressions as bugs.

- **[Trade-off] Removing the vanilla SPA in the same change blocks
  rollback to the legacy UI.** Accepted: the legacy UI is preserved
  in git history, and dual-maintenance during a competition crunch
  is worse than a hard cutover.

## Migration Plan

1. **Branch**: develop under a feature branch; do not merge to
   `main` until backend tests pass and the demo replay reaches
   `complete` for all built-in challenges.
2. **Backend first**: land the SSE endpoint and `--demo` flag in
   a precursor commit, with tests, against the legacy vanilla SPA
   (which simply ignores them). This shrinks the visual-cutover
   commit's blast radius.
3. **Frontend swap**: in a second commit, add the `web/` Next
   project, wire `scripts/build_frontend.sh` to copy the static export
   into `src/web/static/dist/`, switch `server.py`'s static catch-all,
   delete the legacy files.
4. **Build script**: add `scripts/build_frontend.sh` and update
   the README with a "Frontend dev" section.
5. **Rollback strategy**: if the SPA cutover destabilizes the
   demo, revert the second commit. The backend changes from step
   2 stay — they're additive and harmless against the legacy UI.

## Open Questions

<!-- All four original open questions have been resolved and folded
     into D4 (no dist commit on main), D7 (cyan locked), D8 (demo
     read-only via 409 dependency), and D9 (build-ui as a script
     wrapper). No active open questions remain for v1. -->

_None._
