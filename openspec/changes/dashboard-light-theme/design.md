## Context

`frontend-rebuild` shipped the dashboard SPA with a dark theme and
cyan accent (its D7 decision, marked "locked for v1"). It also
shipped an `<DemoBadge />`-only header — the three legacy operator
controls (Refresh / 启动 Worker / 重新验证) were dropped during
implementation, in violation of the `dashboard-frontend` capability's
"Six existing views ported" parity requirement.

Two distinct problems:

1. **Theme**: the operator reports the surface is too dim for stage
   lighting and prefers sky blue over cyan. This requires overriding
   D7. The D7 lock was always "for v1" — explicit revocation is
   expected, not anomalous.
2. **Parity bug**: the missing header buttons are a regression
   against an existing requirement. No spec change needed for the
   fix itself, but tightening the spec's parity scenario prevents
   recurrence.

The competition deadline window is narrow, so this change is a
visual + small-functional patch — not a redesign.

## Goals / Non-Goals

**Goals:**

- Replace the dark base + cyan accent with a light base + sky-500
  accent. Centralize all six colors in Tailwind tokens; no
  hard-coded hexes in components.
- Restore the three header buttons and wire them through the
  existing shared API client, honoring the demo-mode 409 contract.
- Re-build and re-deploy the static export so `serve` picks up the
  new theme.

**Non-Goals:**

- No theme toggle. Light replaces dark; switching is not a v1.1
  feature.
- No new endpoints. Action buttons reuse existing `POST /api/actions/*`.
- No frontend visual-regression tests (still out of scope per
  `frontend-rebuild` Non-Goals).
- No dark-mode parity in CSS — dark tokens are removed, not
  preserved behind a media query.

## Decisions

### D1. Light replaces dark; no toggle

A toggle doubles the styling surface (every shadcn component needs
both `bg-card` and `dark:bg-card`-equivalents) and adds a state to
test against demo-mode visual checks. The operator's complaint is
about a specific deployment context (stage lighting), not about
personal preference for dark vs. light. Light wins outright for v1.1.
A future change can reintroduce a toggle if needed.

### D2. Accent: `#0EA5E9` (sky-500), not sky-400 or sky-600

Considered:

- `#0EA5E9` sky-500 (chosen). Saturated, AA-contrasts white text on
  it, reads cleanly as the primary CTA color.
- `#38BDF8` sky-400. Truer to "sky blue" but loses contrast against
  the `#F7F9FC` page background — interactive affordances start
  feeling like decorations.
- `#0284C7` sky-600. Reads "corporate" / dependable but pulls away
  from the "sky" connotation the operator asked for.

### D3. Surface: `#F7F9FC`, not pure white

Pure white pages plus white cards collapse visually — the card edges
need a surface contrast to read as elevated. `#F7F9FC` is a single
~3% tint toward slate-100 that preserves an unmistakable "light"
feel while letting `#FFFFFF` cards float above it. This is the
standard shadcn light pattern.

### D4. Stage colors retained but re-tuned for light contrast

The pipeline stage colors (`amber → blue → violet → sky`) are kept
because removing them would force per-stage badge re-design — out of
scope. They're re-tuned (e.g., amber-600 instead of amber-400) so
they still read as "warm to cool" against the light surface.
Implementation may use Tailwind's built-in `amber-600` / `violet-600`
/ `sky-600` scales without adding tokens.

### D5. Header buttons reuse `apiPost`; demo-mode message surfaced via inline toast

