# Design Core

Single canonical reference for any organizer-ready challenge spec. Combines the spec template, quality gate, safety constraints, and the machine-readable output shape. Use with `category-tactics.md` for technique catalogs and `cve-pivot.md` when the source is a CVE or GHSA advisory.

## Output Shape (Machine-Readable)

When the pipeline requires JSON, reply with a single JSON object — no markdown, no prose, no file writes:

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
      "category": "web|pwn|re|crypto|forensics|misc|...",
      "difficulty": "easy|medium|hard|expert",
      "points": 100,
      "techniques": ["primary", "secondary"],
      "primary_technique": "string",
      "secondary_technique": "string",
      "learning_objective": "string",
      "deployment": "docker|static|tcp|http",
      "port": 8080,
      "prompt": "string (spoiler-free scoreboard prompt)",
      "flag_location": "string",
      "intended_path": ["step1", "step2", "step3"],
      "artifacts": [
        "README.md",
        "metadata.json",
        "validate.sh",
        "writenup/wp.md",
        "writenup/exp.py",
        "deploy/Dockerfile",
        "deploy/docker-compose.yml",
        "deploy/src/app.py",
        "deploy/_files/start.sh"
      ],
      "flag_plan": {
        "format": "flag{...}",
        "location": "string",
        "generation": "static|seeded|per-team"
      },
      "validation": "string describing reference solve, expected result, regression checks",
      "hints": ["hint1", "hint2", "hint3"],
      "implementation_plan": {
        "runtime": "string",
        "framework": "string",
        "service_model": "string",
        "entrypoints": "string",
        "data_model": "string",
        "vulnerability_location": "string",
        "flag_handling": "string",
        "constraints": "string"
      }
    }
  ]
}
```

Field rules:

- `prompt` is the scoreboard-facing prompt. Do not use `player_prompt`.
- `flag_location` is a string. Do not use `flag_plan.location` as the canonical field.
- `validation` is a single string. Do not nest it as an object.
- `hints` MUST contain exactly 3 entries, staged from gentle to direct.
- `intended_path` MUST be a list of step strings ordered observation → flag.
- Solution uniqueness is tiered. `easy` MAY admit multiple solve paths. `medium`, `hard`, and `expert` MUST have a **single intended solve path**: enumerate every alternate/unintended solution you considered and how the design closes it in a non-empty `unintended_solutions` array (e.g. "one-gadget RCE — blocked by seccomp denying execve", "flag readable via `strings` — flag is XOR-encoded and reconstructed at runtime"). This replaces the old generic "no unintended shortcut" note with an explicit, author-checkable contract.
- `artifacts` MUST be relative local paths (no URLs, no absolute paths, no `..` traversal). Use `writenup/wp.md` and `writenup/exp.py` — not `writeup/...` or bare `solve.py`.
- Native executables and conventional build files do not need extensions. Valid examples include `attachments/crackme` and `deploy/Makefile`. Put challenge source files under `src/` or `deploy/src/`.
- For Pwn container services, use native service artifacts under `deploy/src/` or `src/`. A small multi-file project is valid when each planned source/build artifact is listed, e.g. `deploy/src/src/main.c`, `deploy/src/lib/menu.c`, `deploy/src/include/menu.h`, `deploy/src/Makefile`, or `deploy/src/bin/challenge`; it is not limited to a single `deploy/src/vuln.c`. For default/native/binary/kernel pwn runtimes set `implementation_plan.service_user` exactly to `ctf`; include `deploy/_files/ctf.xinetd` whenever the service model or `runtime_profile` is xinetd/chroot/socket; declare the scaffold/template as `pwn/xinetd-chroot` so build uses `scaffolds/pwn/xinetd-chroot/`; do not use Web's Python `deploy/src/app.py` unless a Python wrapper is intentionally part of the design.
- `implementation_plan` is intent-level only. Never include code, Dockerfile bodies, compose YAML, SQL scripts, or exploit code.
- For `web` and `pwn`, `deployment` MUST include the substring `docker` and `port` MUST be set.
- `expert` difficulty requires a `novelty` field describing the non-trivial trick (0day-style, custom protocol, multi-stage chain, parser differential, etc.). See `category-tactics.md` for examples.

## Author-Facing Spec Template (Markdown)

Use when the user asks for a full organizer-facing spec instead of JSON:

```markdown
## <ID>. <Title>

