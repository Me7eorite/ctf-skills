## MODIFIED Requirements

### Requirement: Light, AI-product visual identity

The dashboard SHALL ship with a single light theme tuned for an "AI
control plane" aesthetic, using a pale slate-blue page surface,
white cards, slate-on-white text, and one primary accent color (sky
blue) for interactive affordances and live-data indicators. Semantic
pipeline stage colors MAY be used. The previous v1 dark theme is
explicitly removed; no theme toggle is provided.

The frozen token set is:

- Surface (page background): `#F7F9FC`
- Card / panel: `#FFFFFF`
- Border / line: `#E2E8F0`
- Primary text (`ink`): `#0F172A`
- Muted text: `#64748B`
- Primary accent: `#0EA5E9` (sky-500)
- CSS `color-scheme`: `light`

All theme colors MUST be expressed as Tailwind theme tokens (in
`web/tailwind.config.*`) and/or CSS custom properties in
`web/app/globals.css`. Components MUST NOT hard-code hex values for
these roles outside the central definitions.

#### Scenario: theme tokens are centralized

- **WHEN** a designer changes the accent color
- **THEN** the change is applied by editing a single Tailwind theme
  token (or shadcn `globals.css` CSS variable) and is reflected across
  every view without per-component overrides

#### Scenario: dark mode is gone

- **WHEN** a reviewer greps the `web/` tree for the literal string
  `color-scheme: dark` or for the previous dark surface value
  `#0A0B0F` or `#13151B` or for the previous accent `#22D3EE`
- **THEN** zero matches are found

#### Scenario: light surface renders by default

- **WHEN** the SPA loads its root layout for the first time
- **THEN** the rendered `<body>` resolves a computed background of
  `#F7F9FC` (or the closest token derived from it) and a computed
  text color matching `#0F172A`, without requiring a class toggle
  or media query

### Requirement: Six existing views ported

The SPA SHALL preserve every view present in the legacy dashboard —
Overview, Live Progress, Seeds, Challenges, Shards, and Logs — and SHALL
consume the existing `GET /api/state` aggregate read endpoint for
summary, challenge, seed, shard, log, validation, process, and progress
data. No backend read endpoint contract is altered as part of this
requirement.

The SPA's header SHALL also expose the three legacy operator controls
that drive the existing `POST /api/actions/*` endpoints: a "刷新"
(refresh) control that forces an immediate re-fetch of `/api/state`
across mounted views, a "启动 Worker" control that issues
`POST /api/actions/worker`, and a "重新验证" control that issues
`POST /api/actions/validate`. All three MUST route through the shared
API client and MUST surface the demo-mode 409 read-only response as a
user-visible inline message (not a silent failure).

#### Scenario: parity with legacy dashboard

- **WHEN** a user navigates to each of the six views in the new SPA
- **THEN** every datum that was visible in the legacy dashboard is also
  visible (possibly re-styled or re-grouped), and no view depends on a
  backend endpoint that did not exist before this change

#### Scenario: header controls are present

- **WHEN** the SPA's root layout is rendered
- **THEN** the header contains visible interactive elements labeled
  "刷新", "启动 Worker", and "重新验证" (or the documented English
  equivalents), each accessible via keyboard and pointer

#### Scenario: action button surfaces demo-mode refusal

- **WHEN** the user clicks "启动 Worker" or "重新验证" while the
  server is in demo mode and the request returns
  `409 {"ok": false, "message": "Demo mode is read-only"}`
- **THEN** the message text "Demo mode is read-only" is rendered
  visibly to the user (toast, banner, or inline area), and the view
  state is not corrupted by an unhandled error
