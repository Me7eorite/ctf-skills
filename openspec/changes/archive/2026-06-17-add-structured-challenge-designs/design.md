## Context

`add-design-task-planning` left every `design_tasks` row inert once it
reached `queued`. The repo already ships:

- `skills/design-challenges/SKILL.md` - a CTF challenge designer skill
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
background design worker pool can come later; it would reuse the same
attempt/design tables after adding real claim leasing behavior.

## Goals / Non-Goals

**Goals:**

- Convert one `queued` design task into one validated
  `challenge_designs` row by invoking `skills/design-challenges`
  through Hermes.
- Keep prompt assembly deterministic and replayable: same inputs =>
  same rendered prompt file on disk.
- Validate the returned JSON before persistence so a malformed Hermes
  output cannot pollute `challenge_designs`.
- Persist every attempt - including failures - so operators can audit
  why a task failed to design.
- Drive `design_tasks.status` through `queued -> designing -> designed`
  on success and `queued -> designing -> queued|failed` on failure,
  up to the parent request's `max_attempts`.
- Expose the validated design and attempt history in the existing
  request-detail API + dashboard, inline with the design task it came
  from.

**Non-Goals:**

- No background worker pool, no `claim_next_design_task`, no lease
  expiry / heartbeat / SKIP LOCKED queue (operator presses
  "Design now" and the request thread runs the attempt). This change
  uses a request-local `claim_token` only as a fencing token for the
  two short transactions around a synchronous Hermes call.
- No challenge file generation, no Dockerfile/source/attachment
  emission, no `work/shards/pending/*.json`.
- No multi-model fan-out (one Hermes profile per attempt, taken from
  the same `hermes_profile_bindings` table the research executor uses,
  via a new role binding).
- No spec-template-driven Markdown writeup; only the JSON schema from
  `SKILL.md` is persisted.
- No edit-after-the-fact UI for `challenge_designs`. Once a task
  reaches `designed`, this change has no path back to `queued`;
  operator either accepts the draft (by acting on it downstream) or
  waits for a later supersede/requeue capability before re-running
  design. Re-design on a still-queued task (e.g. after a failed
  attempt that returned the task to `queued`) is supported by simply
  pressing "Design now" again.

## Data Model

`design_attempts`:

| Column | Purpose |
| --- | --- |
| `id uuid pk` | Attempt identity. |
| `design_task_id uuid fk` | Parent design task. |
| `attempt int` | 1-based attempt counter within the task. Failed attempts stay on disk as audit rows; the chain is reconstructed by `design_task_id` + `attempt` order, so no `parent_attempt_id` column is needed. |
| `status text` | `running | completed | failed`. |
| `claimed_by text` | Caller identity (operator session or worker id). |
| `claim_token uuid` | Request-local fencing token. It is not a lease in this change. |
| `started_at / finished_at timestamptz` | Hermes wall-clock window. |
| `profile_name_used text` | Hermes profile resolved for this attempt. |
| `prompt_path text` | Rendered prompt file on disk. |
| `hermes_log_path text` | Hermes stdout/stderr log. |
| `last_error text` | Failure reason. |
| `created_at timestamptz` | Audit. |

Constraints:

- `unique(design_task_id, attempt)`
- `attempt > 0`
- `status in ('running','completed','failed')`
- index `(design_task_id, status)`

`challenge_designs`:

| Column | Purpose |
| --- | --- |
| `id uuid pk` | Design identity. |
| `design_task_id uuid fk` | Parent design task. |
| `design_attempt_id uuid fk` | Producing attempt (`completed`). |
| `payload jsonb` | The full validated `challenges[<item>]` object. |
| `summary text` | Generated short summary (<= 280 chars) for UI. |
| `flag_format text` | Echoed from event-level payload (defaults `flag{...}`). |
| `validation_notes text` | Solver/validation outline pulled from payload. |
| `quality_gate_passed bool` | Result of running the bundled `quality-gate.md` checklist (computed by the validator). |
| `status text` | `draft | accepted | superseded` - only `draft` is written by this change. |
| `created_at / updated_at timestamptz` | Audit. |

