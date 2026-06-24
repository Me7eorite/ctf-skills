# Difficulty Rubric

Machine-checked rubric for the four difficulty tiers. The validator enforces the technique counts, `intended_path` upper bounds, buildability caps, business-scenario requirement, and expert novelty requirement on every accepted design. Use the qualitative notes when drafting the `prompt`, `scenario`, `intended_path`, and `novelty` fields.

## Tiers

| Tier | Techniques (考点) | Intended path step cap | implementation_plan | max explicit components | LOC budget (build) | Business scenario | Novelty field | Solution uniqueness |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| easy   | exactly **1** | ≤ 4 | optional | ≤ 5  | ≤ 200  | optional (toy service OK) | not required | multiple paths OK (`unintended_solutions` optional) |
| medium | **2 or 3** | ≤ 5 | optional | ≤ 7  | ≤ 400  | **required** | not required | **single intended path** — `unintended_solutions` required |
| hard   | **3 or 4** | ≤ 7 | **required** | ≤ 10 | ≤ 700  | **required** | not required | **single intended path** — `unintended_solutions` required |
| expert | **≥ 2** | ≤ 10 | **required** | ≤ 15 | ≤ 1200 | **required** | **required** — describe the 0day-style trick | **single intended path** — `unintended_solutions` required |

**Counting technique slots:** the validator gathers `techniques`, `primary_technique`, and `secondary_technique`, normalizes each label to a canonical sub-technique, folds mechanical decode/unwrap transforms order-free, and counts distinct non-mechanical 考点. If every declared technique is mechanical, the whole decode/unwrap chain counts as exactly one `encoding` 考点. Mechanical transforms add nothing when a real non-mechanical technique is present.

**考点 (distinct technique) ≠ 解题步骤 (mechanical step):** difficulty is driven by the count of distinct 考点 plus, for expert, novelty. It is NOT driven by the number of solve steps. A linear decode/unwrap chain is one technique regardless of length: `strings→base64→flag` and `IDA→xor→base64→flag` are both easy when the declared techniques are only mechanical transforms.

**Counting intended_path steps:** the validator counts non-empty string entries in `intended_path` only as an upper-bound sanity check. Do not pad `intended_path` to meet a tier; there is no per-tier minimum.

**Business scenario heuristic:** for medium and harder, the `prompt` field must be at least 60 characters and the parent task `scenario` field must be non-empty. A one-line prompt like "find the SQLi and read the flag" is rejected as too thin to carry business context.

**Solution uniqueness (medium and harder):** these tiers MUST have a single intended solve path. The validator requires a non-empty `unintended_solutions` array — each entry names one alternate/unintended solution you considered and how the design closes it (mitigation, constraint, or removed primitive). Examples: "one-gadget RCE — blocked by a seccomp filter denying `execve`", "flag dumped via `strings` — flag is XOR-encoded in `.data` and reconstructed at runtime", "auth bypass via default creds — seeded accounts use random passwords". `easy` may omit the field and allow multiple paths. Note: this enforces that you *considered and documented* uniqueness; it cannot prove no other solution exists, so still sanity-check during the reference solve.

**Novelty heuristic for expert:** the `novelty` field must be at least 40 characters and explicitly identify the unusual element — for example: "Parser differential between two HTTP smuggling implementations chained with a custom pickle gadget", or "Custom JWS algorithm-confusion via unregistered alg=none-like state in an in-house library". Generic statements like "advanced exploitation" are rejected.

**Buildability budget:** only entries in the optional `implementation_plan.components` array count as build/deploy components. Descriptive top-level fields such as `runtime`, `framework`, `entrypoints`, and `flag_handling` are metadata and do not count toward the cap. The LOC budget is guidance for the build agent — it is not validator-enforced. If a design needs more real components or LOC than its tier allows, simplify or split it; otherwise upgrade the difficulty tier.

## Qualitative Guidance

### easy

- One observable bug, one well-known primitive, or one mechanical decode/unwrap chain.
- Player finds the bug from the surface of the app within the first few minutes.
- Solve is mechanical once the primitive is identified; extra decode layers do not raise the tier by themselves.
- Typical 5–20 min solve.

### medium

- 2–3 considered points chained inside a believable product context (notes app, ticket system, file converter, internal admin panel...).
- At least one step requires non-trivial reasoning (auth flow, parser quirk, configuration leak).
- Typical 20–60 min solve.

### hard

- 3–4 考点 chained across the path.
- At least one step must constrain or bypass a mitigation, not just stack two easy primitives.
- The `implementation_plan` MUST show the vulnerability surface, the flag-handling, and the constraints that force the intended chain. This is the build-phase agent's contract.
- Typical 1–3 h solve.

### expert

- Non-trivial mechanic at the core: 0day-style technique, custom protocol, parser differential, novel chain, or unusual constraint set.
- `novelty` field is the single source of truth for *what* makes this challenge non-trivial. Without it the design is rejected even if all other counts pass.
- Use sparingly: 5% of an event mix is plenty.
- Typical 3+ h solve. Solvers should leave having learned something they cannot find in a textbook.

## What to Write Where

When generating a design, populate these fields with the rubric in mind:

- `techniques` — list every 考点 you count toward the tier minimum.
- `primary_technique` — the headline technique (one of the entries in `techniques`).
- `secondary_technique` — present iff your count is ≥ 2.
- `intended_path` — one step per significant observation/action; do not pad with filler and do not use step count to justify a higher tier.
- `implementation_plan` — required for hard/expert; describe vulnerability location, flag handling, and what forces the intended chain. When useful, add `components` as an array naming only independently buildable or deployable units.
- `novelty` — required for expert; describe what is non-trivial in 1–3 sentences.
- `unintended_solutions` — required for medium/hard/expert; list each considered alternate solution and how the design blocks it. Optional for easy.
- `asset_flow` — required for medium (≥1 effective transition) and hard (≥2); each stage produces a concrete `produced_asset_or_capability` the next stage needs (`why_next_stage_requires_it`). The flag must not be reachable while skipping the chain. Optional/direct for easy.
- `actual_solution_type` — required for medium/hard/expert; the real solve type(s), which MUST exercise the nominal technique and MUST NOT be a generic **collapse shortcut** for the category (re: `static_xor_decrypt`, `direct_run_get_flag`, `strings_plaintext_flag`, `hardcoded_license`; web: `default_credentials`, `exposed_flag_route`, `source_code_leak`, `backup_file_leak`, `unrelated_sqli/lfi`; pwn: `unintended_win_function`, `direct_shellcode`, `direct_stack_ret`, `one_gadget_shortcut`). A design whose declared solve IS a shortcut is "collapsed" and rejected.

If the validator rejects with a difficulty-alignment error, revise or split the design to meet the tier, or upgrade it to the appropriate difficulty.
