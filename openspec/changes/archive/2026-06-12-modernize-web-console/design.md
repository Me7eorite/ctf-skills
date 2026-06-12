## Context

The console today is one HTML file and one JS file served by FastAPI. The frontend has no build step, no component model, and no real-time channel. The only navigation primitive is six sibling sections switched via `data-view` classes. The Hermes runtime expects two on-disk files for LLM configuration (`~/.hermes/config.yaml` and `auth.json`) and the operator must edit them with a text editor; the existing legacy custom provider mapping (in `hermes/runner.py`) is the only code path that reads these files today.

The platform roadmap calls for four core capabilities (challenge generation, scenario generation, learning materials, learning paths). Only the first is implemented. The console must be able to display all four meaningfully — generation as a fully functional surface, the other three as forward-looking placeholders — without the IA changing again when they ship.

Phase 1 of the larger plan (`add-stack-templates` → `add-quality-lint` → `add-validation-autofix` → `add-quality-metrics` → `add-ci-gates`) is happening on a parallel track. The console must reserve slots (Quality / Lint, Quality / Diversity) that turn on without further routing work when those backends land.

## Goals / Non-Goals

**Goals:**

- Replace the vanilla-JS dashboard with a Vue 3 + Vite + TypeScript SPA that looks and feels like a commercial AI product.
- Expose "capability" as a first-class IA model so the four planned core capabilities have permanent navigation entries today.
- Surface Hermes LLM provider configuration in the UI with masked credentials and a working test-connection round trip.
- Replace 5-second polling with a server-pushed event stream that has predictable reconnect behavior.
- Keep the existing FastAPI/Python pipeline untouched; only extend the API surface.

**Non-Goals:**

- No authentication, registration, password reset, OAuth, or session model. This is a local trusted-operator tool.
- No multi-workspace, multi-tenant, RBAC, invitations, audit logs.
- No billing, quota, token accounting, subscription plans.
- No marketing/landing page or external product page.
- No dark mode toggle (dark tokens reserved but not exposed).
- No internationalization layer (Chinese-first, English technical terms kept verbatim).
- No mobile responsive perfection (must not break below `lg`, that is the entire requirement).
- No implementation of Scenario Builder, Learning Materials, or Learning Paths business logic. Placeholder pages only.
- No source/solve/writeup editing. Monaco viewers are read-only.
- No replacement of `generation-profiles.json` or matrix file formats.
- No change to `STAGES` enum, CLI command names, SQLite schema, or shard queue format.
- No quality lint / diversity scoring backend; the corresponding UI tiles render in disabled state and read empty payloads until Phase 1 ships those endpoints.

## Decisions

### D1 Vue 3 + Vite + TypeScript + Tailwind + shadcn-vue

**Decision**: Build the SPA on Vue 3 SFC + Vite + TypeScript. Style with Tailwind CSS. Use shadcn-vue's copy-into-repo component pattern instead of a packaged component library.

**Alternatives considered**:

- *React + shadcn/ui + Vite*: same ergonomics, larger React ecosystem. Rejected only because Vue 3 SFC syntax is the more approachable surface for the contributors expected to work on this repo (mainly Python/security folks), and the chance of Hermes-assisted page authoring leans Vue-shaped due to recent training corpora; both are acceptable, this is a coin-flip closed by team preference.
- *Svelte + SvelteKit*: smaller community, riskier for long-lived components.
- *Alpine.js + Tailwind*: insufficient for tabbed details, Monaco embedding, command palette, and SSE-driven state. Rejected.
- *Next.js full stack*: replaces FastAPI; out of scope.

**Tooling additions**: `pinia` for state, `vue-router@4` for routing, `@tanstack/vue-query` for server cache, `@vueuse/core` for composables, `lucide-vue-next` for icons, `monaco-editor` for code viewing, `vitest` for unit tests, `@vue/test-utils` for component tests.

