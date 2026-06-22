---
name: design-challenges
description: Designs high-quality CTF competition challenges and challenge packs from organizer requirements, especially for web, pwn, and reverse engineering. Use when Codex or GLM-5 needs to create challenge ideas, full challenge specs, author tickets, CTFd drafts, balanced category plans, validation checklists, or batch-generation workflows for authorized synthetic CTF/training environments.
license: MIT
compatibility: Requires a filesystem-based agent such as Codex, GLM-5, or similar, with Markdown editing support. Uses bundled design references plus the repository's ctf-* material directories as optional technique catalogs.
allowed-tools: Bash Read Write Edit Glob Grep Task WebFetch WebSearch Skill
metadata:
  user-invocable: "true"
  argument-hint: "[event brief] [categories] [difficulty mix] [output format]"
---

# CTF Challenge Designer

Design organizer-ready CTF challenges. Targets must be synthetic, isolated, reproducible, and explicitly authorized for competition or training.

Prefer quality over volume. A strong challenge has a clear learning objective, fair discovery path, reliable reference solve, isolated deployment, staged hints, and a validation plan another author can execute.

## Operating Mode

1. Collect or infer the event brief.
2. Build a compact challenge matrix before writing full specs.
3. Select techniques from `category-tactics.md` and, when useful, the `ctf-*` material directories.
4. Expand only the requested slice into full challenge specifications.
5. Run the quality gate from `design-core.md` before finalizing.
6. Produce the requested format: Markdown pack, author tickets, JSON matrix, CTFd draft, or repo scaffold plan.

For large batches, work in shards. Generate a matrix first, then expand by category, difficulty, or ID range. Do not fully specify hundreds of challenges in one pass.

## Inputs

If details are missing, infer conservative defaults and state them briefly.

- Event name, theme, audience, expected player level.
- Target categories (web, pwn, reverse, mixed).
- Difficulty mix: easy, medium, hard, expert.
- Competition length, team size, scoring model.
- Platform constraints: CTFd, kCTF, Docker Compose, static files, HTTP/TCP services.
- Flag format and per-team/static flag requirements.
- Deliverable format and level of detail.
- Safety constraints: local-only, no real credentials, no production targets, no live malware.

Default assumptions:

- Audience: intermediate university or club CTF players.
- Difficulty mix: 40% easy, 35% medium, 20% hard, 5% expert.
- Flag format: `flag{...}`.
- Deployment: static files or isolated Docker services.
- Output: Markdown challenge pack plus a compact matrix.

## Reference Map

Read only the files needed for the current request.

- [references/design-core.md](references/design-core.md) — output shape (JSON), spec template, quality gate, safety, fairness, revision actions. **Always read before finalizing.**
- [references/category-tactics.md](references/category-tactics.md) — technique lanes (Easy/Medium/Hard/Expert), design seeds, anti-patterns, and container conventions for web, pwn, reverse, crypto, forensics, misc, OSINT, malware-themed, AI/ML.
- [references/cve-pivot.md](references/cve-pivot.md) — read only when converting a CVE / GHSA advisory into a CTF design.

Optional deeper material catalogs (only when a design needs more technique variety or realism):

| Category | Material Catalog |
| --- | --- |
| Web | `/skills/ctf-web` |
| Pwn | `/skills/ctf-pwn` |
| Reverse | `/skills/ctf-reverse` |
| Crypto | `/skills/ctf-crypto` |
| Forensics | `/skills/ctf-forensics` |
| Misc | `/skills/ctf-misc` |

## Quick Modes

| Request | Action |
| --- | --- |
| "I need a web challenge about X" | Read design-core + category-tactics (Web), produce one spec |
| "Generate 10 pwn challenges" | Read design-core + category-tactics (Pwn), produce a matrix first, then expand a slice |
| "Convert this CVE to a CTF" | Read cve-pivot.md, then design-core + category-tactics |
| "Check these challenges" | Run the quality gate from design-core.md, report gaps |
| "Package for CTFd" | The delivery format lives under `docs/delivery-formats/ctf-v2/` — read it there |

## Safety Guardrails

Never design challenges that:

- Attack real third-party systems, domains, or services.
- Require player access to production infrastructure.
- Embed real credentials, API keys, or secrets.
- Include live malware or active exploit payloads.
- Target specific real-world organizations.
- Require social engineering against real people.

Always design challenges that:

- Run in isolated, disposable containers or offline environments.
- Use synthetic data, fictional organizations, seeded credentials.
- Have documented reset and health-check procedures.
- Include a reference solve that works without external dependencies.
- Can be validated by another organizer without special knowledge.
