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

### D0 — Diversity is advisory; count is authoritative; clear axis ownership

The whole feature is a **soft governance + diagnostics** layer, never a hard
gate on generation. The responsibility split is:

- **`technique_family` = governance axis.** Carries the only tunable knobs
  (`technique_quota`, `cooldown_window`). It is a hard-soft *hybrid*: the planner
  prefers to satisfy the quota, falls back when the pool can't, and **records**
  the fallback (`family_quota_exceeded`). Its job is to stop the whole batch
  collapsing into one lane. `technique_family` is treated as a **soft governance
  signal, not absolute truth** — if the upstream taxonomy drifts, only governance
  quality degrades; generation correctness and count are unaffected.
- **`sub_technique` = diagnostic axis.** Pure **best-effort avoidance** of
  exact sibling repeats (e.g. `xor` three times in one batch); when it can't
  avoid, it **flags** `subtechnique_duplicate`. It introduces **no second set of
  quota knobs** and never drives a hard fallback of its own.
- **`plan_reviewed_at` = review marker.** Approval metadata only (see D4).
- **dashboard = visualization + intervention.** Read-only review plus three
  service-backed actions; no policy computation (see D4).
- **`target_count` is absolute.** Diversity is a preference resolved greedily,
  not a global optimization; low diversity only annotates tasks and never blocks
  or reduces the generated set.

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

### D2 — Greedy allocation: family governs, sub-technique diagnoses

`technique_taxonomy.py` exposes `resolve_family(finding)` (coarse, lane — **added
by this change**) and `resolve_sub_technique(finding)` (fine, canonical key —
**introduced and specified by `fix-difficulty-step-inflation`**, which owns the
normalization rules and the conservatism guard; this change only consumes it).
Allocation is a single greedy pass (not a global
optimizer) that, when picking each task's primary finding, applies the two axes
with **different weight**:

- **family — governance.** Soft quota `ceil(target_count / distinct_families_in_pool)`
  plus cooldown window `K` (default `1`: avoid reusing the previous task's family
  when an alternative exists). The planner prefers candidates within quota; if
  none exist it relaxes the quota and records `family_quota_exceeded`. These are
  the only tunable knobs.
- **sub-technique — diagnostics.** Among the family-preferred candidates the
  planner *also* prefers one whose sub-technique no sibling has used yet
  (best-effort avoidance); if every remaining candidate repeats a sibling
  sub-technique it picks anyway and records `subtechnique_duplicate`. There is
  **no sub-technique quota knob** and sub-technique never triggers its own
  fallback ladder — it only re-orders preference and annotates.

If both axes are unsatisfiable the planner falls back to the existing
round-robin. **`target_count` is always produced; diversity never blocks or
shrinks the set.** The Hermes planner additionally receives `avoid_techniques`
(sibling sub-technique keys) so its `considered_techniques` diversify;
`design_planner_prompt.md` gains a `SHOULD avoid reusing: {used}` clause.

Family quota divisor and `K` are read from `generation-profiles.json`
(`technique_quota`, `cooldown_window`) alongside the existing `runtime_rotation`,
so operators tune *family governance* the same way they tune runtimes;
sub-technique has no such knob by design.

### D3 — `diversity_flags` is computed once and stored

The planner writes `design_tasks.diversity_flags` =
`{"family": <lane>, "sub_technique": <key>, "warnings": [...]}` with warnings
from the closed enum
`family_quota_exceeded | subtechnique_duplicate | family_other`. UI, API, and
logs read it verbatim; nothing recomputes diversity downstream. The enum is a
list (not a scalar) so a task can carry both `family_quota_exceeded` and
`subtechnique_duplicate` at once. The two are **not co-equal**: per D0,
`family_quota_exceeded` reports a governance relaxation while
`subtechnique_duplicate` is a finer diagnostic hint — consumers should treat the
sub-technique flag as advisory, not as an absolute dedup verdict.

All warnings are diagnostic/intervention signals only — they never gate a state
transition. The sole queue precondition is `plan_reviewed_at`; a flagged task is
fully queueable once approved. Neither backend nor UI may turn a warning into an
extra approval gate, so the warnings stay a visualization signal and never drift
into a second checkpoint.

### D4 — Checkpoint reuses `draft`; gate is `plan_reviewed_at`

No new business status. `plan_reviewed_at` is **review metadata, not a state in
the design-task state machine** — the status set
(`draft|queued|designing|...`) is unchanged; the marker only adds a precondition
to the *existing* `draft -> queued` transition. `draft` already means
"generated, not yet in authoring", which is exactly the review window.