### D2 Repository layout: `frontend/` at repo root, build output to `src/web/static/dist/`

**Decision**: The new frontend workspace lives at `frontend/` next to `src/`, not inside `src/`. Production builds emit to `src/web/static/dist/` which is shipped via `package-data`.

**Alternatives considered**:

- *Put frontend under `src/web/frontend/`*: violates the spirit of the `module-architecture` capability (src/ is Python packages). Rejected.
- *Separate repo*: makes local dev painful, splits issue tracking. Rejected.

**Implication**: `frontend/` is outside the `src/` dependency-direction matrix, so the existing `test_dependency_direction.py` does not need to grow rules for it. The frontend cannot import Python modules; the backend cannot import TypeScript modules. They communicate only through the HTTP API.

**Build artifact policy**: commit the contents of `src/web/static/dist/` to git so that operators who only run `uv sync` can serve the SPA without Node installed. `frontend/node_modules/` is gitignored. CI rebuilds `dist/` and fails if it diverges from what was committed.

### D3 SPA fallback in FastAPI

**Decision**: `src/web/server.py` registers all `/api/*` routes first, then a single catch-all route for `GET {path:path}` that serves `dist/index.html`. Static assets under `/static/dist/assets/*` get a 1-year `Cache-Control: public, max-age=31536000, immutable` header (Vite hashes filenames).

The `index.html` itself must be served with `Cache-Control: no-store` so the SPA shell always picks up the latest asset hashes after a deploy.

### D4 Capability model is hard-coded, not data-driven

**Decision**: `GET /api/capabilities` returns a constant Python list with four entries. The list shape is:

```python
[
    {"id": "challenge-generator", "status": "enabled", ...},
    {"id": "scenario-builder", "status": "coming_soon", ...},
    {"id": "learning-materials", "status": "coming_soon", ...},
    {"id": "learning-paths", "status": "coming_soon", ...},
]
```

**Alternatives considered**:

- *Read from a YAML/JSON file*: invites configuration drift between code and content. The capability list is part of the product surface, not operator configuration. Rejected.
- *Generate from registered feature flags*: speculative; we have no feature flag system. Rejected.

**Future extension**: when Phase 2 ships scenario-builder, flip its `status` to `enabled` and add a real route handler. The IA does not move.

### D5 LLM provider configuration uses the existing Hermes file layout

**Decision**: `domain/llm_settings.py` reads and writes the same two files Hermes already consumes:

- `~/.hermes/config.yaml` — provider / base_url / model under the `model:` block.
- `~/.hermes/auth.json` — API keys under the `credential_pool` map.

It does **not** introduce a third configuration file. The schema mirrors what `hermes/runner.py:_apply_legacy_custom_provider` already parses, which means the runtime keeps working without further changes.

**Mask convention**: API keys are returned via `GET /api/settings/llm` as `<first-3>***<last-4>` (e.g. `sk-***wXyZ`). Keys shorter than 8 characters mask to a fixed `*****`. The mask string is also accepted as the input on `PUT` to signal "leave the stored key untouched"; this lets operators edit base_url or model without retyping the secret. The same convention is used everywhere the key would be visible (logs, errors, SSE messages); the spec encodes this as a contract.

**Test connection**: a thin Python helper that performs the smallest possible HTTP call for the configured provider — for OpenAI-compatible providers, a `GET /v1/models`; for Anthropic, a `POST /v1/messages` with `max_tokens=1`. Timeout 10 seconds. Returns `{ok, latency_ms, model, error}` without ever echoing the API key into the response.

**Alternatives considered**:

- *Store credentials in env vars only*: poor UX for rotation; doesn't match what Hermes already uses. Rejected.
- *Introduce a new `~/.cf/settings.toml`*: forces double-source-of-truth with Hermes. Rejected.

### D6 Server-Sent Events instead of WebSocket

**Decision**: Use SSE (`text/event-stream`) for real-time progress push.