Constraints:

- `unique(design_task_id) WHERE status = 'draft'` - at most one live
  draft per task.
- `status in ('draft','accepted','superseded')`.
- `design_attempt_id` FK with `on delete restrict` so the audit chain
  cannot drop the producing attempt.

## Prompt Wrapper

A deterministic prompt loader first reads the skill/reference files
from the repository and builds an immutable prompt context. A pure
builder then assembles the Hermes prompt from that context plus the
task/request/evidence inputs, in this order, into a single Markdown file under
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

`load_design_prompt_context(paths)` owns file IO for `SKILL.md` and
the reference files. `build_design_prompt(context, design_task,
generation_request, findings, sources) -> str` is the pure function:
it does not touch the database, filesystem, environment, or Hermes.
Both pieces are unit-testable.

## JSON Validation

The validator parses the Hermes stdout, finds the first JSON object,
and rejects the attempt unless:

- top-level keys: `event` (object), `challenges` (array of length 1)
- `event.flag_format` is a non-empty string (default `flag{...}` if
  missing - the validator inserts the default, it does not fail)
- the single challenge object must contain (all required, all
  non-empty unless marked):
  - `id` (must equal `design_tasks.challenge_id`)
  - `title`
  - `category` (must equal `design_tasks.category`)
  - `difficulty` (must equal `design_tasks.difficulty`)
  - `points` (positive int; matches `design_tasks.points` +/- 0)
  - `deployment` (non-empty string)
  - `primary_technique`
  - `learning_objective`
  - `prompt` (the spoiler-free player prompt)
  - `artifacts` (array of strings, >=1)
  - `flag_location`
  - `validation`
  - `hints` (array of strings, exactly 3, ordered gentle -> near-solve)
- for Web/Pwn:
  - `deployment` contains `docker` (case-insensitive)
  - `port` field present and equal to `design_tasks.port`
- safety guardrails (rejects):
  - any HTTP URL in `artifacts` or `validation` (artifacts must be
    relative paths)

A content-aware safety review (real-domain denylist, PII patterns,
live-malware references, etc.) is out of scope here and is left to a
later content-safety change; the validator above only enforces the
shape rules in this spec.

The validator also runs a deterministic in-code quality gate derived
from `quality-gate.md`. The Markdown file remains the human-readable
source of intent; the executable rules are explicit Python predicates
such as staged hint count, non-empty validation plan, canonical
difficulty, relative artifact paths, and category-specific deployment
checks. Result is recorded as `quality_gate_passed`. A failed quality
gate does not by itself fail the attempt - it just records
`quality_gate_passed = false` and the operator decides; schema-level
validation above is still a hard fail.

## Execution Flow

`POST /api/design-tasks/{id}/design` runs the synchronous flow in the
request thread:

1. Load task; reject if `status != 'queued'`.
2. Inside one short transaction:
   - select and lock the `design_tasks` row; require
     `status = 'queued'`.
   - select latest `design_attempts` row for this task; require the
     latest attempt number `< parent_request.max_attempts` OR no prior
     attempts.
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
     `last_error`, `finished_at`; if `attempt < max_attempts`, set
     `design_tasks.status = 'queued'` so the operator can re-trigger
     and create the next real attempt later; otherwise set
     `design_tasks.status = 'failed'`.

Each transition is gated on the `(design_attempt.id, claim_token)`
pair and the current `design_tasks.status` so a stale request cannot
overwrite the row. This is not a lease; a future worker-pool change
may add heartbeat and lease-expiry columns/behavior.

These executor-owned status writes must not go through the planning
layer's `set_design_task_status()` / planning transition validator,
which intentionally rejects `queued -> designing -> designed|failed`.
The challenge-design repository owns these transitions inside the same
transactions that create or finish attempts.

