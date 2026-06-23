## Why

The design-planning chain only hard-constrains **count** and **difficulty
distribution**. It does nothing to constrain **technique diversity**, so a
single research run whose findings cluster around one popular technique (the
reported case: many `xor`-flavoured findings) produces a batch of design tasks
that all reuse the same 考点. Concretely:

- `src/services/design_task_planning_service.py::_findings_for_task` allocates
  findings by pure round-robin `(index + offset) % pool_size` with no awareness
  of what techniques sibling tasks already used.
- The optional Hermes planner (`src/services/design_planner_hermes.py`) is
  invoked **once per hard/expert task with no shared state**, so it cannot
  avoid a technique a sibling task already locked.
- `src/domain/design/difficulty.py::_count_techniques` only counts techniques
  **within a single challenge**; there is no cross-task dedup.
- `prompts/design_planner_prompt.md` never tells the planner to avoid recently
  used techniques.
- `generation-profiles.json` has a `runtime_rotation` quota for Web runtimes
  but no analogous technique quota.

There is also no human checkpoint: `generate_for_request` commits `draft` rows
straight to the database, so an operator cannot see or correct technique
clustering before the tasks enter authoring.

## What Changes

- **Modify** `research-planning`: every research finding SHALL carry a
  `technique_family` drawn from a controlled category-lane vocabulary. The
  **code module `technique_taxonomy.py` is the single source of truth** for that
  vocabulary; `skills/design-challenges/references/category-tactics.md` is
  documentation that mirrors it, not the authority (so a doc edit cannot silently
  shift classification). The field is a
  **weakly-enforced, derivable** attribute: persisted when the agent supplies
  it, defaulted to `other` (with a logged warning) when the agent omits or
  emits an unknown value, and resolvable from `label` for legacy findings that
  predate the field. The research run report SHALL surface the
  `technique_family` distribution and highlight the `other` ratio so a failing
  taxonomy is visible, not silently swallowed.
- **Modify** `design-task-planning`: design-task generation SHALL allocate
  findings with two **unequally-weighted** axes. `technique_family` (lane) is the
  **governance axis** — it carries the only knobs (`technique_quota`,
  `cooldown_window`) and its job is to stop the batch collapsing into one lane;
  when the pool cannot satisfy the family quota the planner relaxes it and
  records `family_quota_exceeded`. `sub_technique` (normalized label) is the
  **diagnostic axis** — best-effort avoidance of exact sibling repeats with **no
  quota knob of its own**, flagging `subtechnique_duplicate` when it cannot
  avoid. Diversity is a greedy preference, never a hard gate: `target_count` is
  always produced, low diversity only annotates tasks, and the final fallback is
  the existing round-robin.
- **Modify** `design-task-planning`: each generated `design_task` SHALL store a
  machine-readable `diversity_flags` object
  (`{"family": <lane>, "sub_technique": <key>, "warnings": [<enum>...]}`,
  warnings drawn from
  `family_quota_exceeded | subtechnique_duplicate | family_other`) computed
  once at plan time so the dashboard, API, and logs read it without recomputing.
- **Modify** `design-task-planning`: introduce a **plan-review checkpoint** on
  the existing `draft` status. `plan_reviewed_at` is **review metadata, not a new
  business status or state-machine stage** — the status set is unchanged; the
  marker only adds a precondition to the existing `draft -> queued` transition,
  and a task whose plan has not been reviewed SHALL NOT be queued. Because
  approval does not change status, regeneration stays available throughout
  review. Review, full regeneration, and **single-task regeneration** SHALL all
  be performed through `DesignTaskPlanningService` (reusing the existing
  parent-row lock and `replace_draft_or_archived_tasks` rebuild), never by direct
  DB writes from the dashboard. Single-task regeneration returns a **three-state
  outcome** (`regenerated | regenerated_with_warning | no_alternative`): unlike
  batch generation it has no count obligation, so when only equivalent or
  sibling-duplicate candidates remain it declines as a no-op with a
  machine-readable reason (`research_diversity_insufficient |
  subtechnique_exhausted`) rather than forcing a duplicate. Family is soft (no
  `family_exhausted` refusal); family saturation surfaces as
  `regenerated_with_warning`.
- **Modify** `design-task-planning`: the dashboard design-task view SHALL be a
  **read-only review surface plus exactly three service-backed actions**
  (approve, regenerate-all, regenerate-one). It renders a **plan matrix**
  (task_no / difficulty / family / sub_technique / scenario seed) by reading
  stored `diversity_flags` — colour-coding `family_quota_exceeded` (mild
  governance signal) distinctly from `subtechnique_duplicate` (finer diagnostic
  hint) — and shows a "research diversity insufficient — consider re-running
  research" hint when duplicates are pervasive. The dashboard SHALL NOT compute
  diversity or policy itself; this three-action cap keeps it from drifting into a
  planning console.
- **Add** a classification-only foundation module
  `src/domain/design/technique_taxonomy.py` (Layer 1) holding the lane enum, the
  `label`→lane keyword map, `resolve_family()` and `resolve_sub_technique()`.
  This module is **pure classification — it knows nothing about difficulty,
  mechanical transforms, or chain folding** (those are a separate Layer 2 owned
  by the sibling change `fix-difficulty-step-inflation`). It is the **single
  source of truth for family/sub-technique normalization**, consumed by the
  research prompt rendering, the Python dedup path, the dashboard, and (read-only)
  the difficulty layer.

This proposal does **not**:

- add a multi-turn conversational planning session (the `draft` checkpoint is
  the lightweight stand-in; a true agent-plan session is a later increment);
- change the difficulty rubric or step-counting semantics — that is the sibling
  change `fix-difficulty-step-inflation`;
- touch the shard prompt or split it into a skill (deferred).

## Capabilities

### Modified Capabilities

- `research-planning`: ADD `technique_family` to the finding contract and the
  run report.
- `design-task-planning`: ADD two-axis diversity allocation, `diversity_flags`,
  the `plan_reviewed_at` queue gate, single-task regeneration, and the
  dashboard plan matrix.

### New Capabilities

- None. (The new `technique_taxonomy.py` module is a shared classification
  helper, not a separately-specced capability.)

## Impact

- **Code**: `prompts/research_prompt.md`, `src/domain/research.py`
  (+`technique_family`), research parser/validator, new
  `src/domain/design/technique_taxonomy.py`,
  `src/services/design_task_planning_service.py`,
  `src/services/design_planner_hermes.py`, `prompts/design_planner_prompt.md`,
  `src/domain/design_tasks.py` (+`diversity_flags`, `plan_reviewed_at`),
  `src/domain/design_task_validators.py` (queue gate),
  `generation-profiles.json` (quota/cooldown knobs), `src/web` design-task
  endpoints + static plan-matrix view.
- **Database**: Alembic revision adding nullable `research_findings.technique_family`
  and `design_tasks.diversity_flags` (JSON) + `design_tasks.plan_reviewed_at`
  (timestamp). All nullable; legacy rows are grandfathered (NULL `technique_family`
  resolves from `label`; NULL `plan_reviewed_at` rows generated before this
  change are treated as review-exempt to avoid backfill).
- **Compatibility**: `technique_family` weak enforcement means an existing
  research run never breaks; `diversity_flags` is additive to the task JSON the
  reconciler already tolerates; the queue gate only rejects *new* unreviewed
  drafts.
- **Out of scope**: conversational planning sessions, shard-prompt split,
  difficulty rubric changes.
