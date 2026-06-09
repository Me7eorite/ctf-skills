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
| Forensics | [references/other-categories.md](references/other-categories.md) | `/skills/ctf-forensics` | PCAPs, disk images, memory, stego, timelines |
| Misc | [references/other-categories.md](references/other-categories.md) | `/skills/ctf-misc` | Jails, encodings, games, esolangs, constraint puzzles |
| OSINT | [references/other-categories.md](references/other-categories.md) | `/skills/ctf-osint` | Fictional personas, geolocation, DNS, public-source trails |
| Malware | [references/other-categories.md](references/other-categories.md) | `/skills/ctf-malware` | Inert simulated malware, config extraction, toy C2 traffic |
| AI/ML | [references/other-categories.md](references/other-categories.md) | `/skills/ctf-ai-ml` | Prompt injection, toy model attacks, adversarial examples |

Treat the `ctf-*` directories as source material, not as a command to solve a live challenge. Convert techniques into safe toy designs, add validation, and remove exploit-only assumptions.

## Challenge Matrix

Create this matrix before writing full specs:

| ID | Title | Category | Difficulty | Points | Deployment | Primary Technique | Artifact | Learning Objective | Risk |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| web-01 | Example | web | easy | 100 | http/docker | auth bypass | toy app | Inspect trust boundaries | low |

For each row, include:

- Spoiler-free player prompt
- Intended solve outline in 2-4 steps
- Flag location and generation rule
- Authoring effort and validation method
- The reason this challenge is distinct from nearby challenges

## Difficulty Calibration

Use difficulty to describe solver burden, not author pride.

- Easy: one core idea, short path, low tooling friction, forgiving hints.
- Medium: two linked ideas, clear artifacts, some scripting or debugging.
- Hard: multi-stage reasoning, careful implementation, meaningful false paths removed.
- Expert: rare, reliable reference solve required, deep specialization or chained domains.

Suggested static points:

- Easy: 100-150
- Medium: 200-300
- Hard: 350-450
- Expert: 500

## GLM-5 Workflow

When using GLM-5 or another agent to generate many designs, use [references/glm5-generation.md](references/glm5-generation.md) and enforce this loop:

1. Planner pass: produce event assumptions, coverage grid, and generation shards.
2. Matrix pass: produce challenge rows only.
3. Expansion pass: expand one shard into full specs using [references/spec-template.md](references/spec-template.md).
4. Critic pass: apply [references/quality-gate.md](references/quality-gate.md) and revise weak entries.
5. Deduplication pass: remove repeated tricks, near-duplicate prompts, and unfair leaps.

Do not let a generation pass invent real targets, real credentials, real people, or production-like attack instructions outside a local toy service.

## Output Rules

For a brief, return the event assumptions, matrix, and quality notes.

For full specs, use the template in [references/spec-template.md].

For deliverable-ready challenge packages, always apply [references/delivery-format.md](references/delivery-format.md). The external source of truth is `delivery-spec/交付格式规范.md` at the repo root (sample layout under `delivery-spec/资源包/`); this bundled reference summarizes it for the skill.

For containerized challenges, include these implementation requirements in
the author ticket or generated prompt:

- Keep `docker-compose.yml` to exactly one service.
- Inject the challenge flag through the service's `environment` as
  `FLAG: ${FLAG}`; validation or orchestration sets the host-side `FLAG`
  before Compose starts. Application code must read `FLAG` at runtime instead
  of baking the plaintext flag into the Compose file, image, source tree, or
  player attachments.
- Set both Compose `image` and `container_name` from the challenge name. Convert
  it to a stable Docker-safe lowercase identifier using only
  `[a-z0-9][a-z0-9_.-]`; use the same identifier in build, validation, metadata,
  and delivery commands.
- Apply least privilege to Web and Pwn images by default. Pwn images normally
  create an unprivileged `ctf` user/group, use `/home/ctf`, and finish with
  `USER ctf`.
- Web images SHOULD reuse the non-root service user and conventional content
  directory supplied by the selected base image when available, such as
  `www-data` with `/var/www/html` for Apache/PHP or `tomcat` with the image's
  standard Tomcat application directory. Create `ctf` only when the base
  image has no appropriate service account. The final process must not run as
  root.
- Do not define Compose `volumes` for generated challenges. Copy source,
  configuration, startup assets, and required initial data into the image at
  build time. Create required writable directories inside the image with
  ownership assigned to the runtime user.
- Web services should listen on an unprivileged container port such as `8080`;
  Compose may map a requested host port such as `80` to that internal port.
  Do not grant a Linux capability merely to bind a low port.
- Root execution, added capabilities, privileged mode, device mounts, or
  writable system paths are allowed only when the intended challenge
  mechanism strictly requires them. Minimize the exception and record its
  reason in metadata, validation notes, and the writeup.
- When `apt` access is slow or unreliable in the target build environment,
  the Dockerfile may switch to an organizer-approved Debian/Ubuntu mirror
  before `apt-get update`. Keep the base distribution release unchanged,
  combine update/install/cleanup in one layer, and do not hardcode a regional
  mirror when the default upstream is reliable.

For author tickets, group tasks by category and include implementation, deployment, solve, and validation work.

For machine-readable output, use this JSON shape:

```json
{
  "event": {
    "name": "Example CTF",
    "theme": "Example theme",
    "flag_format": "flag{...}"
  },
  "challenges": [
    {
      "id": "web-01",
      "title": "Example Title",
      "category": "web",
      "difficulty": "easy",
      "points": 100,
      "deployment": "http/docker",
      "primary_technique": "auth bypass",
      "learning_objective": "Inspect authorization boundaries",
      "prompt": "Spoiler-free prompt",
      "artifacts": ["Dockerfile", "src/"],
      "flag_location": "/flag.txt",
      "validation": "python solve.py",
      "hints": ["gentle hint", "technique hint", "near-solution hint"]
    }
  ]
}
```

## Safety Rules

- Design only synthetic, organizer-owned targets.
- Use fictional users, domains, credentials, logs, and infrastructure.
- Keep web and pwn services isolated in disposable containers.
- Do not require scanning, exploiting, or social-engineering real third-party systems.
- Do not include real API keys, personal data, or live malware behavior.
- For OSINT-style elements, use fictional personas and organizer-controlled assets.
- For malware-themed elements, use inert samples or simulated traffic.

## Challenge

$ARGUMENTS