## Status Sync With `design_tasks`

This change implements the previously reserved transitions:

```
queued     -> designing  (on real attempt insert)
designing  -> designed   (on validation success)
designing  -> failed     (terminal, max_attempts exhausted)
designing  -> queued     (retry available; no queued attempt row is inserted)
```

`archived` and `draft` are owned by the planning layer and unchanged.

## API Shape

- `POST /api/design-tasks/{id}/design`
  - 200 -> `{ "design_task_id", "attempt_id",
    "design_task_status", "attempt_status",
    "challenge_design": {...} | null, "error": null | "<reason>" }`
  - 409 if task is not `queued`
  - 404 if task does not exist
- `GET /api/research/requests/{id}` (extended):
  - each `design_tasks[]` entry now includes
    `latest_design: ChallengeDesignDict | null` and
    `attempts: AttemptSummaryDict[]` ordered **oldest first**
    (`id`, `attempt`, `status`, `started_at`, `finished_at`,
    `last_error`, `prompt_artifact_url`, `log_artifact_url`). Raw
    filesystem paths are not exposed in this response.
    `prompt_artifact_url` is
    `/api/design-attempts/<attempt_id>/artifact?kind=prompt` when
    `prompt_path` exists, otherwise `null`; `log_artifact_url` is
    `/api/design-attempts/<attempt_id>/artifact?kind=log` when
    `hermes_log_path` exists, otherwise `null`.
- `GET /api/design-attempts/{id}/artifact?kind={prompt|log}`:
  - serves the file stored at `design_attempts.prompt_path` (when
    `kind=prompt`) or `hermes_log_path` (when `kind=log`).
  - 404 if the attempt does not exist or has no path of the requested
    kind.
  - 400 on unknown `kind`.
  - stored paths are project-relative paths under
    `work/design/prompts/` or `work/design/logs/`; before reading,
    resolve the stored path against the project root, canonicalize the
    candidate and allowed root, and require the candidate to be
    relative to the allowed root. Absolute paths, traversal, symlink
    escapes, and string-prefix-only checks are rejected with 403.

## UI Shape

Inside each Design Task row, a collapsible sub-panel:

- Header line: latest attempt status pill + "Design now" button (only
  enabled when task status is `queued`).
- Attempts list: numbered rows with start/end time, status, link to
  the Hermes log via the artifact endpoint.
- Design payload: collapsible JSON tree of `latest_design.payload`
  with the validator's quality-gate badge.

No prompt text is shown inline. Operators can click "View prompt" or
"View log" through a bounded design-artifact endpoint that only serves
files under `work/design/prompts/` and `work/design/logs/` by attempt
id. The endpoint must reject arbitrary paths and path traversal.

## Risks / Trade-offs

- **Synchronous endpoint may stall the request thread** during Hermes
  invocation. -> Mitigation: enforce a per-attempt timeout (config'd,
  default 600s), and surface the running attempt via the existing UI
  poll so operators see it without page reload. A future worker pool
  removes this entirely.
- **The validator only enforces shape rules; no content-safety
  review.** -> Mitigation: documented as out of scope here; a follow-up
  content-safety change can plug in additional rejectors at the same
  validation seam.
- **Prompt size grows with finding count.** -> Mitigation: cap evidence
  bullets at the first N=20 findings (logged); raw text never inlined.
- **Hermes JSON parsing.** Models often wrap JSON in fences. ->
  Mitigation: the parser strips ```json fences and looks for the
  first `{` - balanced `}` block.
- **Concurrent re-trigger** by two operators on the same task. ->
  Mitigation: select and lock the `design_tasks` row and update it
  from `queued` to `designing` in the opening transaction; second
  caller sees a non-queued status and gets 409.

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
  here. Because `designed` tasks are not `queued`, re-running a design
  requires a later explicit supersede/requeue capability.
