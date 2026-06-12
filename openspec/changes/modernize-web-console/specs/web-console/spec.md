## ADDED Requirements

### Requirement: Frontend is a Vue 3 + Vite + TypeScript SPA

The console SHALL be built with Vue 3 (Single File Components, Composition API), Vite, and TypeScript. Styling SHALL use Tailwind CSS with a project theme defined in `tailwind.config.ts`. Component primitives SHALL follow the shadcn-vue copy-into-repo pattern, living under `frontend/src/components/ui/`.

State management SHALL use Pinia. Routing SHALL use `vue-router@4`. Server cache SHALL use `@tanstack/vue-query`. Icons SHALL use `lucide-vue-next`. Code viewers SHALL use Monaco Editor loaded lazily.

Production builds SHALL emit to `src/web/static/dist/`. `pyproject.toml` `[tool.setuptools.package-data]` SHALL include `web/static/dist/**` so the SPA ships with the Python package. The `frontend/` workspace SHALL live at the repository root, not inside `src/`, so the dependency-direction guard for Python packages does not apply to it.

#### Scenario: Production build emits SPA assets

- **WHEN** a developer runs `cd frontend && npm install && npm run build`
- **THEN** `src/web/static/dist/index.html` exists
- **AND** `src/web/static/dist/assets/` contains hashed JS and CSS bundles

#### Scenario: Backend ships SPA assets

- **WHEN** the Python package is installed via `uv pip install -e .`
- **THEN** the installed distribution includes the contents of `src/web/static/dist/` as package data

### Requirement: SPA fallback coexists with the API

`src/web/server.py` SHALL register all `/api/*` routes before any catch-all route. A single catch-all GET route SHALL serve `src/web/static/dist/index.html` for paths that do not start with `/api/` or `/static/`. Hashed assets under `/static/dist/assets/*` SHALL receive the response header `Cache-Control: public, max-age=31536000, immutable`. The served `index.html` SHALL receive `Cache-Control: no-store`.

#### Scenario: API request returns JSON not HTML

- **WHEN** a client requests `GET /api/state`
- **THEN** the response Content-Type is `application/json` and the body parses as JSON

#### Scenario: SPA path returns HTML shell

- **WHEN** a client requests `GET /generate/runs/web-0001-0001.json` (an SPA route)
- **THEN** the response Content-Type is `text/html`
- **AND** the body contains `<div id="app">`

#### Scenario: Hashed assets are immutable

- **WHEN** a client requests `GET /static/dist/assets/index-<hash>.js`
- **THEN** the response includes `Cache-Control: public, max-age=31536000, immutable`

### Requirement: Design tokens enforce a semantic system

The Tailwind theme SHALL define exactly six semantic color groups: `success`, `warning`, `danger`, `info`, `neutral`, `accent`. Each group SHALL provide tonal scales suitable for backgrounds, text, and borders. Component code SHALL reference these semantic classes rather than raw Tailwind palette names like `bg-blue-500`. An ESLint rule SHALL flag raw palette names in `frontend/src/**/*.{vue,ts}`.

Typography SHALL use Inter (sans) and JetBrains Mono (mono), self-hosted via `@fontsource`. Font sizes SHALL be limited to four steps (display / h2 / body / caption). Spacing SHALL follow an 8 px grid (`4 / 8 / 12 / 16 / 24 / 32 / 48`). Border radius SHALL use three levels: 4 px (badges), 8 px (buttons), 12 px (cards).

#### Scenario: Raw palette names rejected by ESLint

- **WHEN** a `.vue` or `.ts` source file uses `bg-blue-500` or another raw Tailwind palette class
- **THEN** `npm run lint` fails with a rule pointing at the line

#### Scenario: Required component primitives exist

- **WHEN** the build runs
- **THEN** `frontend/src/components/ui/` includes Button, Card, Badge, Skeleton, EmptyState, Toast, Dialog, Sheet, Tabs, Tooltip, DropdownMenu, Command, ProgressBar, Sparkline, GanttRow

### Requirement: Information architecture exposes seven top-level groups

