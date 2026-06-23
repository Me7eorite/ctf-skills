## Why

Difficulty is being driven by the **number of mechanical solve steps** instead
of the number of distinct 考点 (techniques). Two challenges that are
fundamentally easy get classified differently purely because one has an extra
decode step:

- Q1: `strings <binary>` → base64-decode → flag (2 steps)
- Q2: IDA → xor-decrypt → base64-decode → flag (3 steps)

Both have a single 考点 — "recover the flag by peeling static encodings" — yet
Q2 is graded harder only because the decode chain is one layer longer. Two
places in the chain encode this conflation:

1. **Prose**: `skills/design-challenges/references/category-tactics.md` and the
   rubric describe `easy = single-step solve` and `hard = … multi-step`, which
   lets the authoring agent read "more steps" as "more difficult".
2. **Rubric**: `src/domain/design/difficulty.py` uses `intended_path_min` as a
   *lower bound* per tier (hard `=3`, expert `=4`). A trivial 3-layer decode
   chain meets hard's step floor, so the validator does not push back — it
   effectively **rewards padding the solve with mechanical steps**.

The correct difficulty unit is distinct 考点 plus novelty; `intended_path`
length should at most be an upper sanity bound, never a promotion signal.

## What Changes

- **Modify** the difficulty-alignment behaviour (capability
  `structured-challenge-designs`) so **mechanical decode/unwrap transforms fold
  order-free**. A technique is mechanical iff its normalized `sub_technique` is
  in a mechanical-transform set of canonical sub-technique keys (`base64`,
  `base32`, `hex`, `url`, `rot`, `caesar`, `xor`, `gzip`, `zlib`, `strings`,
  …) — judged purely from the label, with **no
  ordering source and no runtime key-state inference** (decision A). The counted
  total is the number of distinct **non-mechanical** sub_techniques; when every
  technique is mechanical the total is exactly `1` (a pure decode chain is one
  `encoding` 考点), and mechanical transforms add nothing when a non-mechanical
  technique is present. The folded class is named **`mechanical_class`** (value
  `encoding`), **deliberately not `family`** — `family` is the research lane /
  governance axis in `add-design-technique-diversity`, an unrelated taxonomy.
  This change **consumes the existing `resolve_sub_technique`** from
  `src/domain/design/technique_taxonomy.py`, which is already built (with its
  normalization + conservatism guard) and shipped by `add-design-technique-diversity`;
  it does **not** introduce or modify that module. The new fold logic lives in a
  dedicated **Layer 2** module `src/domain/design/mechanical_transforms.py`
  (mechanical-transform set + `is_mechanical_transform()`) that consumes Layer 1's
  `resolve_sub_technique`. Classification (existing L1), mechanical judgement
  (new L2), and the difficulty rubric (L3) stay in three separate layers — no
  single file carries 分类 + 难度 + 折叠规则.
- **Modify** the difficulty rubric so `intended_path` length is **demoted from a
  difficulty driver to an upper-bound sanity check only**. The per-tier
  `intended_path_min` floors SHALL be removed (or set to `1` for every tier) so
  a short, low-考点 solve is no longer forced to pad steps to qualify;
  `intended_path_max` is retained to catch genuinely runaway walkthroughs.
  Difficulty SHALL be driven by `techniques` count (`techniques_min/max`) and,
  for expert, `needs_novelty`.
- **Modify** the prose guidance (`category-tactics.md` /
  `difficulty-rubric.md` and `prompts/design_planner_prompt.md`) to state
  explicitly that difficulty is the count of **distinct 考点 + novelty, NOT the
  number of solve steps**, with the worked counter-example that
  `strings→base64→flag` and `IDA→xor→base64→flag` are **both easy** because each
  has a single decode 考点.

This proposal does **not**:

- change technique-diversity allocation, `diversity_flags`, the plan checkpoint,
  or the dashboard matrix — those live in the sibling change
  `add-design-technique-diversity`;
- introduce or modify `technique_taxonomy.py` — that module (including
  `resolve_sub_technique` + normalization) is already built and owned by
  `add-design-technique-diversity`; this change only consumes
  `resolve_sub_technique` and adds the Layer 2 `mechanical_transforms.py`;
- touch the shard prompt split (deferred).

## Capabilities

### Modified Capabilities

- `structured-challenge-designs`: ADD a difficulty-alignment rule set that makes
  distinct 考点 + novelty the difficulty driver, folds mechanical transforms
  order-free into one `encoding` `mechanical_class`, and demotes `intended_path`
  length to an upper bound.

### New Capabilities

- None.

## Impact

- **Code**: new `src/domain/design/mechanical_transforms.py` (Layer 2:
  mechanical-transform set + `is_mechanical_transform`, plus the `mechanical_class`
  constant `encoding`; order-free, no chain/ordering API);
  `src/domain/design/difficulty.py` (rubric `intended_path_min` removal;
  `_count_techniques` gathers `techniques[]`+`primary_technique`+`secondary_technique`,
  wraps each string label as `{"label": value}` before routing it through the
  existing `resolve_sub_technique`, then counts distinct non-mechanical
  sub_techniques, folding all mechanical ones to one `encoding` only when no
  non-mechanical technique exists),
  `src/services/design_prompt.py` (render `intended_path` as an upper-bound
  budget, not a min-max difficulty range),
  `prompts/design_planner_prompt.md`,
  `skills/design-challenges/references/category-tactics.md`,
  `skills/design-challenges/references/difficulty-rubric.md`.
  This change does **not** create or modify `technique_taxonomy.py`.
- **Database**: none.
- **Dependency**: requires `src/domain/design/technique_taxonomy.py`
  (`resolve_sub_technique` + normalization), which is **already shipped** by
  `add-design-technique-diversity`. Since that module already exists in the
  codebase, this change is effectively ready to implement now.
- **Compatibility**: the `intended_path_min` relaxation only loosens, so it
  never invalidates a prior pass. The order-free mechanical fold, however, counts
  more strictly than before (a design that previously met a technique floor by
  padding with mechanical transforms now counts fewer 考点 and may fail that
  floor) — this is intentional, since such designs were mis-graded. Historical
  stored designs are unaffected because `validate_difficulty_alignment` only runs
  on new design attempts and the `legacy_grandfather` path is retained; no stored
  row is re-validated.
- **Out of scope**: diversity allocation, plan checkpoint, shard-prompt split.
