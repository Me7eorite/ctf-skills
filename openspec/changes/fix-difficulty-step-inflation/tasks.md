## 0. Dependency (already satisfied)

- [x] 0.1 Confirm `src/domain/design/technique_taxonomy.py::resolve_sub_technique` (+ its
      normalization and conservatism guard) exists — it is **already shipped** by
      `add-design-technique-diversity` and tested in `tests/app/test_technique_taxonomy.py`.
      This change consumes it as-is and does NOT create or modify `technique_taxonomy.py`.

## 1. Layer 2: order-free mechanical fold

- [x] 1.1 Create `src/domain/design/mechanical_transforms.py` (Layer 2) with
      `MECHANICAL_TRANSFORMS = {base64, base32, hex, url, rot, caesar, xor, gzip, zlib, strings, ...}`,
      `is_mechanical_transform(sub_technique) -> bool`, and the `mechanical_class` constant
      `encoding`. It is **order-free**: NO chain/sequence API and NO key-state inference.
      Imports Layer 1 `technique_taxonomy` only; MUST NOT import `difficulty.py` or know the
      rubric. (Note: bare `xor` is mechanical; a genuine reversing step is labelled
      `xor key recovery`, which is NOT in the set.)
- [x] 1.2 Update `_count_techniques` in `src/domain/design/difficulty.py`: gather techniques
      from all existing sources (`techniques[]` + `primary_technique` + `secondary_technique`,
      as today), wrap each plain string as `{"label": value}` (or an equivalent adapter)
      before routing it through `resolve_sub_technique` (Layer 1), then count = number
      of distinct **non-mechanical** sub_techniques; when ALL are mechanical → return
      `1`; mechanical transforms contribute `0` when any non-mechanical technique is present.
- [x] 1.3 Tests: `["xor","base64"]` → 1; `["sqli","base64"]` → 1 (base64 free);
      `["sqli","xss"]` → 2; `["xor key recovery","logic flaw"]` → 2 (label keeps the
      analysis step). Order of the list does not affect the count. Cross-layer boundary
      assertion: `resolve_sub_technique("xor key recovery") ∉ MECHANICAL_TRANSFORMS` while
      `resolve_sub_technique("xor-decrypt") ∈ MECHANICAL_TRANSFORMS`. Also assert a design
      whose only declared technique is in `primary_technique` (empty `techniques[]`) is
      counted, proving the gather step did not drop primary/secondary.

## 2. Demote intended_path length to an upper bound

- [x] 2.1 In the `RUBRIC` table, remove the per-tier `intended_path_min` floor (set to
      `1` for every tier) while retaining `intended_path_max`.
- [x] 2.2 Remove the `intended_path_min` violation check in
      `validate_difficulty_alignment` (keep the `intended_path_max` "trim filler" check).
- [x] 2.3 Tests: a 3-step linear decode solve slotted as `easy` validates (no longer
      promoted/forced); a hard-tier design with enough distinct 考点 but a one-step
      `intended_path` validates, proving the lower floor is gone; a single-考点 design
      slotted as `hard` still FAILS on `techniques_min`, proving difficulty is
      technique-driven not step-driven; an expert design with one 考点 still fails
      (`needs_novelty` / `techniques_min` unaffected).
- [x] 2.4 Update `src/services/design_prompt.py::_render_build_budget` so rendered
      prompts describe `intended_path` as an upper-bound budget (`≤ intended_path_max`)
      rather than a min-max range, and update prompt-rendering tests accordingly.

## 3. De-couple steps from difficulty in prose

- [x] 3.1 Update `skills/design-challenges/references/difficulty-rubric.md` and
      `skills/design-challenges/references/category-tactics.md` to define
      "考点 (distinct technique) ≠ 解题步骤 (mechanical step)" and add the worked
      counter-example (`strings→base64→flag` and `IDA→xor→base64→flag` are both easy).
- [x] 3.2 Add to `prompts/design_planner_prompt.md`: "Difficulty is driven by the count of
      distinct 考点 + novelty, NOT by the number of solve steps. A linear decode/unwrap
      chain is ONE technique regardless of length."
- [x] 3.3 Verify the difficulty-rubric reference, the `difficulty.py` RUBRIC table, and the
      rendered Build Budget prompt stay in sync (the module docstring already asserts the
      reference and table mirror each other).

## 4. Regression guard

- [x] 4.1 Add a regression test pinning the reporter's exact case: two `re` designs, one
      `strings→base64` and one `IDA→xor→base64`, both slotted `easy`, both validate, and
      `_count_techniques` returns 1 for each.
