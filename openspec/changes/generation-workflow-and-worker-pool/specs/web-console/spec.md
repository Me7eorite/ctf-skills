## MODIFIED Requirements

### Requirement: Pages exist for the documented routes

The SPA SHALL implement the documented routes and pages. The `/generate/new`
route SHALL be an end-to-end generation cockpit rather than a raw seed form.
It SHALL support batch seed editing, shard plan preview, execution-mode
selection, launch monitoring, and links into Runs, Queue, Workers, and Logs.

#### Scenario: New Run cockpit exposes the complete workflow

- **WHEN** a user opens `/generate/new`
- **THEN** the page shows sections for Seed Batch, Shard Plan, Execution, and
  Launch Monitor
- **AND** the page does not require the user to navigate to another page before
  they can validate input, preview shards, choose worker execution, and launch

#### Scenario: Seed batch rows are editable and validatable

- **WHEN** a user adds Web, Pwn, and Reverse seed rows
- **THEN** each row exposes the required seed fields for its category
- **AND** Reverse rows use backend category value `re`
- **AND** Web/Pwn rows require a valid port while Reverse rows do not

#### Scenario: Shard plan is visible before launch

- **WHEN** the seed batch and shard size are valid
- **THEN** the page calls the side-effect-free run preview endpoint
- **AND** previews planned run-scoped shard filenames grouped by category
- **AND** validation errors and blocking planning errors are shown before the
  user launches

#### Scenario: Execution mode is explicit

- **WHEN** a user prepares a run
- **THEN** they can choose exactly one execution mode from `enqueue only` and
  `single worker`
- **AND** dry-run mode is visible as a first-class option
- **AND** dry-run is enabled by default for new sessions

#### Scenario: Unsupported worker pool controls are absent

- **WHEN** a user opens `/generate/new`
- **THEN** the page does not show worker-count or local-pool launch controls
- **AND** any future multi-worker affordance is clearly marked as unavailable
  rather than wired to a partial backend implementation

#### Scenario: Launch feedback remains visible

- **WHEN** the user submits the run-creation form
- **THEN** the page renders the created shards, execution mode, started workers,
  and any backend error message in the Launch Monitor
- **AND** navigation to Runs, Queue, Workers, or Logs is explicit rather than
  an immediate automatic redirect that hides the launch result

### Requirement: Design tokens enforce a semantic system

The Tailwind theme SHALL continue to define semantic color groups and shared
spacing/typography/radius rules. The generation cockpit SHALL use those tokens
to render a polished operational interface: compact, high-contrast, scannable,
and free of decorative gradients or nested card structures.

#### Scenario: Generation cockpit is operational, not decorative

- **WHEN** a reviewer inspects `/generate/new`
- **THEN** the primary workflow is visible above the fold on desktop
- **AND** controls use icons where appropriate for add, duplicate, delete,
  validate, and launch actions
- **AND** repeated seed rows have stable heights and do not shift when
  validation messages appear
- **AND** no UI section places a card inside another card

#### Scenario: Mobile layout keeps the primary action reachable

- **WHEN** `/generate/new` is rendered at a mobile viewport width
- **THEN** seed rows remain editable without horizontal page overflow
- **AND** the primary launch action remains reachable via a sticky bottom
  action bar or equivalent mobile-safe control
