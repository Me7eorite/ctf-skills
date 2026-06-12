## 1. Backend foundation (commit 1)

- [x] 1.1 Add `pyyaml` to `[project.dependencies]` in `pyproject.toml`; run `uv sync` and confirm import.
- [x] 1.2 Create `src/domain/llm_settings.py` with `load_settings`, `save_settings`, `test_connection`. Mask API keys as `<first-3>***<last-4>` for keys longer than 8 characters, `*****` otherwise. Persist to `~/.hermes/config.yaml` and `~/.hermes/auth.json`. Preserve unrelated keys across writes.
- [x] 1.3 Refactor `hermes/runner.py:_apply_legacy_custom_provider` to call `domain.llm_settings.load_settings` instead of hand-rolling YAML parsing; verify existing legacy custom provider behavior unchanged.
- [x] 1.4 Create `src/web/api/` package skeleton with `__init__.py`.
- [x] 1.5 Add `src/web/api/capabilities.py` exposing `GET /api/capabilities` returning the four-entry constant list.
- [x] 1.6 Add `src/web/api/kpis.py` exposing `GET /api/kpis` with `avg_quality_score: null` until Phase 1.
- [x] 1.7 Add `src/web/api/llm.py` exposing `GET/PUT /api/settings/llm` and `POST /api/settings/llm/test`. Plain-text key MUST NOT appear in any response or log.
- [x] 1.8 Add `src/web/api/presets.py` with CRUD endpoints persisting to `work/presets.json` via `core.jsonio`.
- [x] 1.9 Add `src/web/api/runs.py` exposing list, detail, per-challenge detail, and artifact endpoints. Artifact endpoint rejects path traversal with HTTP 400.
- [x] 1.10 Add `src/web/sse.py` exposing `GET /api/events/stream`: `text/event-stream` body, `:heartbeat` every 15 s, replay events when `Last-Event-ID` header present, set `X-Accel-Buffering: no`.
- [x] 1.11 Wire new modules into `src/web/server.py` while keeping existing `/api/state`, `/api/seeds`, `/api/seeds/enqueue`, `/api/process/start` contracts unchanged. Do not yet add the SPA fallback.
- [x] 1.12 Add `tests/app/test_llm_settings.py` with: mask convention, save preserves unrelated keys, mask placeholder preserves stored key, new key overwrites, plain-text key never in JSON serializations, `test_connection` mocked.
- [x] 1.13 Add `tests/app/test_capabilities_api.py` asserting exactly 4 entries, status distribution, and required field shape.
- [x] 1.14 Add `tests/app/test_runs_api.py` covering list, detail, and path-traversal rejection on the artifacts route.
- [x] 1.15 Add `tests/app/test_sse.py` asserting content-type, `X-Accel-Buffering: no`, heartbeat appears within 16 s, replay from `Last-Event-ID`.
- [x] 1.16 Add `tests/app/test_presets_api.py` for the presets CRUD round trip.
- [x] 1.17 Run `uv run pytest tests/` and `uv run ruff check`; both green.

## 2. Frontend scaffolding (commit 2)

- [x] 2.1 Add `frontend/` workspace: `package.json`, `vite.config.ts`, `tsconfig.json`, `tailwind.config.ts`, `postcss.config.js`, `index.html`, `src/main.ts`, `src/App.vue`, `.eslintrc.cjs`, `.prettierrc`. Pin Node 20 LTS via `.nvmrc` at repo root.
- [x] 2.2 Install runtime deps: `vue@3`, `vue-router@4`, `pinia`, `@tanstack/vue-query`, `@vueuse/core`, `lucide-vue-next`, `tailwindcss`, `monaco-editor`, `@fontsource/inter`, `@fontsource/jetbrains-mono`.
- [x] 2.3 Install dev deps: `vite@5`, `typescript@5`, `@vitejs/plugin-vue`, `vue-tsc`, `eslint`, `eslint-plugin-vue`, `@typescript-eslint/*`, `prettier`, `vitest`, `@vue/test-utils`, `jsdom`.
- [x] 2.4 Define design tokens in `tailwind.config.ts`: six semantic color groups (success/warning/danger/info/neutral/accent), four font sizes, 8 px spacing scale, three radii.
- [x] 2.5 Add ESLint rule rejecting raw Tailwind palette names (`bg-blue-500`, etc.) in `frontend/src/**/*.{vue,ts}`.
- [x] 2.6 Copy shadcn-vue primitives into `frontend/src/components/ui/`: Button, Card, Badge, Skeleton, EmptyState, Toast, Dialog, Sheet, Tabs, Tooltip, DropdownMenu, Command, ProgressBar, Sparkline, GanttRow.
- [x] 2.7 Implement `useEventStream` composable with exponential-backoff reconnect (1 s / 2 s / 4 s) and `Last-Event-ID` resumption.
- [x] 2.8 Implement `useApi` composable using `@tanstack/vue-query`.
- [x] 2.9 Create Pinia stores: `useUIStore` (nav state, command palette open), `useRunsStore`, `useWorkersStore`, `useSettingsStore`.
- [x] 2.10 Define `vue-router` routes for all paths listed in the spec; lazy-load each page component.
- [x] 2.11 Implement `App.vue` shell: sidebar + top bar layout + `<RouterView />`; Cmd-K listener that opens Command dialog with at least 8 navigation entries.
- [x] 2.12 Wire build output to `src/web/static/dist/`; verify `npm run build` produces hashed assets and an `index.html`.
- [x] 2.13 Add Vitest config + 6 component/composable unit tests: `useEventStream` reconnect backoff, Command palette filtering, Capability tile rendering, LLM Settings form dirty detection, Skeleton fallback, EmptyState rendering.
- [x] 2.14 Add `Makefile` at repo root with `ui-dev`, `ui-build`, `ui-test` targets. Add `cd frontend && npm install` documentation to README.

