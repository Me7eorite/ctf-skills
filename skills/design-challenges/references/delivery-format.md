# CTF Challenge Delivery Format

Use this reference whenever generated challenges must be delivered as files, packages, reports, EXP scripts, Docker images, or Excel inventories.

Source of truth: `docs/delivery-formats/ctf-v2/交付格式规范.md` (`CTF题目交付格式规范 v2.0`, 2026-06-09). Reference layout sample: `docs/delivery-formats/ctf-v2/资源包/`. If this summary conflicts with that file, follow the source of truth.

## Package Layout

```text
资源包/
├── 工具/                                # writeup + exp per challenge
│   └── js-{type}-{name}exp.zip
├── 题库资源/
│   ├── ctf-overview.xlsx               # one row per challenge
│   └── deploy/
│       ├── js-{type}-{name}.zip        # docker config per containerized challenge
│       ├── enclosure/                  # player attachments per challenge
│       │   └── js-{type}-{name}.zip
│       └── report/                     # PDF writeup per challenge
│           └── js-{type}-{name}.pdf
└── 虚拟机资源/
    ├── docker-tar/                     # image tar — pwn/web (containerized challenges only)
    │   └── {name}[{port}]-{YYYYMMDD}.tar
    └── 镜像模板.xlsx                   # inventory of docker-tar files
```

Key constraints:

- `工具/*.zip` bundles BOTH the writeup source (`wp.md`) and the EXP scripts — they are no longer split.
- `题库资源/deploy/*.zip` is the docker source package (the `deploy/` tree below). Only containerized challenges get one.
- `enclosure/` and `report/` live INSIDE `题库资源/deploy/`, not directly under `题库资源/`.
- `虚拟机资源/docker-tar/*.tar` is provided ONLY for challenges that ship a docker image (typically web and pwn, plus any other containerized category).
- `镜像模板.xlsx` is the canonical inventory of `docker-tar/`; add/remove rows whenever a tar is added/removed. `虚拟机模板.xlsx` is no longer required.

Each docker-config zip under `题库资源/deploy/` must wrap a `deploy/` tree:

```text
deploy/
├── src/                    # challenge source code
├── _files/                 # runtime configs, start.sh, init scripts, etc.
├── Dockerfile
└── docker-compose.yml      # exactly one service
```

`docker-compose.yml` must define exactly one service. Databases, caches, queues, and similar dependencies must be installed into the base image or started from `_files/start.sh` inside the same container.

Container authoring conventions:

- Define the deterministic organizer flag in the single Compose service under
  `environment` using the literal list form `- FLAG=flag{xxxx}`. It must equal
  `metadata.flag`, and the service must read `FLAG` from its runtime
  environment. Do not use `${FLAG}` interpolation or bake the flag into the
  Dockerfile, image layer, business source, or attachment.
- Set both `image` and `container_name` to the challenge name normalized as a
  stable lowercase Docker identifier (`[a-z0-9][a-z0-9_.-]`). Use that same
  name for build tags, validation commands, `metadata.docker_image`, and
  delivery inventory fields.
- Pwn images run with least privilege by default: create an unprivileged
  `ctf` user/group, place the challenge under `/home/ctf`, assign intentional
  ownership, and end with `USER ctf`.
- Web images reuse the base image's established non-root service account and
  standard application directory when available. Examples include
  `www-data` with `/var/www/html` for Apache/PHP and `tomcat` with the
  selected Tomcat image's conventional application directory. Create a
  separate `ctf` user only if the base image has no suitable account.
- Keep application and challenge files read-only at runtime where practical.
  Create narrowly scoped writable directories owned by the selected runtime
  user only for data the service genuinely needs to modify.
- `docker-compose.yml` must not define `volumes`, including bind mounts and
  named volumes. All source, configuration, startup assets, and seed data must
  be copied into the image during `docker build`.
- Web services should bind an unprivileged container port such as `8080`.
  When the delivery port is `80`, map host port `80` to that internal port
  instead of granting bind capabilities or running as root.
- Root execution, Linux capabilities, privileged mode, device mounts, host
  networking, or writable system directories require a challenge-specific
  technical reason. Use the smallest exception and document it in metadata,
  validation notes, and the Chinese writeup.
