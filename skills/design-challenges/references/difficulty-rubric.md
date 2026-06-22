# Difficulty Rubric

Machine-checked rubric for the four difficulty tiers. The validator enforces the counts in this table on every accepted design. Use the qualitative notes when drafting the `prompt`, `scenario`, `intended_path`, and `novelty` fields.

## Tiers

| Tier | Techniques (考点) | Intended path steps | implementation_plan | Business scenario | Novelty field |
| --- | --- | --- | --- | --- | --- |
| easy   | exactly **1** | 1–3 | optional | optional (toy service OK) | not required |
| medium | **2 or 3** | 2–5 | optional | **required** (believable feature) | not required |
| hard   | **3 or 4** | 3–7 | **required** (intent-level) | **required** | not required |
| expert | **≥ 2** | 4–10 | **required** (show how steps chain) | **required** | **required** — describe the 0day-style trick |

**Counting technique slots:** the validator unions `techniques`, `primary_technique`, and `secondary_technique`, strips whitespace, deduplicates case-insensitively, and counts the distinct strings.

**Counting intended_path steps:** the validator counts non-empty string entries in `intended_path`.

**Business scenario heuristic:** for medium and harder, the `prompt` field must be at least 60 characters and the parent task `scenario` field must be non-empty. A one-line prompt like "find the SQLi and read the flag" is rejected as too thin to carry business context.

**Novelty heuristic for expert:** the `novelty` field must be at least 40 characters and explicitly identify the unusual element — for example: "Parser differential between two HTTP smuggling implementations chained with a custom pickle gadget", or "Custom JWS algorithm-confusion via unregistered alg=none-like state in an in-house library". Generic statements like "advanced exploitation" are rejected.

## Qualitative Guidance

### easy

- One observable bug, one well-known primitive.
- Player finds the bug from the surface of the app within the first few minutes.
- Solve is mechanical once the primitive is identified.
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
- `intended_path` — one step per significant observation/action; do not pad with filler.
- `implementation_plan` — required for hard/expert; describe vulnerability location, flag handling, and what forces the intended chain.
- `novelty` — required for expert; describe what is non-trivial in 1–3 sentences.

If the validator rejects with a difficulty-alignment error, do not edit the validator to pass — revise the design to meet the tier, or downgrade the difficulty.
