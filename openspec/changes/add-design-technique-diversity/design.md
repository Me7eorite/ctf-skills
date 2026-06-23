## Context

`DesignTaskPlanningService.generate_for_request` turns a completed research run
into `target_count` `draft` design tasks. The allocation is purely positional
(`_findings_for_task` round-robins findings; `primary_technique` is taken
verbatim from `finding.label`), and the optional Hermes planner runs once per
hard/expert task with no shared history. Difficulty alignment
(`domain/design/difficulty.py`) only counts techniques inside one challenge.
The net effect: when the finding pool is technique-clustered, the batch
inherits that clustering with nothing to detect or correct it.

The reporter's concrete symptom — "xor 相关 finding 容易被检出，后续 design 反复
把它当默认解法" — is **sub-technique** repetition, which a lane-only view cannot
see (all xor variants share one lane). So diversity must be measured on two
axes, and the operator needs a checkpoint to catch what automation cannot
manufacture.

## Goals / Non-Goals

**Goals**

- Make technique diversity a first-class, machine-checked property of a design
  batch, on both a coarse (lane) and a fine (sub-technique) axis.
- Keep diversity enforcement *soft*: never fail a batch for low diversity;
  surface it instead, because the design layer cannot create variety the
  research stage did not produce.
- Give the operator a review checkpoint and correction tools (approve,
  regenerate-all, regenerate-one) without adding a new business status or
  letting the dashboard write the database directly.
- Establish one shared normalization source consumed by both the prompt and the
  Python dedup path so they cannot drift.

**Non-Goals**

- Multi-turn conversational planning. The `draft` + `plan_reviewed_at`
  checkpoint is intentionally a read-model-plus-actions surface, not a session.
- Difficulty/step-count semantics (sibling change).
- Manufacturing diversity upstream — when the pool is genuinely monocultural the
  correct fix is re-running research; this change only makes that legible.

## Decisions

### D1 — `technique_family` is weakly enforced and derivable

`research_findings.technique_family` is a nullable persisted column. The
research agent is asked (via `research_prompt.md`) to set it from the injected
lane vocabulary. On read, dedup never trusts the raw column; it calls
`resolve_family(finding)` which returns the stored value when present and valid,
else derives from `label`, else `other`. Consequences:

- Legacy findings (NULL column) work with zero backfill.
- An unknown agent-supplied value is coerced to `other` with a warning, not an
  error — a single bad finding never wastes a research run.
- `other` is **monitored, not swallowed**: the run report carries the family
  distribution and the `other` ratio; a ratio above
  `RESEARCH_FAMILY_OTHER_WARN_RATIO` (default `0.30`) emits a neutral
  "classification miss-rate high — check the lane vocabulary or research scope"
  warning. The wording stays neutral because a high ratio can mean either a too-
  narrow vocabulary or genuinely scattered research.

### D2 — Two diversity axes, soft quota, count-preserving fallback

`technique_taxonomy.py` (Layer 1, classification-only) exposes
`resolve_family(finding)` (coarse, lane) and
`resolve_sub_technique(finding)` (fine, normalized `label`: lowercased,
trimmed, stop-word-stripped). Allocation tracks two `Counter`s:

- **family soft quota** = `ceil(target_count / distinct_families_in_pool)`,
  plus a cooldown window `K` (default `1`: don't reuse the same family as the
  immediately preceding task when an alternative exists).
- **sub-technique cap** = `ceil(target_count / distinct_sub_techniques_in_pool)`
  — the stricter axis, the one that actually catches `xor×3`.

When picking a task's primary finding the planner prefers candidates that
violate neither axis; if none exist it relaxes family first, then
sub-technique, and finally falls back to the existing round-robin. **Count is
always preserved.** Each relaxation records the corresponding `diversity_flags`
warning. The Hermes planner additionally receives `avoid_techniques` (the
sub-technique keys already used by sibling tasks) so its `considered_techniques`
diversify; `design_planner_prompt.md` gains a `SHOULD avoid reusing: {used}`
clause.

Quota divisors and `K` are read from `generation-profiles.json`
(`technique_quota`, `cooldown_window`) alongside the existing
`runtime_rotation`, so operators tune diversity the same way they tune runtimes.

### D3 — `diversity_flags` is computed once and stored

The planner writes `design_tasks.diversity_flags` =
`{"family": <lane>, "sub_technique": <key>, "warnings": [...]}` with warnings
from the closed enum
`family_quota_exceeded | subtechnique_duplicate | family_other`. UI, API, and
logs read it verbatim; nothing recomputes diversity downstream. The enum is a
list (not a scalar) so a task can carry both `family_quota_exceeded` and
`subtechnique_duplicate` at once.

### D4 — Checkpoint reuses `draft`; gate is `plan_reviewed_at`

No new business status. `draft` already means "generated, not yet in
authoring", which is exactly the review window. A nullable
`design_tasks.plan_reviewed_at` timestamp marks operator approval. The
`draft -> queued` transition in `design_task_validators.py` gains a guard:
a task with NULL `plan_reviewed_at` cannot be queued (legacy drafts created
before this change are exempt by a generation-time marker to avoid blocking
in-flight work). Approval, regenerate-all, and regenerate-one are all
`DesignTaskPlanningService` methods so they inherit the existing parent-row
lock and the `replace_draft_or_archived_tasks` rebuild path. The dashboard is a
pure read-model + action-button surface; it issues HTTP calls that land in the
service, never SQL.

### D5 — Single-task regeneration carries the sibling avoid-set

`regenerate_task(request_id, task_no)` is the one genuinely new allocation path:
unlike `replace_draft_or_archived_tasks` (whole-set replace) it re-plans **one**
slot while feeding the families/sub-techniques of all *other* current draft
tasks into the avoid-set, so a single regenerate cannot re-introduce a
duplicate it was meant to remove. It operates under the same parent-row lock and
refuses if any sibling task has left `draft`/`archived` (consistent with the
existing "regeneration only before queue release" requirement).

## Risks / Trade-offs

- **Monocultural pool surfaces as a wall of red.** If the research pool has
  fewer distinct sub-techniques than `target_count`, count-preserving fallback
  forces `subtechnique_duplicate` on several tasks. This is intended: the
  matrix tells the operator to re-run research rather than pretending design can
  fix it. The dashboard hint makes that explicit.
- **Lane vocabulary drift.** If the prompt's injected lanes and the Python
  keyword map diverge, family classification splits. Mitigated by D1's single
  source (`technique_taxonomy.py` is the only definition; the prompt is rendered
  *from* it).
- **Cooldown vs. small batches.** For `target_count <= 2` the cooldown window is
  meaningless; quota math degenerates gracefully (divisor ≥ 1) and the window is
  skipped when no alternative family exists.

## Migration

Single additive Alembic revision: nullable `research_findings.technique_family`
(text), `design_tasks.diversity_flags` (JSON), `design_tasks.plan_reviewed_at`
(timestamptz). No backfill. Legacy `draft` rows are marked review-exempt at
upgrade time (or treated as exempt when `plan_reviewed_at` is NULL **and**
`created_at` precedes the revision) so the new queue gate never strands
in-flight work.

## Open Questions (resolved)

- family normalization location → **research stage** (agent-labelled, Python
  derivation as fallback). [D1]
- quota exhaustion → **relax to preserve count, warn**. [D2]
- checkpoint entry point → **dashboard**, service-backed. [D4]
- "same family, different sub-technique" → **two-axis**; family is mild,
  sub-technique is the real duplicate signal. [D2/D3]
