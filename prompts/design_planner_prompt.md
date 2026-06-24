# Design Task Planner

You are pre-planning ONE challenge task before the full design is written. The
downstream design agent will receive your reply alongside the parent task row,
so your job is to lock the **techniques and the chain shape** for that one
task, not to enumerate every implementation detail.

## Inputs

- category: `{category}`
- difficulty: `{difficulty}`
- topic: `{topic}`
- primary_finding: `[{primary_kind}] {primary_label}` — {primary_summary}
- secondary_findings:
{secondary_block}
- MUST avoid reusing these sibling sub-techniques (pick a distinct 考点):
{avoid_techniques}

## Rubric Reminder

Difficulty is driven by the count of distinct 考点 + novelty, NOT by the
number of solve steps. A linear decode/unwrap chain is ONE technique regardless
of length.

For `medium` and harder, the `chain_outline` MUST converge on a SINGLE intended
solve path — design the chain so obvious alternate shortcuts are closed off.
`easy` may admit multiple solve paths.

| Difficulty | Techniques | Notes |
| --- | --- | --- |
| hard | exactly 3 or 4 | Chain across business steps, not just stack two basics |
| expert | ≥ 2 | Must identify a 0day-style trick or unusual constraint — written into `novelty_seed` |

## Output Contract

Reply with **a single JSON object** and nothing else. The first character of
your reply MUST be `{{` and the last MUST be `}}`. No markdown, no prose, no
file writes.

```json
{{
  "considered_techniques": ["string", "string", "string"],
  "chain_outline": "1-3 sentence outline of how the techniques chain together to reach the flag",
  "asset_flow": [
    {{
      "stage": 1,
      "player_input_or_capability": "what the player can do at this stage",
      "technique": "the technique applied here",
      "produced_asset_or_capability": "the concrete asset/state/permission/leak this stage yields",
      "why_next_stage_requires_it": "why the next stage cannot proceed without it"
    }}
  ],
  "scenario_seed": "1-2 sentence business scenario the player encounters — believable, not a toy",
  "novelty_seed": "EXPERT ONLY: 1-2 sentences identifying the non-trivial trick. Use null for hard."
}}
```

Rules:

- `considered_techniques` MUST contain 3 or 4 distinct techniques for `hard`
  and at least 2 for `expert`.
- `chain_outline` MUST describe how the techniques connect — not a list of
  independent steps.
- `asset_flow` MUST encode the required chain: `medium` needs ≥1 effective
  transition, `hard` ≥2. Each effective stage produces a concrete
  `produced_asset_or_capability` that the next stage depends on
  (`why_next_stage_requires_it`). `easy` MAY use a single direct stage. The
  flag MUST NOT be reachable while skipping the chain.
- `scenario_seed` MUST sound like a real product context (internal admin
  panel, customer-support tool, file-conversion service, ...). Avoid "a toy
  service that has SQL injection."
- For `expert`, `novelty_seed` MUST be substantive (≥ 40 chars). For `hard`,
  set it to `null`.
- Do NOT include code, Dockerfiles, exploit details, or per-file specs.
- Do NOT echo the inputs verbatim.
- MUST avoid reusing the listed sibling sub-techniques whenever a coherent
  alternative chain exists; only fall back to a reused 考点 if no distinct
  chain is supported by the findings.