The three header buttons all `POST` to existing endpoints. The
shared API client gains (if it doesn't already have one) an
`apiPost(path, body?)` that returns `{ok: boolean, message?: string}`
parsed from the response, regardless of HTTP status. The 409
demo-mode response and 4xx state-error responses thread through the
same code path: the call resolves with `ok: false` and a `message`,
and the button's caller renders that message.

Considered and rejected: per-button error handling. That fragments
error UX — there's no reason demo-mode messaging in "启动 Worker"
should look different from demo-mode messaging in "重新验证".

### D6. Refresh button: `RefreshContext` + tick counter (no SWR, no reload)

The Refresh control SHALL force all mounted views to immediately
re-fetch `/api/state` without reloading the page. SWR was the
reference pattern, but the v1 polling is a hand-rolled
`useEffect + setInterval(5000)` per page; introducing SWR mid-stream
would balloon the diff for marginal benefit.

Chosen implementation:

- A `RefreshContext` exposes `{ tick: number, bump: () => void }`.
  `tick` is integer-incrementing state at the layout level.
- Each page (`Overview`, `Progress`, `Challenges`, `Shards`, `Logs`,
  `Seeds`) reads `tick` from context and adds it to its existing
  effect's dependency array. The interval timer stays — the tick
  just forces a re-fetch outside the cadence.
- `<RefreshButton />` calls `bump()` on click. KPI cards / cards /
  list rows re-render with fresh data within one fetch round-trip.

Considered and rejected: installing SWR. Tempting because
`mutate()` is the idiomatic answer, but the migration cost crosses
every page and the visible behavior is identical to the tick
approach.

Considered and rejected: `window.location.reload()`. Drops scroll
positions, dismisses inline state (expanded cards, trace history
already rendered). Jarring during a live demo.

Considered and rejected: a `BroadcastChannel`/`EventTarget` global
event. Same effect as Context but harder to test and reason about.

### D7. shadcn component theme: edit in place vs. re-init

shadcn was added with `init`'s dark defaults. Two options:

- **(A) Re-init in light mode** (`npx shadcn init` again). Risk:
  blows away local edits to `button.tsx` / `card.tsx` / `badge.tsx`.
- **(B) Hand-edit the three component files** to swap dark-mode
  utility classes for light equivalents. Tedious but surgical.

**Chosen: (B)** — only three component files exist; hand-editing is
faster and preserves whatever local tuning has accumulated. If a
later change adds more shadcn components, that change can re-init
with light defaults at the time.

## Risks / Trade-offs

- **[Risk] Sky accent is too close to one of the pipeline stage
  colors (sky), so the "live" indicator and a "validate" stage badge
  look identical.** → Mitigation: the validate-stage badge is bumped
  to `sky-700` (deeper) while the accent stays `sky-500`. Verified
  visually in the rehearsal pass.

- **[Risk] Demo-mode buttons that no-op on click look broken.** →
  Mitigation: D5's toast pattern always shows feedback, including
  the "Demo mode is read-only" message. No silent failures.

- **[Risk] Re-building the static export and forgetting to commit
  doesn't matter (D4 of `frontend-rebuild` says dist isn't in git),
  but a stale dist on a teammate's machine could confuse them.** →
  Mitigation: README already documents the `build-ui` step; the
  cutover in this change re-runs it once and the rest is operator
  discipline.

- **[Trade-off] Removing dark mode entirely blocks anyone who
  preferred it.** Accepted: the spec change is explicit; an
  override is one more change away if anyone asks.

## Migration Plan

1. Edit `web/tailwind.config.*` and `web/app/globals.css` to the new
   palette. Edit the three shadcn components by hand for light
   utility classes. Edit `layout.tsx` to add the three header
   buttons and adjust `<DemoBadge />` styling.
2. Add `apiPost` to `web/lib/api/client.ts` if it doesn't already
   exist; otherwise reuse.
3. Run `uv run challenge-factory build-ui` to produce a fresh
   `src/web/static/dist/`.
4. Run `uv run challenge-factory serve --demo` and verify:
   - Light theme renders by default.
   - Three header buttons are present.
   - Clicking "启动 Worker" or "重新验证" surfaces the
     "Demo mode is read-only" message.
   - Clicking Refresh forces an immediate snapshot re-fetch.
5. Repeat against `uv run challenge-factory serve` (no `--demo`) to
   confirm the buttons hit the real endpoints. Use the existing
   `--dry-run` path documented in project.md if needed.
6. No rollback path beyond reverting the commit — the dark theme
   tokens are removed, not toggled.

## Open Questions

_None._
