## 0. Dependency

- [ ] 0.1 Confirm the Layer 1 `src/domain/design/technique_taxonomy.py` (from
      `add-design-technique-diversity`, task 1.1) is present; this change consumes
      its `resolve_family` / `resolve_sub_technique` from a new Layer 2 module.

## 1. Layer 2: mechanical-transform collapse

- [ ] 1.1 Create `src/domain/design/mechanical_transforms.py` (Layer 2) with
      `MECHANICAL_TRANSFORMS = {base64, base32, hex, url, rot, xor_known_key, gzip,
      zlib, strings, ...}`, `is_mechanical_transform(sub_technique) -> bool`, and
      `collapse_mechanical_chain(sub_techniques) -> list`. It imports Layer 1
      `technique_taxonomy` only; it MUST NOT import `difficulty.py` or know about
      the rubric.
- [ ] 1.2 Update `_count_techniques` in `src/domain/design/difficulty.py` to route
      declared techniques through `resolve_sub_technique` (Layer 1), delegate the
      consecutive-mechanical collapse to `collapse_mechanical_chain` (Layer 2), then
      count; non-mechanical techniques continue to count individually.
- [ ] 1.3 Tests: `["xor","base64"]` counts as 1 technique; `["sqli","xss"]` counts as 2;
      a `xor` described as a standalone key-recovery analysis step still counts as 1
      distinct technique (not collapsed away).

## 2. Demote intended_path length to an upper bound

- [ ] 2.1 In the `RUBRIC` table, remove the per-tier `intended_path_min` floor (set to
      `1` for every tier) while retaining `intended_path_max`.
- [ ] 2.2 Remove the `intended_path_min` violation check in
      `validate_difficulty_alignment` (keep the `intended_path_max` "trim filler" check).
- [ ] 2.3 Tests: a 3-step linear decode solve slotted as `easy` validates (no longer
      promoted/forced); a single-考点 design slotted as `hard` still FAILS on
      `techniques_min`, proving difficulty is technique-driven not step-driven; an expert
      design with one 考点 still fails (`needs_novelty` / `techniques_min` unaffected).

## 3. De-couple steps from difficulty in prose

- [ ] 3.1 Update `skills/design-challenges/references/difficulty-rubric.md` and
      `skills/design-challenges/references/category-tactics.md` to define
      "考点 (distinct technique) ≠ 解题步骤 (mechanical step)" and add the worked
      counter-example (`strings→base64→flag` and `IDA→xor→base64→flag` are both easy).
- [ ] 3.2 Add to `prompts/design_planner_prompt.md`: "Difficulty is driven by the count of
      distinct 考点 + novelty, NOT by the number of solve steps. A linear decode/unwrap
      chain is ONE technique regardless of length."
- [ ] 3.3 Verify the difficulty-rubric reference and the `difficulty.py` RUBRIC table stay
      in sync (the module docstring already asserts they mirror each other).

## 4. Regression guard

- [ ] 4.1 Add a regression test pinning the reporter's exact case: two `re` designs, one
      `strings→base64` and one `IDA→xor→base64`, both slotted `easy`, both validate, and
      `_count_techniques` returns 1 for each.