## 3. Pages and SPA serving (commit 3)

- [x] 3.1 Implement `OverviewPage.vue`: 4 KPI cards backed by `/api/kpis`, recent runs list, workers panel, 4 capability tiles backed by `/api/capabilities`.
- [x] 3.2 Implement `NewRunPage.vue`: three-pane layout (saved presets / category cards / live preview). Submit posts to existing `/api/seeds/enqueue` or new run endpoint and transitions to run detail.
- [x] 3.3 Implement `RunsListPage.vue`: paginated list with EmptyState CTA.
- [x] 3.4 Implement `RunDetailPage.vue` with six tabs (Overview / Challenges / Artifacts / Validation / Logs / Settings). Tab state synced to `?tab=` query string. Overview tab includes Gantt timeline component.
- [x] 3.5 Implement `ChallengeDetailPage.vue` with six tabs (Brief / Source / Solve / Verify / Quality / Telemetry). Source and Solve embed Monaco lazily in read-only mode.
- [x] 3.6 Implement `OperateQueuePage.vue` (kanban 4 columns).
- [x] 3.7 Implement `OperateWorkersPage.vue` (start/stop controls reusing `DashboardService`).
- [x] 3.8 Implement `OperateLogsPage.vue` (filtered + searchable log explorer).
- [x] 3.9 Implement `SettingsLLMPage.vue`: form with provider dropdown, base_url, api_key (`type=password` + show/hide toggle), model, Save (disabled when not dirty), Test connection button.
- [x] 3.10 Implement `SettingsProfilePage.vue`: Monaco JSON editor for `generation-profiles.json` with JSON Schema validation on save.
- [x] 3.11 Add SPA fallback to `src/web/server.py`: register all `/api/*` first, then catch-all returning `dist/index.html`; set `Cache-Control: public, max-age=31536000, immutable` on `/static/dist/assets/*`, `Cache-Control: no-store` on the served `index.html`.
- [x] 3.12 Update `pyproject.toml` `[tool.setuptools.package-data]` to include `web/static/dist/**`.
- [x] 3.13 Delete `src/web/static/index.html` and `src/web/static/app.js`.
- [x] 3.14 Add `tests/app/test_spa_fallback.py` asserting `/api/state` returns JSON and `/anything/else` returns SPA HTML with `<div id="app">`.
- [x] 3.15 Smoke verify: `cd frontend && npm install && npm run build && cd .. && uv run challenge-factory serve`; open `http://127.0.0.1:4173/` and confirm Overview renders.

## 4. Placeholders, polish, docs (commit 4)

- [x] 4.1 Create 4 SVG illustrations in `frontend/src/assets/empty-states/`: runs-empty, workers-empty, logs-empty, coming-soon.
- [x] 4.2 Implement `PlaceholderPage.vue` used by `/scenario`, `/learning/materials`, `/learning/paths`, `/quality/lint`, `/quality/diversity` with capability-specific illustration + roadmap copy.
- [x] 4.3 Ensure every list/page renders Skeleton during initial load and EmptyState when data is empty; audit each route.
- [x] 4.4 Wire 100 ms opacity fade-in and 200 ms slide-in transitions through a shared `useTransition` composable; apply to route changes and Sheet open.
- [x] 4.5 Wire SSE-driven toasts: dashboard events with status `failed` trigger danger toasts; events with status `passed` for `complete` stage trigger success toasts.
- [x] 4.6 Update `README.md`: add a "Frontend development" section with `nvm use`, `cd frontend && npm install`, `npm run dev`, `npm run build` instructions; note that `dist/` is committed.
- [x] 4.7 Update `docs/architecture.md`: add a "Frontend stack" diagram and a short note on the SPA fallback contract.
- [x] 4.8 Add bundle-size measurement step to CI (or as a Make target): log gzipped initial JS bundle size; warn above 800 KB.
- [x] 4.9 Commit the built `src/web/static/dist/` contents so `uv sync` consumers can serve without Node.
- [x] 4.10 Update `.gitignore` if needed: keep `frontend/node_modules/` ignored; ensure `src/web/static/dist/` is NOT ignored.

## 5. Verification

- [x] 5.1 `uv run ruff check` clean.
- [x] 5.2 `uv run pytest tests/` green including all new backend tests.
- [x] 5.3 `cd frontend && npm run typecheck` clean.
- [x] 5.4 `cd frontend && npm run lint` clean (including the no-raw-palette rule).
- [x] 5.5 `cd frontend && npm run test` green (≥ 6 unit tests).
- [x] 5.6 `cd frontend && npm run build` succeeds; initial JS bundle gzipped logged.
- [x] 5.7 `uv run challenge-factory serve` then browse `http://127.0.0.1:4173/` and verify: Overview renders all sections; `/scenario`, `/learning/materials`, `/learning/paths`, `/quality/lint`, `/quality/diversity` render PlaceholderPage (not 404); Cmd-K opens with at least 8 entries; `/settings/llm` loads masked config and saves a change; `POST /api/settings/llm/test` returns a structured result.
- [x] 5.8 `curl -N http://127.0.0.1:4173/api/events/stream` shows `Content-Type: text/event-stream` and a `:heartbeat` line within 16 seconds.
- [x] 5.9 `openspec validate modernize-web-console --strict` passes.
