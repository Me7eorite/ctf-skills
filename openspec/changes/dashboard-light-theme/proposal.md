## Why

The `frontend-rebuild` change locked a dark-base + cyan-accent visual
identity (its design.md §D7, "locked for v1"). After implementation, the
operator ran the dashboard against real demo traffic and reported two
concrete problems:

1. The dark surface is too dim for the conditions the deck will be
   judged in (overhead projector + bright stage lighting). Dark UIs
   wash out badly under those conditions.
2. They want a different brand tone — sky blue rather than cyan — as
   the primary accent. The two are similar but read differently:
   sky-500 feels more like a corporate "intelligence platform" and
   pairs with the slate-on-white shadcn light theme judges instantly
   recognize from modern SaaS dashboards.

In parallel, an implementation regression has been observed: the
rebuilt `layout.tsx` shipped with only the title and a `<DemoBadge />`
in the header — the **refresh button, "启动 Worker" button, and
"重新验证" button were dropped**. That violates the
`dashboard-frontend` capability's "Six existing views ported" parity
requirement: every datum and control visible in the legacy dashboard
must remain available. This is a bug, not a spec change; this proposal
addresses it alongside the theme override since both touch the same
layout file and tailwind tokens.

## What Changes

- **BREAKING (visual)**: replace the dark theme with a light theme.
  - Surface: `#F7F9FC` (pale slate-blue)
  - Card: `#FFFFFF`
  - Border / line: `#E2E8F0` (slate-200)
  - Primary text: `#0F172A` (slate-900)
  - Muted text: `#64748B` (slate-500)
  - Accent: `#0EA5E9` (sky-500), replacing the previous `#22D3EE`
    cyan-400
  - `color-scheme: light` replaces `color-scheme: dark` in
    `web/app/globals.css`
- The accent is the single primary color; semantic pipeline stage
  colors (amber → blue → violet → sky) are retained but re-tuned for
  contrast against the light surface.
- Restore the three header buttons missing from `layout.tsx`:
  - **Refresh** — triggers a `mutate()` on the polling client so all
    open views re-fetch immediately
  - **启动 Worker** — `POST /api/actions/worker` via the shared API
    client
  - **重新验证** — `POST /api/actions/validate` via the shared API
    client
  All three respect the existing demo-mode 409 contract — when the
  server returns `{"ok": false, "message": "Demo mode is read-only"}`,
  the buttons surface that message in an inline toast/banner.
- DEMO badge styling adapts to the new accent (sky-500 outline +
  background tint, replacing the dark-mode glow).

## Capabilities

### New Capabilities

<!-- None. This change is a re-skin + parity restoration, not new
     surface area. -->

### Modified Capabilities

- `dashboard-frontend`: the "Dark, AI-product visual identity"
  requirement is replaced with a "Light, AI-product visual identity"
  requirement covering the new palette and accent. The "Six existing
  views ported" requirement is unchanged in text, but its parity
  scenario now formally calls out the header-button regression
  ("启动 Worker / 重新验证 / 刷新" must be present and wired to the
  documented endpoints) so the bug cannot recur.

## Impact

- **Code**:
  - `web/app/globals.css` — `color-scheme` and base colors swapped to
    light.
  - `web/tailwind.config.*` — 6 token values swapped (`surface`,
    `card`, `line`, `ink`, `muted`, `accent`).
  - `web/app/layout.tsx` — adds Refresh / 启动 Worker / 重新验证
    buttons to the header; adjusts `<DemoBadge />` for light contrast.
  - `web/lib/api/client.ts` — gains `apiPost(path, body?)` if not
    already present, for the action buttons.
  - `web/components/ui/*` — shadcn components may need re-init under
    light theme (or hand-edit the dark Tailwind classes used in
    `button.tsx`, `card.tsx`, `badge.tsx`).
- **APIs**: no change. The action buttons hit existing endpoints
  (`POST /api/actions/worker`, `POST /api/actions/validate`).
- **Tests**: a minimal frontend assertion is added — a static check
  (grep-based or rendered-HTML check) that `web/app/layout.tsx`
  references the three button labels. Full visual regression remains
  out of scope per `frontend-rebuild` Non-Goals.
- **Build artifact**: `src/web/static/dist/` must be re-built after
  this change (`uv run challenge-factory build-ui`); the README's
  "first run" instructions already cover this.
- **Out of scope**: any change to the dark color palette as a
  selectable option (no theme toggle in v1.1 — light replaces dark).
  i18n, RBAC, and mobile-specific layouts remain out of scope as
  before.