- A Dockerfile may replace Debian/Ubuntu apt sources with an
  organizer-approved mirror when the target build network needs it. Preserve
  the base release/codename, switch the source before `apt-get update`, and
  keep `apt-get update`, package installation, and `/var/lib/apt/lists`
  cleanup in one `RUN` layer. Prefer the upstream source when it is reliable.

## Naming Rules

| Content | Path | Format | Example |
| --- | --- | --- | --- |
| EXP + writeup package | `工具/` | `js-{type}-{name}exp.zip` | `js-crypto-rsa_wiener_001exp.zip` |
| Docker config package | `题库资源/deploy/` | `js-{type}-{name}.zip` | `js-web-sqli_basic_001.zip` |
| Attachment package | `题库资源/deploy/enclosure/` | `js-{type}-{name}.zip` | `js-crypto-rsa_wiener_001.zip` |
| PDF report | `题库资源/deploy/report/` | `js-{type}-{name}.pdf` | `js-crypto-rsa_wiener_001.pdf` |
| Docker image tar | `虚拟机资源/docker-tar/` | `{name}[{port}]-{YYYYMMDD}.tar` | `sqli_basic_001[8080]-20260521.tar` |

## Category Prefixes

| Category | Prefix | English Name |
| --- | --- | --- |
| Cryptography | `js-crypto` | Crypto |
| Web Exploitation | `js-web` | Web |
| Binary Exploitation | `js-pwn` | Pwn |
| Reverse Engineering | `js-reverse` | Reverse |
| Miscellaneous | `js-misc` | Misc |
| Steganography | `js-stego` | Stego |
| Forensics | `js-forensics` | Forensics |
| Industrial Control Systems | `js-ics` | ICS |
| AI Security | `js-ai` | AI |
| Cloud Security | `js-cloud` | Cloud |
| Mobile Security | `js-mobile` | Mobile |
| Blockchain Security | `js-blockchain` | Blockchain |
| IoT Security | `js-iot` | IoT |
| Automotive Security | `js-auto` | Auto |
| Data Security | `js-data` | Data |
| Malware Analysis | `js-malware` | Malware |
| OSINT | `js-osint` | OSINT |

## EXP + Writeup Package Rules

Path: `工具/js-{type}-{name}exp.zip`.

Required contents:

```text
js-{type}-{name}exp.zip
├── wp.md                # writeup source — MUST be written in Chinese, same content as the report/ PDF
├── exp.py               # main solve script; runs end-to-end and prints the flag
├── exp2.py              # optional alternate solve
├── utils.py             # optional helpers
├── requirements.txt     # pin Python deps if any
└── [other solve tools]
```

Rules:

- Bundle BOTH the writeup source (`wp.md`) and the solve scripts in the same zip.
- **`wp.md` body MUST be written in Chinese** (headings, analysis, step-by-step instructions all in Chinese; code, commands, and tool names stay in English). The PDF under `题库资源/deploy/report/` must carry the same Chinese content.
- `exp.py` should run and obtain the flag without manual edits.
- Document Python version and dependencies.
- Do not embed large unrelated assets.

## Docker Config Package Rules

Path: `题库资源/deploy/js-{type}-{name}.zip`. Required only for containerized challenges (web, pwn, and any other categories that ship a container).

Contents — the entire `deploy/` tree shown above. The resulting image, when built from this package, must match the tar shipped under `虚拟机资源/docker-tar/`.

## Attachment Package Rules

Path: `题库资源/deploy/enclosure/js-{type}-{name}.zip`.

Provide attachments according to category:

| Category | Attachments | Typical Contents |
| --- | --- | --- |
| Crypto | required | `task.py`, `output.txt`, public keys, parameters |
| Web | not required | players access the container directly |
| Pwn | optional | binary, libc, ld may be provided |
| Reverse | required | binaries, unpacked files, auxiliary assets |
| Stego | required | image/audio/video carrier files |
| Forensics | required | PCAPs, logs, disk or memory images |
| Misc | required | archives, QR codes, text, puzzle files |
| ICS | required | PLC programs, traffic, config files |
| AI | required | model files, sample data, adversarial samples |
| Cloud | optional | cloud environment or configuration files |
| Mobile | required | APK, certificates, config files |
| Blockchain | required | contracts, transaction records, simulated private keys |
| IoT | required | firmware, device config, communication logs |
| Auto | required | CAN logs, ECU firmware, protocol data |