**Alternatives considered**:

- *WebSocket*: bidirectional, but we only need server → client. WebSocket adds protocol complexity, harder to debug with `curl`, no built-in auto-reconnect in browsers. Rejected.
- *Long polling*: no improvement over current 5s polling. Rejected.

**Server implementation**: a background asyncio task per connection polls `progress_events` table every 1 second using `last_event_id` boundary. Heartbeat `:heartbeat` every 15 seconds. Connection set is held in memory; on app shutdown all connections are closed cleanly.

**Reverse proxy hint**: response sets `X-Accel-Buffering: no` so nginx does not buffer the event stream. README documents `proxy_buffering off` for any deployment behind nginx.

**Client implementation**: `useEventStream()` composable using the native `EventSource`; on `onerror`, reconnect with exponential backoff (1s/2s/4s, capped at 4s). Events are dispatched into pinia stores. The Last-Event-ID header carries the highest event id seen so reconnects do not lose events.

### D7 Information architecture: 7 top-level groups

**Decision**: Sidebar groups (in order): Overview / Generate / Scenario / Learning / Operate / Quality / Settings. Within Generate: New Run + Runs. Within Learning: Materials + Paths. Within Operate: Queue + Workers + Logs. Within Quality: Lint + Diversity. Within Settings: LLM Provider + Generation Profile.

**Rationale**: the top groups separate the four product narratives (capabilities, automation, quality, configuration) from each other so users orient by intent rather than feature list. Coming-soon items stay visible to communicate roadmap.

**Coming-soon presentation**: rendered with a small grey `coming soon` badge; routes resolve to a single `PlaceholderPage` component that varies only by SVG illustration and copy. Clicking does not 404.

### D8 Monaco editor is loaded on demand, not in the initial bundle

**Decision**: Use `vite-plugin-monaco-editor` (or dynamic import) so Monaco's worker JS and language packs do not enter the initial bundle. Only the Brief tab (Markdown) and Source/Solve tabs (Python, Dockerfile, YAML, Markdown) need Monaco; they import it lazily on tab activation.

**Bundle budget**: initial JS bundle ≤ 800 KB gzipped. CI logs the bundle size and warns above the budget; does not fail the build (a single fail-loud warning is enough; tuning chunk-splitting is an ongoing concern).

### D9 Design tokens enforced through Tailwind theme only

**Decision**: all color/spacing/typography/radius values flow through `tailwind.config.ts` theme keys. We do not allow raw Tailwind color classes like `bg-blue-500` in components; only semantic classes like `bg-info-500` are permitted. An ESLint rule enforces this with a `no-restricted-syntax` pattern matching forbidden literals.

**Rationale**: when later phases introduce a dark mode, semantic tokens swap by class root (`dark:`) cleanly. Allowing both styles now means rewriting later.

### D10 Run/Challenge detail page is route-driven, master/detail not modal

**Decision**: Clicking a challenge in a run takes the user to `/generate/runs/:shard/challenges/:id`. The page is a full route, not a slide-in dialog. Tab state lives in the URL query string (`?tab=verify`), so deep links to the Verify tab are sharable.

**Side panels (`Sheet`)**: reserved for ephemeral, single-action flows like "Start worker" or "Edit preset". Not used for primary navigation.

### D11 Persistence for New Run presets is JSON, not SQLite

**Decision**: presets are stored in `work/presets.json` as a single JSON document with a `presets: [{name, payload, created_at}]` list. `domain/presets.py` reads/writes atomically using `core.jsonio`.

**Rationale**: presets are user-curated artifacts, not telemetry. They sit alongside `generation-profiles.json` in the same shape, share the same backup strategy, and do not warrant a schema migration when fields evolve.

## Risks / Trade-offs