The sidebar SHALL render seven top-level navigation groups in this order: **Overview**, **Generate**, **Scenario**, **Learning**, **Operate**, **Quality**, **Settings**. Generate SHALL contain `New Run` and `Runs`. Learning SHALL contain `Materials` and `Paths`. Operate SHALL contain `Queue`, `Workers`, `Logs`. Quality SHALL contain `Lint` and `Diversity`. Settings SHALL contain `LLM Provider` and `Generation Profile`.

The top bar SHALL render a workspace label fixed to `Default`, a breadcrumb derived from the current route, a Cmd-K command palette trigger, a notification bell, and a help dropdown.

#### Scenario: Sidebar lists all seven groups

- **WHEN** a user opens the SPA at `/`
- **THEN** the sidebar shows seven top-level groups in the documented order
- **AND** items reserved for future capabilities are visible rather than hidden

### Requirement: Capability model is hard-coded and returned by the API

`GET /api/capabilities` SHALL return a JSON array of exactly four entries with this shape:

```
{
  "id": "<kebab-case>",
  "name": "<display name>",
  "status": "enabled" | "coming_soon" | "disabled",
  "description": "<short copy>",
  "icon": "<lucide name>",
  "route": "<absolute SPA path>"
}
```

The four entries SHALL be: `challenge-generator` (status `enabled`), `scenario-builder` (status `coming_soon`), `learning-materials` (status `coming_soon`), `learning-paths` (status `coming_soon`).

Overview SHALL render exactly four capability tiles backed by `/api/capabilities`. Tiles for non-enabled capabilities SHALL remain clickable; clicking them SHALL route to a `PlaceholderPage` component varying only by SVG illustration and copy. No capability route SHALL respond with HTTP 404.

#### Scenario: Capabilities endpoint shape

- **WHEN** a client requests `GET /api/capabilities`
- **THEN** the response is a JSON array of length 4
- **AND** exactly one entry has `status: "enabled"` with `id: "challenge-generator"`
- **AND** three entries have `status: "coming_soon"` with `id` values `scenario-builder`, `learning-materials`, `learning-paths`

#### Scenario: Coming-soon capability route is not 404

- **WHEN** a user navigates to `/scenario`, `/learning/materials`, or `/learning/paths`
- **THEN** the SPA renders the `PlaceholderPage` component with an SVG illustration and forward-looking copy
- **AND** the page does not throw and does not return HTTP 404

### Requirement: Pages exist for the documented routes

The SPA SHALL implement these routes and pages:

- `/` — Overview: 4 KPI cards driven by `/api/kpis`, recent runs list, workers panel, 4 capability tiles.
- `/generate/new` — New Run composer: three-pane layout (saved presets on the left, category cards in the middle, live preview on the right). Submit transitions to `/generate/runs/:shard`.
- `/generate/runs` — Runs list with pagination and an EmptyState CTA when empty.
- `/generate/runs/:shard` — Run detail with six tabs: Overview, Challenges, Artifacts, Validation, Logs, Settings. Tab state SHALL be reflected in the URL query string `?tab=`.
- `/generate/runs/:shard/challenges/:id` — Challenge detail with six tabs: Brief, Source, Solve, Verify, Quality, Telemetry. Source and Solve SHALL embed Monaco Editor in read-only mode.
- `/scenario`, `/learning/materials`, `/learning/paths` — placeholder pages rendered by the same `PlaceholderPage` component.
- `/operate/queue` — kanban view with 4 columns: pending, running, done, failed.
- `/operate/workers` — workers panel with start/stop controls reusing the existing `DashboardService` backend.
- `/operate/logs` — central log explorer filtered by worker and searchable.
- `/quality/lint`, `/quality/diversity` — UI tiles rendered in disabled state until Phase 1 backends ship; they SHALL not crash and SHALL render an EmptyState explaining the dependency.
- `/settings/llm` — LLM Provider configuration form (see the LLM Provider requirement).
- `/settings/profile` — JSON editor for `generation-profiles.json` with JSON Schema validation on save.

#### Scenario: Tabs persist in URL

