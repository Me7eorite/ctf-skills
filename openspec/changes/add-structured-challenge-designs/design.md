## Context

`add-design-task-planning` left every `design_tasks` row inert once it
reached `queued`. The repo already ships:

- `skills/design-challenges/SKILL.md` — a CTF challenge designer skill
  whose JSON output (the `challenges[]` object near the end of the
  skill) is the contract we want to fill.
- Category playbooks under `skills/design-challenges/references/`
  (`web-design.md`, `pwn-design.md`, `reverse-design.md`,
  `other-categories.md`, `spec-template.md`, `quality-gate.md`,
  `delivery-format.md`, etc.).
- A working Hermes execution layer in `src/hermes/` already used by
  the research workflow (`src/services/research_agent_executor.py`),
  including stdout capture, log-path persistence, and a token-fenced
  state machine on `research_runs`.
- A `design_tasks` table whose status enum already reserves
  `designing | designed | failed` for this layer.

The gap: a per-task "design attempt" record, the rendered prompt and
log files, a structured `challenge_designs` row, the validation rules
that say the JSON is acceptable, and an operator-triggered endpoint
that runs the loop once.

This change is intentionally synchronous and operator-driven. A
background design worker pool can come later; it would reuse exactly
the same attempt/design tables.

## Goals / Non-Goals

**Goals:**

- Convert one `queued` design task into one validated
  `challenge_designs` row by invoking `skills/design-challenges`
  through Hermes.
- Keep prompt assembly deterministic and replayable: same inputs ⇒
  same rendered prompt file on disk.
- Validate the returned JSON before persistence so a malformed Hermes
  output cannot pollute `challenge_designs`.
- Persist every attempt — including failures — so operators can audit
  why a task failed to design.
- Drive `design_tasks.status` through `queued → designing → designed`
  on success and `queued → designing → failed → queued (retry)` on
  failure, up to the parent request's `max_attempts`.
- Expose the validated design and attempt history in the existing
  request-detail API + dashboard, inline with the design task it came
  from.

**Non-Goals:**

- No background worker pool, no `claim_next_design_task`, no lease
  expiry / heartbeat / SKIP LOCKED queue (operator presses
  "Design now" and the request thread runs the attempt).
- No challenge file generation, no Dockerfile/source/attachment
  emission, no `work/shards/pending/*.json`.
- No multi-model fan-out (one Hermes profile per attempt, taken from
  the same `hermes_profile_bindings` table the research executor uses,
  via a new role binding).
- No spec-template-driven Markdown writeup; only the JSON schema from
  `SKILL.md` is persisted.
- No edit-after-the-fact UI for `challenge_designs`; operator either
  accepts (by acting on the design downstream) or re-runs design.

## Data Model

`design_attempts`:

| Column | Purpose |
| --- | --- |
| `id uuid pk` | Attempt identity. |
| `design_task_id uuid fk` | Parent design task. |
| `parent_attempt_id uuid nullable fk` | Previous attempt for retries. |
| `attempt int` | 1-based attempt counter within the task. |
| `status text` | `queued | running | completed | failed`. |
| `claimed_by text` | Caller identity (operator session or worker id). |
| `claim_token uuid` | Fencing token, mirrors `research_runs`. |
| `claimed_at / heartbeat_at / lease_expires_at timestamptz` | Reserved for a future worker pool; nullable here. |
| `started_at / finished_at timestamptz` | Hermes wall-clock window. |
| `profile_name_used text` | Hermes profile resolved for this attempt. |
| `prompt_path text` | Rendered prompt file on disk. |
| `hermes_log_path text` | Hermes stdout/stderr log. |
| `last_error text` | Failure reason. |
| `created_at timestamptz` | Audit. |

Constraints:

- `unique(design_task_id, attempt)`
- `attempt > 0`
- `status in ('queued','running','completed','failed')`
- index `(design_task_id, status)`

`challenge_designs`:

| Column | Purpose |
| --- | --- |
| `id uuid pk` | Design identity. |
| `design_task_id uuid fk` | Parent design task. |
| `design_attempt_id uuid fk` | Producing attempt (`completed`). |
| `payload jsonb` | The full validated `challenges[<item>]` object. |
| `summary text` | Generated short summary (≤ 280 chars) for UI. |
| `flag_format text` | Echoed from event-level payload (defaults `flag{...}`). |
| `validation_notes text` | Solver/validation outline pulled from payload. |
| `quality_gate_passed bool` | Result of running the bundled `quality-gate.md` checklist (computed by the validator). |
| `status text` | `draft | accepted | superseded` — only `draft` is written by this change. |
| `created_at / updated_at timestamptz` | Audit. |

