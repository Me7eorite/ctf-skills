## ADDED Requirements

### Requirement: Generation runs are first-class dashboard objects

The backend SHALL create a generation run object when the browser launches a
batch from `/generate/new`. A generation run groups the submitted seed batch,
the planned shard files, and the requested execution mode under a stable
`run_id`. The run object SHALL be persisted under project runtime state
(`work/runs/` or an equivalent `ProjectPaths` location) so it remains
inspectable after the initial HTTP response.

Shard directories remain the source of truth for queue state. The run object
is an observability and grouping manifest, not a replacement queue or lock.

#### Scenario: Run manifest is persisted

- **WHEN** a user launches a valid generation run from the browser
- **THEN** the response contains a stable `run_id`
- **AND** a run manifest for that `run_id` is written under runtime state
- **AND** the manifest records submitted seed IDs, planned shard names,
  execution mode, worker request, creation timestamp, and latest aggregate
  status

#### Scenario: Run survives shard claim rename

- **WHEN** a worker immediately claims a pending shard created for a run and
  renames it into `work/shards/running/`
- **THEN** the run detail endpoint can still associate that claimed shard with
  the original `run_id`
- **AND** the frontend Launch Monitor can keep showing the run without relying
  on the original pending filename still existing

### Requirement: Run creation endpoint owns planning and launch

`POST /api/runs` SHALL be the primary browser contract for creating generation
runs. It SHALL validate submitted seeds, plan shard files, persist the run
manifest, write planned shards, and optionally request worker execution. The
frontend SHALL NOT approximate run creation by independently calling
`POST /api/seeds`, `POST /api/seeds/enqueue`, and worker action endpoints.

The endpoint SHALL support these request fields:

- `seeds`: non-empty list of matrix-compatible seed objects
- `shard_size`: positive integer
- `save_seeds`: boolean controlling whether submitted seeds are saved for reuse
- `execution.mode`: one of `enqueue_only`, `single_worker`
- `execution.dry_run`: boolean
- `execution.timeout`: optional positive integer

#### Scenario: Enqueue-only run

- **WHEN** a request uses `execution.mode: "enqueue_only"`
- **THEN** the endpoint validates the seed batch, writes the run manifest, and
  creates pending shards
- **AND** no worker process is started
- **AND** the response includes links to the run, queue, and created shards

#### Scenario: Single-worker dry-run

- **WHEN** a request uses `execution.mode: "single_worker"` and
  `execution.dry_run: true`
- **THEN** the endpoint creates the run and pending shards
- **AND** requests exactly one dashboard worker in dry-run mode
- **AND** the response identifies the started worker or returns a clear
  execution error without losing the persisted run manifest

#### Scenario: Unsupported execution mode is rejected

- **WHEN** a request uses an execution mode other than `enqueue_only` or
  `single_worker`
- **THEN** the endpoint rejects the request with a validation error
- **AND** no shard files, run manifest, saved seed updates, or worker process
  is created

### Requirement: Run preview is side-effect free

The backend SHALL expose `POST /api/runs/preview` for the generation cockpit's
Shard Plan section. The preview request SHALL accept the same planning inputs
as `POST /api/runs` except that execution is summarized only; no worker starts.
The preview response SHALL include normalized seed validation results, planned
run ID or run ID prefix, planned shard filenames, per-category counts, execution
summary, and blocking errors.

Preview MUST NOT write shard files, persist submitted seeds, write run
manifests, mutate saved presets, or start worker processes.

#### Scenario: Preview returns planned shards without side effects

- **WHEN** a user previews a valid seed batch with shard size 3
- **THEN** the response lists the planned shard filenames and counts
- **AND** no file is created under `work/shards/`
- **AND** no run manifest is created under `work/runs/`
- **AND** `work/challenge_seeds.json` is not modified

#### Scenario: Preview surfaces validation errors

- **WHEN** a user previews a seed batch containing an invalid Web seed with no
  port
- **THEN** the response identifies the invalid row and field
- **AND** no runtime state is modified

### Requirement: Browser-created shard filenames are run-scoped

Browser-created generation runs SHALL use unique run-scoped shard filenames so
operators can repeatedly dry-run or re-run the same seed batch without
colliding with prior pending/running/done/failed shard files. The shard JSON
payload SHALL retain the existing `{"challenges": [...]}` format consumed by
workers.

Low-level seed enqueue endpoints MAY keep the legacy category/range filename
behavior.

#### Scenario: Re-run same batch creates unique shard names

- **WHEN** a user launches the same valid seed batch twice from `/generate/new`
- **THEN** both launches succeed
- **AND** the shard filenames differ by run identifier or run-scoped suffix
- **AND** each run manifest lists only the shards created for that run

### Requirement: Run planning is atomic before side effects

Run creation SHALL validate every submitted seed and detect shard filename
collisions before writing shard files, persisting submitted seeds, or starting
worker processes. If planning fails, no partial shard files, saved seed
updates, run manifests, or worker processes are created.

#### Scenario: Invalid seed prevents side effects

- **WHEN** a run-creation request contains one valid seed and one invalid seed
- **THEN** the response is a validation error
- **AND** no pending shard is written
- **AND** no submitted seed is persisted
- **AND** no run manifest is written
- **AND** no worker process is started

#### Scenario: Shard collision prevents side effects

- **GIVEN** `work/shards/pending/web-0001-0003.json` already exists
- **WHEN** a run-creation request would create the same shard filename
- **THEN** the response is a conflict
- **AND** the existing shard remains unchanged
- **AND** no submitted seed is persisted
- **AND** no run manifest is written
- **AND** no worker process is started

### Requirement: Run status is derived from queue and progress state

The backend SHALL expose run status by deriving current shard state from
`work/shards/{pending,running,done,failed}/`, single-worker launch metadata
where available, and per-challenge progress from the existing SQLite progress
state. The manifest's latest aggregate status MAY cache the derived value, but
derivation from queue/progress state remains authoritative.

#### Scenario: Aggregate status reflects shard state

- **WHEN** every shard associated with a run is under `work/shards/done/`
- **THEN** the run aggregate status is `done`

#### Scenario: Failed shard marks run failed

- **WHEN** at least one shard associated with a run is under
  `work/shards/failed/`
- **THEN** the run aggregate status is `failed`
- **AND** the run detail includes the failed shard name and log link when
  available

#### Scenario: Mixed pending/running shards mark run active

- **WHEN** any shard associated with a run is pending or running and no shard
  has failed
- **THEN** the run aggregate status is `running` or `pending` according to
  whether at least one shard is currently claimed/running

### Requirement: Generation run read endpoints are run-id based

The backend SHALL expose run-id based read endpoints separate from the existing
shard-oriented `/api/runs/{shard}` routes. At minimum,
`GET /api/generation-runs/{run_id}` SHALL return the manifest, derived shard
states, associated worker snapshots when available, progress summary, and links
to queue/log/run-detail views.

#### Scenario: Read run by id

- **WHEN** a client requests `GET /api/generation-runs/{run_id}` for an
  existing generation run
- **THEN** the response includes `run_id`, submitted seed IDs, planned shards,
  derived aggregate status, execution request, associated workers, and UI links

#### Scenario: Missing run id returns not found

- **WHEN** a client requests `GET /api/generation-runs/missing`
- **THEN** the response is HTTP 404
