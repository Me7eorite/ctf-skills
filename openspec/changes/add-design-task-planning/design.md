## Context

Today the file-backed shard queue consumes JSON shaped as:

```json
{
  "challenges": [
    {
      "id": "web-0001",
      "title": "...",
      "category": "web",
      "difficulty": "easy",
      "primary_technique": "...",
      "learning_objective": "...",
      "points": 100,
      "port": 8080
    }
  ]
}
```

`SeedStore.validate_seed()` treats `id`, `title`, `category`, `difficulty`,
`primary_technique`, and `learning_objective` as required text fields; `points`
is a positive integer; `port` is required for Web/Pwn. That shape is the
compatibility target for the first database-backed design-task model.

The research workflow already stores:

- operator intent in `generation_requests`
- attempts in `research_runs`
- evidence in `research_sources` and `research_findings`

This change adds the next layer only: task planning. It does not run the
challenge design agent.

## Goals / Non-Goals

**Goals:**

- Represent each future challenge as one `design_tasks` row linked to a
  `generation_requests` row.
- Preserve the old shard challenge-row field names so later export/batching is
  straightforward.
- Generate exactly `generation_requests.target_count` draft task rows from the
  latest completed research run.
- Validate generated task rows before persistence so bad planner output cannot
  enter the queue.
- Make task generation observable and repeatable from the request detail page.
- Keep prompt rendering out of the database.

**Non-Goals:**

- No `design_batches` table in this change.
- No design worker, queue claim, lease, heartbeat, or retry attempts.
- No design prompt execution through Hermes.
- No export to `work/shards/pending/`.
- No approval workflow beyond `draft -> queued` and `archived`.

## Data Model

`design_tasks`:

| Column | Purpose |
| --- | --- |
| `id uuid pk` | Task identity. |
| `generation_request_id uuid fk` | Parent demand/request. |
| `research_run_id uuid fk` | Completed research run used as evidence. |
| `task_no int` | Stable 1-based order within the request. |
| `challenge_id text` | Old shard seed `id`; prefix must match category. |
| `title text` | Old shard seed `title`. |
| `category text fk` | Must equal parent request category. |
| `difficulty text` | One of `easy|medium|hard|expert`. |
| `primary_technique text` | Old shard seed `primary_technique`. |
| `learning_objective text` | Old shard seed `learning_objective`. |
| `points int` | Positive scoring value. |
| `port int nullable` | Required for Web/Pwn; null for RE unless explicitly needed. |
| `scenario text` | Human-readable scenario framing. |
| `constraints jsonb` | Runtime and design constraints used by prompt rendering. |
| `evidence_summary text` | Short summary of why this task exists. |
| `source_finding_ids jsonb` | Referenced `research_findings.id` values. |
| `status text` | `draft|queued|designing|designed|failed|archived`. |
| `created_at/updated_at` | Audit timestamps. |

The table intentionally does **not** include `prompt_input`. A later design
executor renders the prompt from:

```text
design-challenges skill/template
+ generation_requests fields
+ design_tasks fields
+ referenced research_findings/research_sources
```

## Status Model

Initial implementation uses:

```text
draft -> queued
draft -> archived
queued -> archived
```

The enum/check constraint also permits future worker states:

```text
queued -> designing -> designed
designing -> failed
failed -> queued
```

Those worker transitions are reserved for a follow-up change. This change
should not create code paths that set `designing`, `designed`, or `failed`
except fixtures/tests that validate allowed statuses.

## Task Generation Flow

1. Operator opens a researched request.
2. Operator clicks `Generate design tasks`.
3. Service loads the request, latest completed `research_run`, findings, and
   sources.
4. The requirement-planning agent or deterministic adapter produces candidate
   task rows. Each candidate must already be shaped like a shard seed plus
   planning metadata.
5. Service validates:
   - candidate count equals `target_count`
   - `task_no` is exactly `1..target_count`
   - category equals the parent request category
   - difficulty distribution matches `difficulty_distribution`
   - required shard seed fields are non-empty
   - `challenge_id` prefix matches category
   - `points > 0`
   - Web/Pwn rows have a valid port
   - each task references at least one finding from the same `research_run`
6. Existing `draft` tasks for the same request may be replaced only when no
   task has reached `queued` or any later status. This keeps generation
   repeatable before queueing while preventing silent replacement of work the
   operator already released.
7. New rows are inserted as `draft`.

## Agent Boundary

This change treats task planning as the conversion from demand analysis to
challenge task rows. It is not the same as designing the full challenge. The
planner may be backed by Hermes later, but its output contract is database rows
compatible with the existing shard seed shape.

The later design agent consumes one task row and renders a prompt via the
project skill. That later prompt may include much richer context, but the
database record remains compact and queryable.

## API Shape

- `POST /api/research/requests/{id}/design-tasks/generate`
  - creates/replaces draft design tasks from the latest completed research run
  - returns generated rows
- `GET /api/research/requests/{id}`
  - includes `design_tasks`
- `POST /api/design-tasks/{id}/queue`
  - `draft -> queued`
- `POST /api/design-tasks/{id}/archive`
  - `draft|queued -> archived`

## UI Shape

Request detail gains a `Design Tasks` section:

- counts by status
- rows showing `task_no`, `challenge_id`, `title`, `difficulty`,
  `primary_technique`, `status`, and evidence count
- actions: `Generate design tasks`, `Queue`, `Archive`

The UI does not show rendered prompts. It shows the database fields that will be
used when a later design worker renders prompts.

## Future Batch Compatibility

This change deliberately does not add `design_batches`. A future batch change
can add:

```text
design_batches 1 -> N design_tasks
design_tasks.design_batch_id nullable fk
```

without changing the meaning of a task row. A batch will group existing
per-challenge tasks; it will not replace them.