- **WHEN** a user clicks the `Verify` tab on `/generate/runs/web-0001-0001.json/challenges/web-0001`
- **THEN** the URL becomes `/generate/runs/web-0001-0001.json/challenges/web-0001?tab=verify`
- **AND** reloading the page restores the Verify tab as active

#### Scenario: Source and solve tabs are read-only

- **WHEN** a user opens the Source or Solve tab in a challenge detail page
- **THEN** the Monaco editor is mounted with `readOnly: true`
- **AND** keystrokes do not modify the displayed text

### Requirement: Command palette is keyboard-accessible

The SPA SHALL listen for `⌘K` (macOS) and `Ctrl+K` (other platforms) globally and open a Command palette modal. The palette SHALL include at least these entries: Overview, New Run, Runs, Queue, Workers, Logs, Settings / LLM Provider, Settings / Generation Profile.

#### Scenario: Command palette opens on hotkey

- **WHEN** a user presses `Ctrl+K` (or `Cmd+K`)
- **THEN** the Command dialog appears with a text input focused and shows at least 8 entries

### Requirement: Loading and empty states are first-class

Every data list and detail page SHALL render a Skeleton placeholder during initial load (no blank panels). Every list, table, or panel SHALL render an `EmptyState` component when the underlying data is empty; the EmptyState SHALL include an SVG illustration, a title, a description, and a primary call-to-action button.

#### Scenario: Empty runs list shows EmptyState

- **WHEN** the runs list endpoint returns an empty list
- **THEN** the `/generate/runs` page renders an EmptyState with the CTA "Start your first run" that links to `/generate/new`

#### Scenario: Loading list renders Skeleton

- **WHEN** the runs list endpoint has not yet responded after navigation
- **THEN** the page renders Skeleton rows that match the expected layout

### Requirement: LLM Provider configuration is editable from the UI

The system SHALL provide a Settings / LLM Provider page that allows operators to view, edit, and test the Hermes LLM provider configuration. The backend SHALL implement three endpoints under `/api/settings/llm`:

- `GET /api/settings/llm` returns the current configuration in the shape `{provider, base_url, model, api_key_masked}` where `api_key_masked` is `<first-3>***<last-4>` for keys longer than 8 characters and `*****` otherwise. The plain-text API key SHALL NOT appear anywhere in the response.
- `PUT /api/settings/llm` accepts `{provider, base_url, model, api_key}`. If `api_key` equals the mask placeholder returned by the previous `GET` (or is omitted), the stored key SHALL be preserved. Otherwise the new value SHALL replace the stored key. Provider, base_url, and model SHALL always be persisted as given.
- `POST /api/settings/llm/test` performs a minimum-cost call to the currently saved provider and returns `{ok, latency_ms, model, error}`. The API key SHALL NOT appear in the response, in server logs, or in any error message returned to the client.

The persistence layer SHALL write the same two files Hermes already reads: `~/.hermes/config.yaml` (provider, base_url, model under the `model:` block) and `~/.hermes/auth.json` (API keys under `credential_pool`). Unrelated keys in either file SHALL be preserved across writes.

The form SHALL render the API key input as `<input type="password">` with a show/hide toggle. The Save button SHALL be disabled when the form has no unsaved changes.

#### Scenario: Key is masked on read

- **WHEN** the stored API key is `sk-anthropic-abcdefghij`
- **AND** a client requests `GET /api/settings/llm`
- **THEN** the response contains `api_key_masked: "sk-***ghij"`
- **AND** the response does NOT contain `sk-anthropic-abcdefghij` anywhere

#### Scenario: Mask placeholder preserves stored key

- **WHEN** a client sends `PUT /api/settings/llm` with `api_key` equal to the mask placeholder
- **THEN** the stored key on disk is unchanged

#### Scenario: New key overwrites stored key

- **WHEN** a client sends `PUT /api/settings/llm` with `api_key: "sk-new-abcdef1234"`
- **THEN** the stored key on disk becomes `sk-new-abcdef1234`

#### Scenario: Unrelated fields preserved across writes

- **WHEN** the configuration file contains keys outside the LLM section
- **AND** a client saves new LLM settings
- **THEN** the unrelated keys remain in the file unchanged

#### Scenario: Test connection never echoes the key

