## Context

`validate_difficulty_alignment` in `src/domain/design/difficulty.py` checks a
validated design against a per-tier `DifficultyRubric`. Two rubric properties
conflate "steps" with "difficulty":

- `intended_path_min` is a per-tier **lower bound** (easy `1`, medium `2`,
  hard `3`, expert `4`). A trivial multi-layer decode solve clears the hard
  floor just by listing each decode as a step.
- `_count_techniques` unions `techniques` + `primary_technique` +
  `secondary_technique` with only case/whitespace normalization, so a design
  that lists `xor` and `base64` as two techniques counts as two 考点 even though
  it is one "peel the encoding" idea.

Meanwhile the prose the authoring agent reads (`category-tactics.md`,
`difficulty-rubric.md`) frames tiers as `single-step` vs `multi-step`, directly
inviting the step-count heuristic.

## Goals / Non-Goals

**Goals**

- Make distinct 考点 + novelty the sole difficulty driver.
- Stop a linear mechanical decode/unwrap chain from inflating either the
  technique count or the step-based tier floor.
- Keep `intended_path` length only as an upper sanity bound.
- Change nothing that previously validated into a failure.

**Non-Goals**

- Re-grading or migrating historical designs.
- Diversity allocation / dedup across a batch (sibling change).
- Defining the lane vocabulary — consumed from the sibling change's Layer 1
  `technique_taxonomy.py`.

## Decisions

### D1 — Linear decode chains collapse to one technique

`_count_techniques` SHALL route each declared technique through
`resolve_sub_technique` and, for the `encoding` family specifically, collapse
**consecutive mechanical transformations** into a single counted technique. A
transformation is "mechanical" when it requires no new knowledge beyond applying
a standard decoder with already-available inputs: `base64`, `base32`, `hex`,
`url`, `rot/caesar`, `xor` with a key already recovered earlier in the path,
`gzip/zlib` unwrap, `strings` extraction, and equivalent single-format unwraps.
Two stacked encodings therefore count as **one** `encoding` 考点.

Non-mechanical techniques (a real vulnerability, an algorithm to reverse, a
key-recovery step that itself needs analysis) are unaffected and continue to
count individually.

The three concerns are deliberately layered into three modules so no single
file carries 分类 + 难度 + 折叠规则:

- **Layer 1 — `technique_taxonomy.py`** (sibling change): classification only —
  `resolve_family` / `resolve_sub_technique`. Knows nothing about mechanical
  transforms or difficulty.
- **Layer 2 — `mechanical_transforms.py`** (this change): the
  mechanical-transform set, `is_mechanical_transform(sub_technique)`, and
  `collapse_mechanical_chain(sub_techniques) -> list`. Depends on Layer 1's
  sub-technique keys; knows nothing about the rubric.
- **Layer 3 — `difficulty.py`**: rubric + alignment; `_count_techniques`
  delegates to Layer 2 for collapse, then applies the rubric.

Dependency direction is a clean DAG (L3 → L2 → L1); Layer 1 imports neither of
the others, so diversity allocation can consume L1 without pulling in any
difficulty logic.

### D2 — `intended_path` length is an upper bound only

The per-tier `intended_path_min` floors SHALL be removed (equivalently, set to
`1` for all tiers). `intended_path_max` is retained to flag runaway
walkthroughs. The difficulty driver becomes `techniques_min/max` plus, for
expert, `needs_novelty`. This removes the incentive to pad a solve with steps to
clear a floor, and it means Q2 (xor→base64→flag) — one 考点 after D1 — can no
longer be promoted to hard by step count.

### D3 — Prose de-couples steps from difficulty

`category-tactics.md` and `difficulty-rubric.md` SHALL define
"**考点 (distinct technique) ≠ 解题步骤 (mechanical step)**" and carry the worked
counter-example: `strings→base64→flag` and `IDA→xor→base64→flag` are **both
easy** (one decode 考点 each); an extra decode layer does not raise difficulty.
`design_planner_prompt.md` SHALL add: "Difficulty is driven by the count of
distinct 考点 + novelty, NOT by the number of solve steps. A linear
decode/unwrap chain is ONE technique regardless of length."

## Risks / Trade-offs

- **Under-counting a real chained technique.** If an author legitimately uses
  `xor` as a non-trivial standalone reversing step (key not yet known, must be
  derived), collapsing it would under-count. Mitigated by D1's "xor with an
  *already-recovered* key" scoping — a key-recovery step that needs analysis is
  not mechanical and still counts.
- **Relaxing floors could let a thin design claim a high tier.** It cannot: the
  technique *minimums* (`techniques_min`) and expert `needs_novelty` remain, so
  a single-考点 design still fails hard/expert on the technique axis. Only the
  step floor — the wrong signal — is removed.
- **Ordering dependency.** Requires Layer 1 `technique_taxonomy.py` from the sibling
  change. Sequence that change's foundation task first.

## Migration

No schema change. `validate_difficulty_alignment` keeps its
`legacy_grandfather` escape hatch. Because the change only relaxes a lower bound
and refines technique counting downward for mechanical chains, no
previously-valid design becomes invalid; no re-validation pass is required.
