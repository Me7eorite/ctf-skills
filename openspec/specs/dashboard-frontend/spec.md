# dashboard-frontend Specification

## Purpose
TBD - synced from change frontend-rebuild. Update Purpose after archive.
## Requirements
### Requirement: SPA technology stack

The dashboard SHALL be implemented as a Next.js 14 (App Router) application
written in TypeScript, using shadcn/ui components, Tremor for chart and KPI
primitives, Framer Motion for view-transition and stage-progression
animations, and Tailwind CSS for styling. The vanilla `index.html` and
`app.js` under `src/web/static/` MUST be removed in the same change that
introduces the SPA — no dual-maintenance period is permitted.

#### Scenario: stack components are present

- **WHEN** a developer inspects the new `web/` project's `package.json`
- **THEN** `next`, `react`, `react-dom`, `typescript`, `tailwindcss`,
  `framer-motion`, and `@tremor/react` are listed as dependencies, and
  shadcn components live under `web/components/ui/` as in-tree source
  files (not opaque vendored modules)

#### Scenario: legacy SPA is removed

- **WHEN** the change is applied
- **THEN** `src/web/static/index.html` and `src/web/static/app.js` no
  longer exist in the repository

### Requirement: Dark, AI-product visual identity

The dashboard SHALL ship with a single dark theme tuned for an "AI control
plane" aesthetic, using a near-black surface (`#0A0B0F` to `#13151B`),
neutral text scales, and one primary accent color (cyan, chosen in
design.md) for interactive affordances and live-data indicators.
Semantic stage colors MAY be used for pipeline status. Light theme is
explicitly NOT a v1 requirement.

#### Scenario: theme tokens are centralized

- **WHEN** a designer changes the accent color
- **THEN** the change is applied by editing a single Tailwind theme
  token (or shadcn `globals.css` CSS variable) and is reflected across
  every view without per-component overrides

### Requirement: FastAPI co-existence

The Next.js application SHALL coexist with the existing FastAPI server in
two distinct modes:

- **Dev mode**: `npm run dev` starts Next on port 3000. Next's config
  proxies all requests matching `/api/*` to the FastAPI server on port
  4173. The FastAPI server is started separately by
  `uv run challenge-factory serve`.
- **Prod mode**: `npm run build` emits a static export under `web/out/`;
  `scripts/build_frontend.sh` copies it to `src/web/static/dist/`.
  FastAPI serves that directory at `/` with an HTML fallback, replacing
  the current static files under `src/web/static/`. The user-facing
  command remains
  `uv run challenge-factory serve`.

#### Scenario: dev proxy round-trip

- **WHEN** the dev server is running on port 3000 and FastAPI is running
  on port 4173, and a developer's browser issues `GET /api/state`
- **THEN** the request is proxied to `http://127.0.0.1:4173/api/state`
  and the JSON response is returned unchanged

#### Scenario: prod single-process serve

- **WHEN** a user runs `uv run challenge-factory serve` against a repo
  that contains a built `src/web/static/dist/`
- **THEN** opening `http://127.0.0.1:4173/` serves the Next build's
  `index.html` and all assets under `/`, and the SPA is fully
  functional without a Node.js process running

#### Scenario: missing build artifact

- **WHEN** a user runs `serve` without `src/web/static/dist/` present
- **THEN** the server starts but responds to `GET /` with a clear
  human-readable message naming the exact command
  `uv run challenge-factory build-ui`, rather than a generic 404

### Requirement: Six existing views ported

The SPA SHALL preserve every view present in the legacy dashboard —
Overview, Live Progress, Seeds, Challenges, Shards, and Logs — and SHALL
consume the existing `GET /api/state` aggregate read endpoint for
summary, challenge, seed, shard, log, validation, process, and progress
data. No backend read endpoint contract is altered as part of this
requirement.

#### Scenario: parity with legacy dashboard

- **WHEN** a user navigates to each of the six views in the new SPA
- **THEN** every datum that was visible in the legacy dashboard is also
  visible (possibly re-styled or re-grouped), and no view depends on a
  backend endpoint that did not exist before this change

### Requirement: Challenge card grid view

The Challenges view SHALL render each challenge as a card (not a table
row) showing at minimum: category icon, challenge title, difficulty as a
star rating, current pipeline stage as an animated badge, and a masked
flag preview (e.g., `flag{****}` revealing only the brace structure).
Cards SHALL animate stage transitions using Framer Motion. The card's
base metadata SHALL come from the `challenges` array returned by
`GET /api/state`; the current pipeline stage SHALL be derived by joining
that challenge id to `progress.snapshots` entries when present, falling
back to build/solve status when no progress snapshot exists.

#### Scenario: card renders metadata

- **WHEN** a challenge's `metadata.json` is loaded into the Challenges
  view through the `GET /api/state` response
- **THEN** the rendered card displays the category icon for that
  challenge's category, a star count derived from the metadata's
  difficulty field, and a flag preview that masks all characters
  between the outermost braces

#### Scenario: stage transition is animated

- **WHEN** a challenge's stage changes from one value to another via a
  state snapshot refresh
- **THEN** the badge on its card animates the transition rather than
  swapping content instantly

### Requirement: Polling and live updates

The SPA SHALL refresh non-trace data by polling `GET /api/state` on a
5-second interval by default, matching the legacy behavior. Live agent
traces use a separate SSE channel covered by the `agent-trace-stream`
capability and MUST NOT be conflated with this polling loop.

#### Scenario: poll interval default

- **WHEN** the SPA loads against an idle FastAPI server
- **THEN** `GET /api/state` requests are issued at 5-second intervals
  (±500ms)

### Requirement: API client centralization

All HTTP calls to the FastAPI backend SHALL go through a single client
module under `web/lib/api/`. Components MUST NOT call `fetch` directly
against `/api/*` paths. This enables uniform error handling, future
auth token injection, and Demo Mode interception.

#### Scenario: components use the client

- **WHEN** a reviewer greps the `web/app/` and `web/components/` trees
  for the literal string `fetch("/api`
- **THEN** zero matches are found
