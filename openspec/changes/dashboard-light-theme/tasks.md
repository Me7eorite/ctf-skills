## 1. Theme token swap

- [x] 1.1 Edit `web/app/globals.css`: replace `color-scheme: dark` with `color-scheme: light`; replace body `background: #0a0b0f` and `color: #e6e8ee` with `background: #F7F9FC` and `color: #0F172A`
- [x] 1.2 Edit `web/tailwind.config.{js,mjs,ts}` theme tokens to: `surface: "#F7F9FC"`, `card: "#FFFFFF"`, `line: "#E2E8F0"`, `ink: "#0F172A"`, `muted: "#64748B"`, `accent: "#0EA5E9"`
- [x] 1.3 Grep `web/` for the now-removed dark hex values (`#0A0B0F`, `#13151B`, `#1B1E26`, `#E6E8EE`, `#8B92A1`, `#22D3EE`) and replace any stray occurrences with the corresponding token reference (e.g., `bg-surface`, `text-ink`, `border-line`, `text-accent`). No dark hex MAY remain after this task

## 2. shadcn component re-tuning

- [x] 2.1 Edit `web/components/ui/button.tsx`: replace dark-mode utility classes (any `bg-card`, `border-line`, `text-ink` combos sized for a dark surface) so the default `Button` variant reads as a sky-accent primary on white; ensure focus ring uses `ring-accent/40`
- [x] 2.2 Edit `web/components/ui/card.tsx`: card defaults to `bg-card border border-line` on the light surface; verify the elevation reads cleanly against `#F7F9FC`
- [x] 2.3 Edit `web/components/ui/badge.tsx`: tune outline and filled variants for the new accent; the DEMO badge variant uses `border-accent/60 text-accent bg-accent/10`

## 3. Header parity restoration

- [x] 3.1 Add `apiPost<T>(path: string, body?: unknown): Promise<{ok: boolean; message?: string; data?: T}>` to `web/lib/api/client.ts` as a NEW function. Do NOT modify the existing `apiSend` — it is used by `web/app/shards/page.tsx` and throws on non-2xx by contract. Implementation of `apiPost`: `fetch` with `method: "POST"`, content-type JSON when `body` is provided, parse the response body as JSON, and return `{ok: response.ok && payload.ok !== false, message: payload.message ?? payload.detail, data: payload}`. Never throw on 4xx/5xx — the caller decides what to do with `ok: false`
- [x] 3.2 In `web/app/layout.tsx`, replace the header's lone `<DemoBadge />` block with: `<RefreshButton />` + `<WorkerButton />` + `<ValidateButton />` + `<DemoBadge />`, in that order, right-aligned
- [x] 3.3 Implement `<WorkerButton />` (new component under `web/components/header/`): label "启动 Worker", icon `Play` from lucide, on click calls `apiPost("/api/actions/worker")`. On `ok: true`, call `showToast(payload.message ?? "已启动 Worker")` and `bump()` to immediately re-fetch state. On `ok: false`, call `showToast(payload.message ?? "请求失败")`
- [x] 3.4 Implement `<ValidateButton />`: label "重新验证", icon `ShieldCheck`, calls `apiPost("/api/actions/validate")`. Same `{ok, showToast, bump}` handling as `<WorkerButton />`
- [x] 3.5 Implement a `RefreshContext` in `web/lib/refresh.tsx` exporting `RefreshProvider` (holds `tick` state and `bump()` callback) and `useRefreshTick(): number` hook. Wrap the layout `<body>`'s root div in `<RefreshProvider>` so every page sees the same tick
- [x] 3.5a Implement `<RefreshButton />`: label "刷新" (or icon-only with `aria-label="刷新"`), icon `RefreshCw`. On click, calls `bump()` from `RefreshContext`. Brief 250ms spin animation on the icon via Tailwind `animate-spin` toggled by a `useState` for visual feedback
- [x] 3.5b Plumb `useRefreshTick()` into every page's polling effect dep array: edit `web/app/page.tsx`, `web/app/progress/page.tsx`, `web/app/challenges/page.tsx`, `web/app/shards/page.tsx`, `web/app/logs/page.tsx`, `web/app/seeds/page.tsx`. Each `useEffect` whose body fetches `/api/state` (or any `/api/*` polling target) gains `tick` in its dep array, so a `bump()` triggers an immediate re-fetch outside the 5s cadence. Do NOT remove the existing `setInterval(load, 5000)` — both paths coexist
- [x] 3.6 Add a minimal `<Toast />` host to `layout.tsx`. Simplest acceptable: `useState<string|null>` queue at layout level, fixed-position div bottom-right rendering the current message, auto-dismisses after 4s via `setTimeout`. Expose a `showToast(msg)` via context (`ToastContext`, sibling to `RefreshContext`) so `<WorkerButton />` and `<ValidateButton />` can call it from their click handlers
- [x] 3.7 Restyle `<DemoBadge />` for the light theme: `border border-accent/60 bg-accent/10 text-accent` instead of the previous dark-mode `border-accent/50 shadow-glow`

