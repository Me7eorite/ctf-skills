## ADDED Requirements

### Requirement: Passed challenges are selected for delivery

The packer SHALL discover challenge directories under
`work/challenges/<category>/` that contain `metadata.json`, and SHALL include
only challenges whose `build_status` is `passed`. The default output SHALL be
`work/资源包`, and a caller MAY override it.

#### Scenario: failed build is excluded

- **WHEN** a challenge has `build_status: failed`
- **THEN** no per-challenge file or overview row is emitted for it

### Requirement: Per-challenge files follow delivery format v2

For every selected challenge the packer SHALL emit:

- `工具/js-{prefix}-{name}exp.zip`, containing `wp.md` and solver files
- `题库资源/deploy/report/js-{prefix}-{name}.pdf`
- category-dependent deployment and enclosure zips

The category prefix SHALL follow the delivery format table, including
`js-reverse` for the internal `re` category. `delivery_name` SHALL override
the metadata ID when present.

#### Scenario: reverse challenge is packed

- **WHEN** a passed `re` challenge contains `writeup/wp.md`,
  `solve/solve.py`, and `dist/checker`
- **THEN** the tools archive contains `wp.md` and `exp.py`, the enclosure
  contains `checker`, and all output names start with `js-reverse-`

#### Scenario: web challenge has deployment but no enclosure

- **WHEN** a passed `web` challenge contains a `deploy/` tree
- **THEN** its deployment zip contains that tree under `deploy/`
- **AND** no enclosure zip is emitted

#### Scenario: pwn enclosure is opt-in

- **WHEN** a passed `pwn` challenge has player attachments
- **THEN** no enclosure is emitted by default
- **AND** an enclosure is emitted when `--include-pwn-attachments` is set

### Requirement: Writeups are rendered to PDF

The packer SHALL render the exact UTF-8 contents of `writeup/wp.md` into a
non-empty PDF. It SHALL warn, but not fail, when the source contains no CJK
code point.

#### Scenario: Chinese writeup renders

- **WHEN** `writeup/wp.md` contains Chinese Markdown
- **THEN** the report path contains a valid PDF beginning with `%PDF`

### Requirement: Inventories match emitted content

The packer SHALL create `题库资源/ctf-overview.xlsx` with the nine columns
declared by delivery format v2 and one row per selected challenge. It SHALL
create `虚拟机资源/镜像模板.xlsx` with one row per successfully emitted Docker
tar.

#### Scenario: Docker is skipped

- **WHEN** packing runs with `--skip-docker`
- **THEN** no Docker tar is emitted
- **AND** `镜像模板.xlsx` contains only its header row

### Requirement: Docker export degrades predictably

For containerized challenges the packer SHALL always emit the deployment zip.
Unless Docker is skipped, it SHALL invoke `docker save` for the metadata
`docker_image`, or a documented fallback image tag. If the Docker CLI is
unavailable it SHALL warn and continue by default; `--require-docker` SHALL
turn that condition into a packing error.

#### Scenario: Docker CLI is unavailable

- **WHEN** a containerized challenge is packed and `docker` is not on PATH
- **THEN** the deployment zip, tools zip, PDF, and overview row still exist
- **AND** the summary contains a Docker warning