- **WHEN** a client invokes `POST /api/settings/llm/test`
- **THEN** the response body and any server log line generated by the call contain no substring of the stored API key

### Requirement: Real-time progress uses Server-Sent Events

The backend SHALL expose `GET /api/events/stream` returning `Content-Type: text/event-stream`. The endpoint SHALL emit a `:heartbeat` comment line at least every 15 seconds while the connection is open. The endpoint SHALL emit a `data:` event for each new row appended to `progress_events` after the connection was established, including a `id:` field equal to the SQLite row id. The response SHALL include the header `X-Accel-Buffering: no` to prevent reverse-proxy buffering.

If the request includes a `Last-Event-ID` header, the server SHALL stream events with `id > Last-Event-ID` first before tailing new events.

The frontend `useEventStream` composable SHALL reconnect on `EventSource.onerror` with an exponential backoff starting at 1 s, doubling up to 4 s. On reconnect it SHALL send the highest seen event id via the native `EventSource` Last-Event-ID mechanism.

#### Scenario: Endpoint streams event-stream content type

- **WHEN** a client connects to `GET /api/events/stream`
- **THEN** the response status is 200 and Content-Type is `text/event-stream`
- **AND** the response includes header `X-Accel-Buffering: no`
- **AND** at least one `:heartbeat` line is received within 16 seconds of open

#### Scenario: Reconnect resumes from last id

- **WHEN** a client reconnects with header `Last-Event-ID: 100`
- **THEN** the server first replays events with `id > 100`
- **AND** then tails new events

#### Scenario: API key never appears in event stream

- **WHEN** the SSE stream emits events related to LLM operations
- **THEN** no event's data field contains any substring of the stored API key

### Requirement: API surface adds capabilities, runs, kpis, presets

The backend SHALL expose these new endpoints in addition to existing ones, all under `/api/`:

- `GET /api/capabilities` (see capability model requirement)
- `GET /api/kpis` returning `{total_challenges, pass_rate, avg_generation_minutes, avg_quality_score}` where `avg_quality_score` SHALL be `null` until the Phase 1 quality pipeline ships.
- `GET /api/runs` returning a paginated list of shards across `pending/running/done/failed` with summary status, start time, and per-shard pass rate.
- `GET /api/runs/{shard}` returning detailed metadata for a single shard.
- `GET /api/runs/{shard}/challenges/{id}` returning per-challenge metadata, file index, validation result, and per-stage duration.
- `GET /api/runs/{shard}/artifacts/{path}` returning the file contents under the challenge directory. The path SHALL be resolved against the shard's challenge directory and any path that resolves outside that directory (absolute paths, `..` traversal, symlink escape) SHALL return HTTP 400.
- `GET /api/presets`, `POST /api/presets`, `DELETE /api/presets/{name}` persisted to `work/presets.json` via `core.jsonio`.

Existing endpoints (`/api/state`, `/api/seeds`, `/api/seeds/enqueue`, `/api/process/start` and any other route already exposed by `DashboardService`) SHALL retain their current request and response contracts.

#### Scenario: Artifact path traversal rejected

- **WHEN** a client requests `GET /api/runs/web-0001-0001.json/artifacts/../../../etc/passwd`
- **THEN** the response status is 400 and no file outside the challenge directory is read

#### Scenario: KPI quality score is null without backend

- **WHEN** the Phase 1 quality backend is not yet implemented
- **AND** a client requests `GET /api/kpis`
- **THEN** `avg_quality_score` is `null` (not 0 or a default value)

### Requirement: Frontend lives outside the src dependency-direction guard

The `frontend/` workspace SHALL live at the repository root. It SHALL NOT import any Python module. The Python backend SHALL NOT import any TypeScript module. The two communicate exclusively over the documented HTTP API.

The existing `tests/app/test_dependency_direction.py` SHALL NOT be expanded to inspect `frontend/`. Its scope remains restricted to `src/**/*.py`.

#### Scenario: Frontend is outside the src tree

- **WHEN** the repository is checked out
- **THEN** `frontend/package.json` exists at the repository root
- **AND** no Python file under `src/` imports from `frontend/`
