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
  `structured-challenge-designs`) so a **linear mechanical decode/unwrap chain
  collapses to a single technique**. Consecutive purely-mechanical
  transformations (`base64`, `hex`, `xor` with an already-recovered key,
  `strings`, single-format unwrap, etc.) SHALL count as one technique of family
  `encoding`, regardless of how many layers are stacked. The collapse logic
  lives in a dedicated **Layer 2** module
  `src/domain/design/mechanical_transforms.py` (mechanical-transform set +
  `is_mechanical_transform()` + `collapse_mechanical_chain()`) that depends on
  the sibling change's classification-only Layer 1
  `src/domain/design/technique_taxonomy.py` (`resolve_family` /
  `resolve_sub_technique`). Classification, mechanical-transform judgement, and
  the difficulty rubric stay in three separate layers — no single file carries
  分类 + 难度 + 折叠规则.
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
- introduce the Layer 1 `technique_taxonomy.py` module itself — that is the
  sibling change's foundation and must land first; this change only adds the
  Layer 2 `mechanical_transforms.py` on top of it;
- touch the shard prompt split (deferred).

## Capabilities

### Modified Capabilities

- `structured-challenge-designs`: ADD a difficulty-alignment rule set that makes
  distinct 考点 + novelty the difficulty driver, collapses linear decode chains
  to one technique, and demotes `intended_path` length to an upper bound.

### New Capabilities

- None.

## Impact

- **Code**: new `src/domain/design/mechanical_transforms.py` (Layer 2:
  mechanical-transform set + `is_mechanical_transform` + `collapse_mechanical_chain`);
  `src/domain/design/difficulty.py` (rubric `intended_path_min` removal;
  `_count_techniques` delegates collapse to the Layer 2 module),
  `prompts/design_planner_prompt.md`,
  `skills/design-challenges/references/category-tactics.md`,
  `skills/design-challenges/references/difficulty-rubric.md`.
- **Database**: none.
- **Dependency**: depends on the Layer 1 `src/domain/design/technique_taxonomy.py`
  introduced by `add-design-technique-diversity`; land that change's foundation
  first.
- **Compatibility**: existing designs validated under the old floors are
  unaffected (the change only relaxes a lower bound and refines technique
  counting; nothing that previously passed becomes invalid). The
  `legacy_grandfather` path in `validate_difficulty_alignment` is retained.
- **Out of scope**: diversity allocation, plan checkpoint, shard-prompt split.