Because approval **does not change status**, a task stays `draft` throughout
review, so regenerate-all / regenerate-one remain available the entire time an
operator is reviewing — the gate does **not** widen the window in which
regeneration is blocked. That window still opens only after `draft -> queued`,
exactly as today. Legacy drafts created before this change are review-exempt (by
a generation-time marker, or when `plan_reviewed_at` is NULL and `created_at`
precedes the revision) so the gate never strands in-flight work.

Approval signs the **current draft version**, not the request: any regeneration
(full or single-task) clears the affected task's `plan_reviewed_at`, so a stale
approval can never carry a never-reviewed plan into `queued`; after a
single-task regen only that task needs re-approval while siblings keep theirs.
Approval, regenerate-all, and regenerate-one are all
`DesignTaskPlanningService` methods so they inherit the existing parent-row lock
and the `replace_draft_or_archived_tasks` rebuild path. The dashboard is a
**read-only review surface plus exactly three service-backed action buttons**
(approve, regenerate-all, regenerate-one); it computes no diversity or policy
itself, only renders stored `diversity_flags` and issues HTTP calls that land in
the service, never SQL. Keeping the dashboard to these three actions is a
deliberate scope cap so it does not drift into a planning console.

### D5 — Single-task regeneration carries the sibling avoid-set

`regenerate_task(request_id, task_no)` is the one genuinely new allocation path:
unlike `replace_draft_or_archived_tasks` (whole-set replace) it re-plans **one**
slot while feeding the families/sub-techniques of all *other* current draft
tasks into the avoid-set. Its contract is **deliberately asymmetric** with batch
generation: batch/regenerate-all must preserve `count == target_count` and so
fall back to a duplicate-flagged slot, but regenerate-one has no count
obligation and may decline. The two MUST NOT share a fill-forcing code path.

It returns a three-state outcome (see spec) computed greedily over the candidate
set (slot hard constraints, minus sibling sub-techniques, preferring within
family quota/cooldown):

- **`regenerated`** — a clean candidate (≠ slot's `(family, sub_technique)`,
  ≠ sibling sub-technique, within family quota).
- **`regenerated_with_warning`** — a sibling-avoiding candidate exists but only
  outside the family quota; the new task carries `family_quota_exceeded`. Per D0
  family is soft, so saturation surfaces here, never as a refusal — there is no
  `family_exhausted` no-alternative reason.
- **`no_alternative`** — only equivalent-to-current or sibling-duplicate
  candidates remain; a **true no-op** (no row replace, no timestamp churn) with
  reason `research_diversity_insufficient` (no other distinct finding) or
  `subtechnique_exhausted` (distinct findings exist but all sibling duplicates).
  A `research_diversity_insufficient` response is the authoritative trigger for
  the dashboard's "re-run research" hint.

It operates under the same parent-row lock and refuses if any sibling task has
left `draft`/`archived` (consistent with the existing "regeneration only before
queue release" requirement).

### D6 — Allocation is deterministic; Hermes prose is the only non-determinism

The finding→task allocation, cooldown, fallback, and `diversity_flags` are a
pure function of (ordered findings, difficulty distribution, profile knobs).
No randomness, no wall-clock; ties break on a stable key (ascending finding
index/id). This keeps tests stable and makes the matrix explainable to an
operator ("why is this one duplicated?" has a fixed answer). The optional Hermes
enrichment for hard/expert may vary its prose, but the allocation decision is
taken **before and independently of** any Hermes call, so the diversity outcome
is reproducible even when Hermes output differs. This is why the deterministic
guarantee can coexist with an LLM in the loop.

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
(timestamptz). **No backfill.** The grandfather strategy covers all three NULL
cases coherently (see the "Pre-change rows are grandfathered" spec requirement):

- NULL `technique_family` → resolved via `resolve_family` (label derivation /
  `other`), never rejected.
- NULL `diversity_flags` → tolerated everywhere; API returns it absent/empty, UI
  renders no warning, nothing recomputes.
- NULL `plan_reviewed_at` on a `draft` predating the revision → review-exempt
  (generation-time marker, or NULL + `created_at` before the revision), so the
  new queue gate never strands in-flight work.

Only tasks generated after this change carry the review obligation, two-axis
allocation, and `diversity_flags`.

## Open Questions (resolved)

- family normalization location → **research stage** (agent-labelled, Python
  derivation as fallback). [D1]
- quota exhaustion → **relax to preserve count, warn**. [D2]
- checkpoint entry point → **dashboard**, service-backed. [D4]
- "same family, different sub-technique" → **two-axis with different weight**:
  family is the governance axis (quota/cooldown), sub-technique is the
  best-effort diagnostic axis (flag only, no knob). [D0/D2/D3]