Constraints:

- `unique(design_task_id) WHERE status = 'draft'` — at most one live
  draft per task.
- `status in ('draft','accepted','superseded')`.
- `design_attempt_id` FK with `on delete restrict` so the audit chain
  cannot drop the producing attempt.

## Prompt Wrapper

A deterministic builder assembles the Hermes prompt from these inputs,
in this order, into a single Markdown file under
`work/design/prompts/<attempt_id>.md`:

```
1. Skill header pinned at the top:
   /skill design-challenges
2. Event brief block synthesized from generation_requests:
   - topic, category, runtime_constraints, max_attempts
3. Single-challenge block from design_tasks:
   - challenge_id, title, category, difficulty, points, port,
     primary_technique, learning_objective, scenario, constraints
4. Evidence block from research_findings + research_sources:
   - one bullet per cited finding (label, kind, summary, URLs)
   - raw fetched-text excerpts intentionally NOT embedded; only URLs +
     research_findings.summary, to keep the prompt bounded.
5. Category reference selector:
   - category == "web" -> @skills/design-challenges/references/web-design.md
   - category == "pwn" -> @skills/design-challenges/references/pwn-design.md
   - category == "re"  -> @skills/design-challenges/references/reverse-design.md
   - otherwise          -> @skills/design-challenges/references/other-categories.md
   plus the always-on:
   - @skills/design-challenges/references/spec-template.md
   - @skills/design-challenges/references/quality-gate.md
   - @skills/design-challenges/references/delivery-format.md (Web/Pwn only)
6. Output contract block:
   Reproduce the SKILL.md "machine-readable output" JSON shape, with
   exactly one entry in `challenges[]`. Echo `event.flag_format`
   (default flag{...}) and require all fields the validator enforces.
```

The wrapper is a pure function `(design_task, generation_request,
findings, sources) -> str`. It does not touch the database or
Hermes. It is unit-testable.

## JSON Validation

The validator parses the Hermes stdout, finds the first JSON object,
and rejects the attempt unless:

- top-level keys: `event` (object), `challenges` (array of length 1)
- `event.flag_format` is a non-empty string (default `flag{...}` if
  missing — the validator inserts the default, it does not fail)
- the single challenge object must contain (all required, all
  non-empty unless marked):
  - `id` (must equal `design_tasks.challenge_id`)
  - `title`
  - `category` (must equal `design_tasks.category`)
  - `difficulty` (must equal `design_tasks.difficulty`)
  - `points` (positive int; matches `design_tasks.points` ± 0)
  - `deployment` (non-empty string)
  - `primary_technique`
  - `learning_objective`
  - `prompt` (the spoiler-free player prompt)
  - `artifacts` (array of strings, ≥1)
  - `flag_location`
  - `validation`
  - `hints` (array of strings, exactly 3, ordered gentle→near-solve)
- for Web/Pwn:
  - `deployment` contains `docker` (case-insensitive)
  - `port` field present and equal to `design_tasks.port`
- safety guardrails (rejects):
  - mention of any real domain (regex against
    `(?:google|github|microsoft|apple)\.com` etc.) — initial pass
    only checks a small denylist; full SAFER review is out of scope
  - any HTTP URL in `artifacts` or `validation` (artifacts must be
    relative paths)

The validator also runs the bundled `quality-gate.md` checklist (a
deterministic adapter, not Hermes again): it parses the gate items
from the file and asserts each entry the JSON object can be checked
against (presence of `hints` of length 3, `validation` non-empty,
`difficulty` in the canonical set, etc.). Result is recorded as
`quality_gate_passed`. A failed quality gate does not by itself fail
the attempt — it just records `quality_gate_passed = false` and the
operator decides; but the schema-level validation above is hard fail.

## Execution Flow

`POST /api/design-tasks/{id}/design` runs the synchronous flow in the
request thread:

1. Load task; reject if `status != 'queued'`.
2. Inside one short transaction:
   - select latest `design_attempts` row for this task; require
     `attempt < parent_request.max_attempts` OR no prior attempts.
   - insert new `design_attempts` row with
     `status='running', attempt=N, claim_token=uuid4(),
     claimed_by=<caller>, started_at=now, profile_name_used=<resolved>`.
   - update `design_tasks.status = 'designing'`.