- Category: <web|pwn|re|...>
- Difficulty: <easy|medium|hard|expert>
- Points: <number>
- Estimated solve time: <duration>
- Deployment: <static|download|docker|tcp|http>
- Primary technique: <technique>
- Secondary technique: <optional>
- Learning objective: <what players should learn>

### Player Prompt
<Spoiler-free prompt shown on the scoreboard.>

### Intended Path
1. <Initial observation>
2. <Core vulnerability or insight>
3. <Exploit / decode / validation step>
4. <Flag extraction>

### Artifacts
- README.md
- metadata.json
- validate.sh
- writenup/wp.md
- writenup/exp.py
- Containerized: deploy/Dockerfile, deploy/docker-compose.yml, deploy/src/..., deploy/_files/start.sh
- Player attachments when needed: attachments/... (legacy dist/ still accepted)

### Flag Plan
- Format: flag{...}
- Location: <file, DB row, service response>
- Generation rule: <static|seeded|per-team>

### Validation
- Reference solve: <command or script name>
- Expected result: <how the flag appears>
- Regression checks: <solvability; for medium+, single intended path with unintended_solutions enumerated and blocked>

### Hints
1. <Gentle>
2. <Technique>
3. <Near-solution>

### Anti-Frustration
- <false path to remove>
- <tool/version pin>
- <timeout/reset/resource note>
```

## Quality Gate

Run before finalizing any challenge or pack.

### Event-Level
- Category mix matches the event plan.
- Difficulty progression is plausible for the audience.
- Each major category has an easy on-ramp.
- Names and prompts fit the theme without leaking the trick.
- No category is dominated by one repeated technique.
- Hard and expert challenges have reliable reference solves.

### Challenge-Level
- Learning objective is specific and observable.
- Intended path has no unexplained leaps.
- Flag location follows from the solve path.
- Player prompt is spoiler-free but actionable.
- Hints are staged gentle → direct.
- Artifacts are small and documented enough for distribution.
- Validation can be run by another author.
- Reset and health-check behavior is defined for services.

### Safety
- Targets are synthetic and organizer-owned.
- Credentials, identities, logs, domains are fictional.
- No design attacks a real third-party system.
- No live malware behavior.
- Remote services are containerized or otherwise isolated.
- Pwn containers run as unprivileged `ctf` from `/home/ctf` unless documented otherwise.
- Web containers reuse the base image's non-root account (`www-data`, `tomcat`, etc.) when available.
- Dockerfiles end with the intended non-root runtime user.
- Compose files have NO `volumes`; required content is built into the image.
- Web services bind non-privileged internal ports; host port `80` is mapped, not bound directly.
- No `privileged: true`, broad capabilities, host devices, host networking, or writable system mounts unless essential and documented.

### Fairness
- Avoid guessing-heavy hidden endpoints.
- Avoid dependence on one obscure tool unless the challenge teaches that tool.
- Avoid brittle race windows unless bounded and stabilized.
- Avoid excessive artifact noise that does not support the intended path.
- Avoid flags in metadata unless the challenge is explicitly about metadata.

### Revision Actions on Gate Failure
- Add an observable clue.
- Narrow the artifact.
- Replace a duplicate technique.
- Add a validation script.
- Pin a dependency version.
- Split a multi-trick design into separate challenges.
- Downgrade or upgrade difficulty based on actual solver burden.

## Author Ticket Shape

Compact format when the user asks for implementation tickets:

```markdown
### <ID>. <Title>

- Build:
- Deploy:
- Solve:
- Validate:
- Risk:
```
