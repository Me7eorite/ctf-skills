# GLM-5 Generation Workflow

Use this reference when a model must generate many CTF challenge designs consistently.

## Prompt Contract

Give GLM-5 a narrow role and an explicit artifact target:

```text
You are designing authorized synthetic CTF challenges.
Create only organizer-facing challenge designs.
Do not use real targets, real credentials, or real personal data.
First produce a matrix. Do not expand full specs until asked.
Categories: web, pwn, reverse.
Audience: intermediate.
Output: Markdown table plus notes.
```

For full specs, add:

```text
Expand only IDs web-01 through web-05.
Use the exact spec template.
For each challenge include validation, hints, anti-frustration checks, and a distinct learning objective.
```

## Sharding

Split large requests into small, reviewable units:

- Category shard: `web`, `pwn`, `reverse`
- Difficulty shard: `easy`, `medium`, `hard`, `expert`
- Technique shard: `auth`, `injection`, `heap`, `ROP`, `VM`, `anti-debug`
- Expansion shard: 5-15 full specs at a time

Recommended sequence:

1. Generate event assumptions.
2. Generate a coverage grid.
3. Generate a matrix for one shard.
4. Review for duplicates and unfairness.
5. Expand a small ID range.
6. Run a critic pass and revise.

## Self-Review Prompt

Use this critic prompt after each shard:

```text
Review these CTF challenge designs for fairness, duplication, safety, deployment reliability, and clarity.
Return only:
1. Blockers
2. Weak or duplicate designs
3. Missing validation
4. Suggested replacements
5. Revised matrix rows
```

## Deduplication Rules

Reject or revise a design when:

- The same primary trick appears repeatedly with only a changed story.
- The intended path depends on guessing a hidden endpoint, magic string, or arbitrary tool.
- The flag location is unrelated to the vulnerability.
- A hard challenge is hard only because the artifact is huge, noisy, or under-specified.
- A pwn challenge requires unreliable brute force without bounding attempts.
- A web challenge requires attacking a real external domain or service.
- A reverse challenge has no deterministic verification route.

## Temperature Guidance

Use lower creativity for matrices and higher creativity for themes:

- Matrix generation: low to medium creativity, strict schema.
- Naming and story prompts: medium creativity.
- Expert challenge ideation: medium creativity plus strict critic pass.
- Final specs: low creativity, exact template, no missing fields.