3. Commit and drop the transaction.
4. Render the prompt to
   `work/design/prompts/<attempt_id>.md`; persist
   `design_attempts.prompt_path`.
5. Invoke Hermes (reusing the existing executor abstraction) with the
   resolved profile; capture stdout and log path.
6. Open a second short transaction:
   - on schema-valid JSON: insert `challenge_designs(status='draft',
     design_attempt_id=<attempt>, payload=<json>, summary=...,
     validation_notes=..., quality_gate_passed=...)`; mark attempt
     `completed`, set `finished_at`; mark `design_tasks.status =
     'designed'`.
   - on any failure: mark attempt `failed`, set
     `last_error`, `finished_at`; if
     `attempt < max_attempts` insert sibling
     `design_attempts(status='queued', parent_attempt_id=<this>,
     attempt=N+1)` AND set `design_tasks.status = 'queued'` so
     operator can re-trigger; otherwise set `design_tasks.status =
     'failed'`.

Each transition is gated on the (id, claim_token) pair so a stale
worker (in a future change) cannot rewrite the row. For this change
the claim is held within one request thread and re-fetched in step 6.

## Status Sync With `design_tasks`

This change implements the previously reserved transitions:

```
queued     -> designing  (on attempt insert)
designing  -> designed   (on validation success)
designing  -> failed     (terminal, max_attempts exhausted)
designing  -> queued     (transient; new retry attempt inserted)
```

`archived` and `draft` are owned by the planning layer and unchanged.

## API Shape

- `POST /api/design-tasks/{id}/design`
  - 200 → `{ "design_task_id", "attempt_id", "status",
    "challenge_design": {...} | null, "error": null | "<reason>" }`
  - 409 if task is not `queued`
  - 404 if task does not exist
- `GET /api/research/requests/{id}` (extended):
  - each `design_tasks[]` entry now includes
    `latest_design: ChallengeDesignDict | null` and
    `attempts: AttemptSummaryDict[]` (`id`, `attempt`, `status`,
    `started_at`, `finished_at`, `last_error`).

## UI Shape

Inside each Design Task row, a collapsible sub-panel:

- Header line: latest attempt status pill + "Design now" button (only
  enabled when task status is `queued`).
- Attempts list: numbered rows with start/end time, status, link to
  the Hermes log.
- Design payload: collapsible JSON tree of `latest_design.payload`
  with the validator's quality-gate badge.

No prompt text is shown inline — operator can click "View prompt" to
follow `prompt_path` (served via the existing log endpoint
mechanism, no new file route).

## Risks / Trade-offs

- **Synchronous endpoint may stall the request thread** during Hermes
  invocation. → Mitigation: enforce a per-attempt timeout (config'd,
  default 600s), and surface the running attempt via the existing UI
  poll so operators see it without page reload. A future worker pool
  removes this entirely.
- **The validator's safety denylist is intentionally shallow.** →
  Mitigation: clearly mark it as a first-line filter; full content
  review is a downstream change.
- **Prompt size grows with finding count.** → Mitigation: cap evidence
  bullets at the first N=20 findings (logged); raw text never inlined.
- **Hermes JSON parsing.** Models often wrap JSON in fences. →
  Mitigation: the parser strips ```json fences and looks for the
  first `{` … balanced `}` block.
- **Concurrent re-trigger** by two operators on the same task. →
  Mitigation: optimistic check on `design_tasks.status == 'queued'`
  inside the opening transaction; second caller gets 409.

## Migration Plan

1. Alembic revision `0004_design_attempts_and_designs` is additive.
2. Seed `agent_roles` with a new `design` row, and
   `hermes_profile_bindings(role='design', profile_name='default',
   status='enabled')`.
3. No data backfill: existing `design_tasks` rows continue working;
   `queued` rows become eligible for the new endpoint immediately.
4. Rollback = `alembic downgrade -1`; the planning layer is
   unaffected.

## Open Questions

- Should `challenge_designs.payload` be split into typed columns for
  query convenience? Decision: keep as `jsonb` for now; query patterns
  haven't emerged. Revisit if the operator UI grows filter-by-design
  fields.
- Should we add a per-task "discard design draft" endpoint that flips
  `challenge_designs.status` to `superseded`? Decision: out of scope
  here; operator can re-run design to overwrite.
