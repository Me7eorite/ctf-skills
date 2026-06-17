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

Use this skill to design organizer-ready CTF challenges, not to attack third-party systems. Keep every target synthetic, isolated, reproducible, and explicitly authorized for competition or training.

Prefer quality over raw volume. A strong challenge has a clear learning objective, fair discovery path, reliable reference solve, isolated deployment model, staged hints, and a validation plan that another author can execute.

## Operating Mode

1. Collect or infer the event brief.
2. Build a compact challenge matrix before writing full specs.
3. Select techniques from the bundled design playbooks and, when useful, the `ctf-*` material directories.
4. Expand only the requested slice into full challenge specifications.
5. Run the quality gate before finalizing.
6. Produce the requested format: Markdown pack, author tickets, JSON/YAML matrix, CTFd draft, or repo scaffold plan.

When the user asks for very large batches, work in shards. Generate a matrix first, then expand by category, difficulty, or ID range. Do not try to fully specify hundreds of challenges in one pass.

## Inputs

If details are missing, infer conservative defaults and state them briefly.

- Event name, theme, audience, and expected player level
- Target categories, especially web, pwn, reverse, or mixed tracks
- Difficulty mix: easy, medium, hard, expert
- Competition length, team size, and scoring model
- Platform constraints: CTFd, kCTF, Docker Compose, static files, HTTP services, TCP services
- Flag format and per-team/static flag requirements
- Deliverable format and desired level of detail
- Safety constraints: local-only, no real credentials, no production targets, no live malware

Default assumptions:

- Audience: intermediate university or club CTF players
- Difficulty mix: 40% easy, 35% medium, 20% hard, 5% expert
- Flag format: `flag{...}`
- Deployment: static files or isolated Docker services
- Output: Markdown challenge pack plus a compact matrix

## Reference Map

Read only the files needed for the current request.

- [references/glm5-generation.md](references/glm5-generation.md): use for GLM-5 prompt structure, sharding, self-review, and batch generation.
- [references/delivery-format.md](references/delivery-format.md): mandatory format for generated challenge deliverables, package naming, directories, EXP packages, PDF reports, deploy source directories, Docker tar files, and Excel overview fields.
- [references/spec-template.md](references/spec-template.md): use when writing full challenge specs or author tickets.
- [references/quality-gate.md](references/quality-gate.md): use before finalizing any challenge pack.
- [references/cve-advisory-design.md](references/cve-advisory-design.md): use when converting GitHub Advisory Database, GHSA, or CVE patterns into safe CTF challenge designs.
- [references/web-design.md](references/web-design.md): use for web challenge design patterns.
- [references/pwn-design.md](references/pwn-design.md): use for pwn challenge design patterns.
- [references/reverse-design.md](references/reverse-design.md): use for reverse engineering challenge design patterns.

Use the bundled category references for authoring guidance. Use `ctf-*` directories as deeper material catalogs when a design needs more technique variety or realism:

| Category | Primary Authoring Reference | Optional Material Catalog | Use For |
| --- | --- | --- | --- |
| Web | [references/web-design.md](references/web-design.md) | `/skills/ctf-web` | HTTP apps, APIs, auth flows, browser bot tasks, parser mismatches |
| Pwn | [references/pwn-design.md](references/pwn-design.md) | `/skills/ctf-pwn` | Native services, memory corruption, mitigations, exploit stability |
| Reverse | [references/reverse-design.md](references/reverse-design.md) | `/skills/ctf-reverse` | Crackmes, VMs, obfuscation, mobile/WASM/firmware-style artifacts |
| Crypto | [references/other-categories.md](references/other-categories.md) | `/skills/ctf-crypto` | Broken schemes, oracles, math-heavy puzzles |
| Forensics | [references/other-categories.md](references/other-categories.md) | `/skills/ctf-forensics` | Disk/memory artifacts, log analysis, hidden data |
| Misc | [references/other-categories.md](references/other-categories.md) | `/skills/ctf-misc` | OSINT, steganography, esoteric formats |

## Output Shape

When the user requests machine-readable output or the pipeline requires JSON, return a single JSON object:

```json
{
  "event": {
    "name": "string",
    "theme": "string",
    "audience": "string",
    "flag_format": "flag{...}"
  },
  "challenges": [
    {
      "id": "web-0001",
      "title": "string",
      "category": "web|pwn|reverse|crypto|forensics|misc",
      "difficulty": "easy|medium|hard|expert",
      "points": 100,
      "techniques": ["primary", "secondary"],
      "learning_objective": "string",
      "deployment": "docker|static|tcp|http",
      "port": 8080,
      "player_prompt": "string",
      "intended_path": ["step1", "step2", "step3"],
      "artifacts": [
        "README.md",
        "metadata.json",
        "validate.sh",
        "deploy/Dockerfile",
        "deploy/docker-compose.yml",
        "deploy/src/app.py",
        "deploy/_files/start.sh",
        "writenup/wp.md",
        "writenup/exp.py"
      ],
      "flag_plan": {
        "format": "flag{...}",
        "location": "string",
        "generation": "static|seeded|per-team"
      },
      "validation": {
        "reference_solve": "string",
        "expected_result": "string",
        "regression_checks": ["check1", "check2"]
      },
      "hints": ["hint1", "hint2", "hint3"],
      "delivery": {
        "exp_package": "js-web-challenge_nameexp.zip",
        "docker_config": "js-web-challenge_name.zip",
        "attachment": "js-web-challenge_name.zip",
        "pdf_report": "js-web-challenge_name.pdf",
        "docker_tar": "challenge_name[8080]-YYYYMMDD.tar"
      }
    }
  ]
}
```

## Quick Modes

| Request | Action |
| --- | --- |
| "I need a web challenge about X" | Read web-design.md, generate one spec with full metadata |
| "Generate 10 pwn challenges" | Read pwn-design.md, produce a matrix, ask to expand slice |
| "Convert this CVE to a CTF" | Read cve-advisory-design.md, produce a spec with references |
| "Check these challenges" | Run quality-gate.md checklist, report gaps |
| "Package for CTFd" | Read delivery-format.md, produce zip structure |

## Safety Guardrails

Never design challenges that:

- Attack real third-party systems, domains, or services
- Require player access to production infrastructure
- Embed real credentials, API keys, or secrets
- Include live malware or exploit payloads
- Target specific real-world organizations
- Require social engineering against real people

Always design challenges that:

- Run in isolated, disposable containers or offline environments
- Use synthetic data, fictional organizations, and seeded credentials
- Have documented reset and health-check procedures
- Include a reference solve that works without external dependencies
- Can be validated by another organizer without special knowledge