- **Bundle size**: shadcn-vue + Monaco + chart helpers can balloon. Mitigation: code-split Monaco, lazy-load Quality/Settings routes, measure in CI, alert above 800 KB gzipped initial.
- **Node toolchain entry barrier**: adds Node.js to a Python repo. Mitigation: commit `dist/` to git, document `.nvmrc`, ship a Makefile so contributors do not have to learn npm.
- **API key leakage**: any code path that returns the key by accident is a security regression. Mitigation: spec encodes "API key never appears in JSON responses, logs, SSE messages"; `tests/app/test_llm_settings.py` asserts mask everywhere; pre-commit grep guards against the literal substring of any stored key in committed files (config.yaml lives outside the repo so this is belt-and-suspenders).
- **SSE through nginx**: streams may stall behind buffering proxies. Mitigation: `X-Accel-Buffering: no` header + documentation.
- **Placeholder pages read as broken**: users may think Scenario / Materials / Paths are bugs. Mitigation: SVG illustration + clear roadmap copy + "Phase 2 后续" timeline note.
- **shadcn-vue maturity**: younger ecosystem than shadcn/ui (React). Mitigation: copy components into repo so we can patch in place; if a specific component is unworkable, swap to PrimeVue's same-name component and log in CHANGELOG.
- **Removing the old dashboard breaks external scripts**: low risk because the old dashboard was browser-only, but we keep `/api/state`, `/api/seeds`, `/api/seeds/enqueue`, `/api/process/start` REST contracts as-is to avoid surprising tooling that polls them.
- **SPA catch-all eating API routes**: trivial bug if registered in the wrong order. Mitigation: `tests/app/test_spa_fallback.py` asserts `/api/state` returns JSON and `/anything/else` returns the SPA HTML.
- **Hermes runtime still parses YAML by hand**: legacy custom-provider parser in `hermes/runner.py` will keep reading these files. Adding `pyyaml` to dependencies and routing both writers through it eliminates the duplicate parser. Mitigation: replace the hand-rolled parser in `_apply_legacy_custom_provider` with a `domain/llm_settings.load_settings` call as part of this change to keep one parser path.

## Migration Plan

Apply in four commits to keep diffs reviewable:

1. **Backend foundation** — `domain/llm_settings.py`, `web/sse.py`, `web/api/{capabilities,kpis,llm,presets,runs}.py`, `pyproject.toml` adds `pyyaml`, new pytest modules. Old dashboard still works because SPA fallback is not yet wired.
2. **Frontend scaffolding** — `frontend/` workspace, `tailwind.config.ts`, design tokens, base shadcn-vue components, `vue-router` skeleton, pinia stores, `useEventStream`. Build target `src/web/static/dist/` exists but `index.html` is still legacy.
3. **Pages: Overview, Generate (New Run, Runs, Run detail, Challenge detail), Operate (Queue, Workers, Logs), Settings (LLM Provider, Generation Profile)**. Wire SPA fallback in `web/server.py`. Remove `src/web/static/index.html` and `src/web/static/app.js`.
4. **Placeholders, polish, docs** — Scenario, Materials, Paths placeholder pages with SVG illustrations; Quality placeholder; empty states; transitions; README + architecture doc + Makefile + `.nvmrc`; CI bundle size check.

**Rollback**: prior to merging, `git revert` restores the legacy dashboard. After merging, rolling back step (3) alone is the largest plausible rollback target; steps (1)–(2) are additive and not destructive.

**No data migration is required**. `~/.hermes/config.yaml` and `auth.json` are read and written by the new code in the same format Hermes already consumes.

## Open Questions

- *Which provider should be the default in the LLM dropdown?* Currently leaning Anthropic but operator preference may differ. Resolve during apply by reading whatever `~/.hermes/config.yaml` already contains as the initial select.
- *Should the bundle size budget fail CI or only warn?* Starting with warn-only; revisit after the first three release cycles.
- *Will the SPA need workspace switching in Phase 2?* If yes, the top-bar workspace label needs to become a dropdown. Reserve the visual space now; the underlying API/router does not need to change.