Attachment packages must not contain plaintext flags or real credentials.

## PDF Report Rules

Path: `题库资源/deploy/report/js-{type}-{name}.pdf`.

The report must be PDF, **written in Chinese** (matching the `wp.md` content in the EXP package; keep code/commands/tool names in English), and contain:

1. 题目分析 — type and core test point identification.
2. 漏洞/弱点分析 — technical analysis.
3. 解题步骤 — step-by-step solve guide.
4. 工具使用 — tools and parameters.
5. Flag 获取 — final flag and how it was obtained.
6. 附录 — optional code, screenshots, supporting notes.

A second tester must be able to solve the challenge using only the PDF.

## Docker Tar Rules

Path: `虚拟机资源/docker-tar/{name}[{port}]-{YYYYMMDD}.tar`.

Only containerized challenges get a tar — primarily web and pwn, plus any other category that ships a container. Non-containerized challenges (most crypto, reverse, stego, forensics, misc) do not.

Examples:

- Web: `sqli_basic_001[8080]-20260520.tar`
- Pwn: `stack_overflow_001[9999]-20260520.tar`
- Other container challenge: `some_chal_001[10000]-20260520.tar`

Image load naming convention: after `docker load`, the image is `{name}:{date}`, e.g. `sql/bool:202605`.

Build constraint: the tar must be reproducible from the matching docker config package under `题库资源/deploy/`.

Port ranges:

| Category | Port Range | Port Mark Required |
| --- | --- | --- |
| Web | `80`, `8080`, `8000-8999` | yes |
| Pwn | `9000-9999` | yes |
| Other container challenges | `10000-10999` | yes |
| No-container challenges | none | no — tar is omitted |

## Excel Rules

`题库资源/ctf-overview.xlsx` — one row per challenge:

| Field | Meaning | Example |
| --- | --- | --- |
| 题目ID | type-sequence | `crypto-001` |
| 题目名称 | specific challenge name | `rsa_wiener_001` |
| 题目描述 | brief scenario | `某RSA系统使用小私钥加密...` |
| 题型 | category | `Crypto` |
| 难度 | `Easy`/`Medium`/`Hard` | `Medium` |
| 考点 | core test point | `Wiener攻击` |
| 分值 | points | `200` |
| flag格式 | flag prefix or template | `flag{wiener_...}` |
| 状态 | `待验证`/`通过` | `通过` |

`虚拟机资源/镜像模板.xlsx` — one row per tar file in `docker-tar/`:

- 题目名称
- 镜像文件 (tar filename)
- 端口
- 基础镜像 (e.g. ubuntu, python, nginx)
- 启动命令

`虚拟机模板.xlsx` is no longer part of the required delivery.

## Delivery Checklist

P0 checks:

- Each challenge has a `工具/js-{type}-{name}exp.zip` containing both `wp.md` and `exp.py`.
- Each containerized challenge has a `题库资源/deploy/js-{type}-{name}.zip` with `src/`, `_files/`, `Dockerfile`, and single-service `docker-compose.yml`.
- Attachment naming under `题库资源/deploy/enclosure/` matches the prefix rules.
- Each challenge has a PDF under `题库资源/deploy/report/`.
- Each containerized challenge has a tar under `虚拟机资源/docker-tar/`, loadable via `docker load`.
- `虚拟机资源/镜像模板.xlsx` rows match the tar files one-to-one.
- `题库资源/ctf-overview.xlsx` row count equals the number of challenges.
- Running `exp.py` produces the flag.

P1 checks:

- Excel fields are fully populated.
- Attachments contain no unintended sensitive data or hardcoded exposed flag.
- Report steps are clear under manual review.
- `wp.md` in the EXP package matches the PDF under `report/`.
- Both `wp.md` and the PDF report are written in Chinese.