## 4. Pipeline stage color re-tuning

- [x] 4.1 Audit every stage badge usage in `web/app/challenges/page.tsx`, `web/app/progress/page.tsx`, and `web/components/trace/TracePanel.tsx`. Switch from amber-400 / blue-400 / violet-400 / sky-400 (designed for dark) to amber-600 / blue-600 / violet-600 / sky-700 (designed for light). Result: only one location used a dark-tuned class — TracePanel's "reconnecting" indicator (`border-amber-400 text-amber-300`) was bumped to `border-amber-500 bg-amber-50 text-amber-700`. Stage→color mapping is not in v1 implementation; existing badges use the neutral `border-line` / `text-ink` from the shadcn Badge default, which read fine against the light surface
- [x] 4.2 Verify the `validate` stage badge uses `sky-700` (not `sky-500`) so it does NOT collide visually with the `accent` color used by interactive affordances (per design D7 contrast risk). Result: stage→color mapping is not yet wired in v1, so no collision exists today. When a future change introduces per-stage colors, the validate stage MUST use sky-700 per this requirement

## 5. Build, deploy, validate

- [x] 5.1 Run `uv run challenge-factory build-ui` to produce a fresh `src/web/static/dist/`
- [ ] 5.2 Run `uv run challenge-factory serve --demo`; open `http://127.0.0.1:4173/`; verify: (a) surface is `#F7F9FC` / cards are white; (b) accent visible on all three header buttons + DEMO badge + active sidebar item; (c) no element renders with a dark hex computed style
- [ ] 5.3 In demo mode, click "启动 Worker" and "重新验证" — verify each shows a visible "Demo mode is read-only" message via the toast host; the page state is otherwise unchanged
- [ ] 5.4 Click "刷新" — verify the Overview KPIs visibly re-fetch (network panel shows a `/api/state` request inside 100ms, outside the 5s polling interval), without a full page reload; scroll position and any expanded card stays intact
- [ ] 5.5 Run `uv run challenge-factory serve` (no `--demo`); start a worker via the legacy CLI or via the "启动 Worker" header button; verify the buttons hit the real endpoints and DO NOT return the demo-mode 409
- [x] 5.6 Run a grep test: `rg -n '#(0A0B0F|13151B|1B1E26|E6E8EE|8B92A1|22D3EE)' web/ src/web/` — expect zero matches (dark palette fully removed). Confirmed: zero matches in `web/app`, `web/components`, `web/lib`, `web/tailwind.config.ts`, `src/web/server.py`, `src/web/dashboard.py`
- [x] 5.7 Run `openspec validate dashboard-light-theme --strict` — proposal, design, specs, tasks all valid
- [x] 5.8 Run full `uv run pytest tests/` — no existing test regresses (this change is frontend-only; backend tests should be unaffected). 194 passed in 5.99s
