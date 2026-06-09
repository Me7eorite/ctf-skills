# Challenge Spec Template

Use this template for full organizer-facing challenge specifications.

When the user asks for deliverable-ready output, package plans, handoff manifests, or generated challenge bundles, apply [delivery-format.md](delivery-format.md). The spec below should include enough metadata to produce `ctf-overview.xlsx`, `题库资源/deploy/` (docker config zips), `题库资源/deploy/enclosure/`, `题库资源/deploy/report/`, `工具/` (wp + exp), and `虚拟机资源/` (docker-tar + 镜像模板.xlsx) exactly as required.

```markdown
## <ID>. <Title>

- Category: <web|pwn|reverse|...>
- Difficulty: <easy|medium|hard|expert>
- Points: <number or dynamic>
- Estimated solve time: <duration>
- Deployment: <static|download|docker|tcp|http>
- Authoring effort: <low|medium|high>
- Primary technique: <technique>
- Secondary technique: <optional>
- Learning objective: <what players should learn>

### Player Prompt

<Spoiler-free prompt shown on the scoreboard.>

### Intended Path

1. <Initial observation>
2. <Core vulnerability or reversing insight>
3. <Exploit, decode, or validation step>
4. <Flag extraction>

### Artifacts

- <files or services to provide>
- <source, binary, traffic, or seeded data>
- <Docker/service notes if needed>

### Delivery Format

- Prefix: `js-<type>`
- EXP + writeup package: `工具/js-<type>-<challenge_name>exp.zip` (must contain `wp.md` and `exp.py`)
- Docker config package: `题库资源/deploy/js-<type>-<challenge_name>.zip` for containerized challenges, or `not required`
- Attachment package: `题库资源/deploy/enclosure/js-<type>-<challenge_name>.zip` or `not required`
- PDF report: `题库资源/deploy/report/js-<type>-<challenge_name>.pdf`
- Docker tar: `虚拟机资源/docker-tar/<challenge_name>[<port>]-<YYYYMMDD>.tar` for containerized challenges (typically web/pwn), or `not required`
- Deploy tree inside the docker config zip: `deploy/src/`, `deploy/_files/`, `deploy/Dockerfile`, `deploy/docker-compose.yml`
- Compose rule: exactly one service; databases/dependencies must be installed in the same image/service
- Compose identity: `image` and `container_name` use the same Docker-safe,
  lowercase challenge name
- Flag injection: Compose defines `environment.FLAG` as `${FLAG}`; validation
  sets the host-side value, and the service does not bake plaintext into image
  layers, the Compose file, or source
- Apt source: retain upstream by default; when the target build network needs
  a mirror, use an organizer-approved source for the same distro release
- Overview row (`ctf-overview.xlsx`): `<题目ID>, <题目名称>, <题目描述>, <题型>, <难度>, <考点>, <分值>, <flag格式>, <状态>`
- Image inventory row (`镜像模板.xlsx`, containerized only): `<题目名称>, <镜像文件>, <端口>, <基础镜像>, <启动命令>`

### Flag Plan

- Format: `flag{...}`
- Location: <file, DB row, service response, validation output>
- Generation rule: <static, seeded, per-team, dynamic>

### Validation

- Reference solve: `<command or script name>`
- Expected result: <how the flag appears>
- Regression checks:
  - <check that challenge remains solvable>
  - <check that unintended shortcut is not present>

### Hints

1. <Gentle hint>
2. <Technique hint>
3. <Near-solution hint>

### Anti-Frustration Checks

- <false path to remove or document>
- <tool/version dependency to pin>
- <timeout, reset, or resource issue to test>
```

## Author Ticket Shape

Use this compact format when the user asks for implementation tickets:

```markdown
### <ID>. <Title>

- Build:
- Deploy:
- Solve:
- Validate:
- Risk:
```
