# delivery-bundle Specification

## Purpose
TBD - created by archiving change pack-delivery-bundle. Update Purpose after archive.
## Requirements
### Requirement: Passed challenges are selected for delivery

In production mode the packer SHALL include a challenge only when all of the
following are true:

- existing build metadata is compatible with a passed build, but is only a
  compatibility hint and not a sufficient admission signal;
- the corpus membership's immutable BuildAttempt has an effectively accepted
  ArtifactObservation (`status = passed`, or `status = inconclusive` plus a
  valid allowed observation review);
- the challenge belongs to the explicitly requested corpus-admission batch;
- its member decision is corpus-accepted (`passed`, or `review_required`
  with a valid recorded corpus approval);
- the aggregate batch decision is `passed` after the corpus service accounts
  for allowed member reviews without rewriting raw member decisions;
- no non-overrideable corpus rule failed.

Observation review and corpus review are independent. Passing one SHALL NOT
implicitly approve the other. In this spec, the validation layer uses
ArtifactObservation acceptance, while the corpus layer uses corpus-accepted
member decisions.

Production packing SHALL require an explicit `corpus_batch_id` argument and
database access to resolve immutable membership/decision records. It SHALL not
infer a batch from filesystem order, metadata, or latest-created timestamps.

`metadata.build_status = passed` alone SHALL not make a challenge eligible for
a production bundle.

The packer MAY expose explicit `shadow` and `trial` modes. Such outputs SHALL be
marked non-production in their summary/inventory and SHALL not overwrite the
default production bundle without an explicit output path. Shadow/trial packing
SHALL NOT satisfy or publish through the production release gate.

#### Scenario: Individually passed duplicate is excluded

- **GIVEN** a challenge with passed build/solve metadata
- **AND** its corpus decision is blocked as an exact governed duplicate
- **WHEN** production packing runs
- **THEN** the challenge is excluded and the pack operation reports the corpus
  block

#### Scenario: Reviewed borderline similarity may be packed

- **GIVEN** a challenge whose corpus decision is `review_required`
- **AND** an authorized operator recorded an allowed approval with reason and
  timestamp
- **AND** the selected corpus batch's aggregate decision is `passed` after
  accounting for that allowed review
- **WHEN** production packing runs
- **THEN** the challenge is eligible if every other delivery requirement passes

#### Scenario: Member review without aggregate pass is not enough

- **GIVEN** a challenge whose member corpus decision has an allowed approval
- **AND** the selected corpus batch aggregate decision is still
  `review_required` or `blocked`
- **WHEN** production packing runs
- **THEN** the challenge is excluded
- **AND** the pack operation reports the aggregate corpus decision

#### Scenario: Trial bundle is visibly non-production

- **WHEN** the packer runs in explicit trial mode
- **THEN** its summary and inventories identify the bundle as non-production
- **AND** the default production bundle is not silently replaced

### Requirement: Per-challenge files follow delivery format v2

For every selected challenge the packer SHALL emit:

- `工具/js-{prefix}-{name}exp.zip`, containing `wp.md` and solver files
- `题库资源/deploy/report/js-{prefix}-{name}.pdf`
- category-dependent deployment and enclosure zips

The category prefix SHALL follow the delivery format table, including
`js-reverse` for the internal `re` category. `delivery_name` SHALL override
the metadata ID when present.

#### Scenario: reverse challenge is packed

- **WHEN** a passed `re` challenge contains `writenup/wp.md`,
  `writenup/exp.py`, and `dist/checker`
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

The packer SHALL render the exact UTF-8 contents of `writenup/wp.md` into a
non-empty PDF. It SHALL warn, but not fail, when the source contains no CJK
code point.

#### Scenario: Chinese writeup renders

- **WHEN** `writenup/wp.md` contains Chinese Markdown
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
