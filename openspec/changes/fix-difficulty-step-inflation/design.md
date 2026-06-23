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
- Do not re-grade or invalidate historical stored designs; stricter counting may
  reject new designs that relied on mechanical-step padding.

**Non-Goals**

- Re-grading or migrating historical designs.
- Diversity allocation / dedup across a batch (sibling change).
- Building or modifying `technique_taxonomy.py` — `resolve_sub_technique` +
  normalization already exist (shipped by `add-design-technique-diversity`); this
  change only consumes them.

## Decisions

### D1 — Order-free mechanical fold; mechanical-ness is a label property (decision A)

The "consecutive" framing was unimplementable: `_count_techniques` treats
techniques as an unordered set, and there is no ordering source (nor a reliable
way to know at count time whether an `xor` key was recovered earlier). Rather
than introduce an ordering source (`intended_path` is prose; a new
`technique_chain` field expands the design contract), we make the fold
**order-free** and push the analysis-vs-mechanical judgement onto the technique
**label**, which the author already writes.

`_count_techniques` SHALL route each declared technique through
`resolve_sub_technique`, then:

- a technique is **mechanical** iff its `sub_technique` ∈ `MECHANICAL_TRANSFORMS`
  (`base64`, `base32`, `hex`, `url`, `rot`, `caesar`, `xor`, `gzip`, `zlib`,
  `strings`, …) — judged from the label alone, no order, no key-state inference;
- the counted total = the number of distinct **non-mechanical** sub_techniques;
- when **every** technique is mechanical the total is exactly `1` (a pure
  decode/unwrap chain is one `encoding` 考点);
- mechanical transforms contribute **0** when at least one non-mechanical
  technique is present (they are "free").

The folded class is named `mechanical_class` (value `encoding`), **not
`family`** — see D4. A genuine analysis step that resembles a transform is kept
by labelling it as such (`xor key recovery`, not bare `xor`); that label
normalizes to a non-mechanical sub_technique. The prompts teach this.

Worked cases: `xor`+`base64` → all mechanical → `1`; `sqli`+`base64` →
`{sqli}` → `1` (base64 free); `xor key recovery`+`logic flaw` → `2`.

Implementation detail: the already-shipped `resolve_sub_technique` reads a
`label` field from a mapping/object. Since `difficulty.py` gathers plain strings
from challenge fields, it MUST call it as `resolve_sub_technique({"label": value})`
or through an equivalent local adapter. Passing a raw string would normalize to
`unknown` and collapse unrelated techniques incorrectly.

The three concerns are deliberately layered into three modules so no single
file carries 分类 + 难度 + 折叠规则:

- **Layer 1 — `technique_taxonomy.py`** (**already shipped** by
  `add-design-technique-diversity`): `resolve_sub_technique` + normalization +
  the conservatism guard. This change consumes it as-is and does NOT create or
  modify it.
- **Layer 2 — `mechanical_transforms.py`** (**new in this change**): the
  mechanical-transform set, `is_mechanical_transform(sub_technique) -> bool`, and
  the `mechanical_class` constant `encoding`. **Order-free — no chain/sequence
  API.** Depends on Layer 1's sub-technique keys; knows nothing about the rubric.
- **Layer 3 — `difficulty.py`**: rubric + alignment; `_count_techniques` uses
  Layer 2's predicate to fold, then applies the rubric.

Dependency direction is a clean DAG (L3 → L2 → L1); Layer 1 imports neither of
the others. Because Layer 1 already exists in the codebase, this change's net
work is just the L2 module + the `difficulty.py` rewrite + prose, so it is small
and ready to implement now.

### D4 — Name the folded class `mechanical_class`, not `family`

`add-design-technique-diversity` already uses `family` for the research lane /
governance axis (a different taxonomy — its `re` lanes are
`crackme|vm_bytecode|…`, with no `encoding` lane at all). Reusing `family` here
for the mechanical fold would collide in the implementers' heads while both
changes are in flight. This change therefore names its concept
`mechanical_class` (single value `encoding`). The two taxonomies never share a
symbol.

### D2 — `intended_path` length is an upper bound only

The per-tier `intended_path_min` floors SHALL be removed (equivalently, set to
`1` for all tiers). `intended_path_max` is retained to flag runaway
walkthroughs. The difficulty driver becomes `techniques_min/max` plus, for
expert, `needs_novelty`. This removes the incentive to pad a solve with steps to
clear a floor, and it means Q2 (xor→base64→flag) — one 考点 after D1 — can no
longer be promoted to hard by step count.

The rendered Build Budget in `src/services/design_prompt.py` MUST match this
semantics: it should present `intended_path` as an upper bound (for example
`≤ intended_path_max`) rather than a min-max range. Otherwise the generated
design prompt would continue teaching a step floor even after the validator
stops enforcing one.

### D3 — Prose de-couples steps from difficulty

`category-tactics.md` and `difficulty-rubric.md` SHALL define
"**考点 (distinct technique) ≠ 解题步骤 (mechanical step)**" and carry the worked
counter-example: `strings→base64→flag` and `IDA→xor→base64→flag` are **both
easy** (one decode 考点 each); an extra decode layer does not raise difficulty.
`design_planner_prompt.md` SHALL add: "Difficulty is driven by the count of
distinct 考点 + novelty, NOT by the number of solve steps. A linear
decode/unwrap chain is ONE technique regardless of length."

## Risks / Trade-offs

- **Under-counting a real reversing step.** If an author legitimately uses `xor`
  as a non-trivial standalone reversing step (key must be derived), the
  order-free fold would mis-classify a bare `xor` label as mechanical and
  under-count. Mitigated by the label convention: such a step is labelled as the
  analysis it is (`xor key recovery`), which normalizes to a non-mechanical
  sub_technique and counts. The prompts teach this; the risk is a labelling
  discipline, not a logic gap, and it fails safe toward the author's stated
  intent.
- **Stricter counting can fail a newly submitted previously-padded design.** The fold counts
  more strictly than the old set-union, so a design that met a technique floor by
  listing mechanical transforms as separate techniques now counts fewer 考点 and
  may fail if submitted after this change. This is intended (those were
  mis-graded). Only *new* design attempts are affected; stored designs are
  grandfathered.
- **Relaxing floors could let a thin design claim a high tier.** It cannot: the
  technique *minimums* (`techniques_min`) and expert `needs_novelty` remain, so
  a single-考点 design still fails hard/expert on the technique axis. Only the
  step floor — the wrong signal — is removed.
- **Cross-layer coupling.** Mechanical classification depends on the exact output
  of the already-shipped `resolve_sub_technique` (e.g. its qualifier list keeps
  `key`/`recovery`, so `xor key recovery` normalizes to a non-mechanical key).
  This L2-on-L1 coupling is pinned by a cross-layer assertion test in this change
  (`resolve_sub_technique("xor key recovery") ∉ MECHANICAL_TRANSFORMS`). Anyone
  later changing the qualifier list / alias map in `technique_taxonomy.py` MUST
  re-run both that assertion and the diversity change's must-stay-distinct guard.
- **String adapter footgun.** `resolve_sub_technique` consumes mapping/object
  labels, while `_count_techniques` starts from strings. This change pins the
  adapter contract and tests it so raw strings do not all collapse to `unknown`.

## Migration

No schema change. `validate_difficulty_alignment` keeps its `legacy_grandfather`
escape hatch and runs only on new design attempts, so no stored design is
re-validated. The `intended_path_min` relaxation only loosens and never
invalidates a prior pass; the order-free mechanical fold may legitimately reject
a *new* design that relied on mechanical-step padding (intended), but historical
rows are untouched.
