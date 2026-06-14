## ADDED Requirements

### Requirement: Generation requests are scoped to a single challenge category

The system SHALL persist a required `category` column on every `generation_requests` row. Allowed values SHALL be exactly `core.queue.SUPPORTED_CATEGORIES` (`web`, `pwn`, `re`). The column SHALL be backed by a PostgreSQL enum type `challenge_category`. A request whose `category` is missing, null, or not in the allowed set SHALL be rejected by the repository before any row is written.

#### Scenario: Valid category is persisted

- **WHEN** an operator submits `category="web"`, `topic="SQL injection"`
- **THEN** the persisted `generation_requests` row has `category="web"`

#### Scenario: Unknown category is rejected before persistence

- **WHEN** an operator submits `category="crypto"`
- **THEN** the repository raises a validation error naming the unknown category and the allowed set
- **AND** no row is written to `generation_requests`

#### Scenario: Missing category is rejected before persistence

- **WHEN** an operator submits a request payload that omits `category`
- **THEN** the repository raises a validation error naming the missing field
- **AND** no row is written to `generation_requests`

#### Scenario: Listing requests can filter by category

- **WHEN** `ResearchRepository.list_generation_requests(category="re")` is called against a store containing web, pwn, and re requests
- **THEN** only the re-category requests are returned

### Requirement: Generation requests capture operator intent with validated distribution

The system SHALL persist generation requests as rows in `generation_requests` with `category` (enum `web|pwn|re`), `topic` (text), `target_count` (positive integer), `difficulty_distribution` (jsonb mapping difficulty label → count), `runtime_constraints` (jsonb), `status` (enum `draft|researching|researched|failed`), and `created_at` / `updated_at` timestamps. Difficulty labels SHALL be one of `easy|medium|hard|expert`. The sum of values in `difficulty_distribution` SHALL equal `target_count`. A request whose distribution does not sum to `target_count`, or whose distribution contains an unknown label, SHALL be rejected by the repository before any row is written.

#### Scenario: Valid request is persisted

- **WHEN** an operator submits `category="web"`, `topic="SQL injection"`, `target_count=20`, `difficulty_distribution={"easy":5,"medium":10,"hard":5}`
- **THEN** a row appears in `generation_requests` with `status="draft"` and the validated fields
- **AND** the operation returns the new request id

#### Scenario: Distribution mismatch is rejected before persistence

- **WHEN** an operator submits `target_count=20` with `difficulty_distribution={"easy":5,"medium":10,"hard":3}`
- **THEN** the repository raises a validation error naming the mismatch
- **AND** no row is written to `generation_requests`

#### Scenario: Unknown difficulty label is rejected

- **WHEN** an operator submits `difficulty_distribution={"easy":5,"trivial":15}`
- **THEN** the repository raises a validation error naming the unknown label
- **AND** no row is written to `generation_requests`

### Requirement: Research runs follow a strict state machine

The system SHALL model research run lifecycle with statuses `queued`, `running`, `completed`, `failed`. A research run SHALL transition `queued → running → (completed | failed)` and SHALL NOT skip the `running` state. A run that enters `failed` SHALL carry a non-empty `error` message. A run that enters `completed` SHALL have `finished_at` set and `error` null.

#### Scenario: Successful run reaches completed

- **WHEN** `ResearchRunner.execute(generation_request_id)` succeeds end-to-end
- **THEN** the corresponding `research_runs` row transitions queued → running → completed
- **AND** `finished_at` is set and `error` is null

#### Scenario: Hermes failure is recorded as failed

- **WHEN** the Hermes Research Agent exits non-zero or returns invalid JSON
- **THEN** the `research_runs` row transitions queued → running → failed
- **AND** `error` contains a non-empty diagnostic
- **AND** no `research_sources` or `research_findings` rows for this run exist

#### Scenario: Failed run leaves no partial sources or findings

- **WHEN** `ResearchRunner.execute` raises midway through persistence
- **THEN** the surrounding transaction rolls back
- **AND** the row count in `research_sources` and `research_findings` for that run is zero

### Requirement: Every research finding references at least one source

The system SHALL persist research findings as rows in `research_findings` with `kind` (enum `technique|variant|scenario|prerequisite`), `label` (text), `summary` (text), and a foreign key to `research_runs`. The system SHALL persist source references in `research_finding_sources` (join table on `finding_id` + `source_id`). The repository SHALL reject any finding submitted without at least one source reference, before any row is written. The combined insert (`research_findings` row + join rows) SHALL be atomic within a single transaction.

#### Scenario: Finding with sources is persisted atomically

- **WHEN** `ResearchRepository.create_finding(run_id, kind, label, summary, source_ids=[s1, s2])` is called
- **THEN** one row in `research_findings` and two rows in `research_finding_sources` exist, all inside the same transaction

#### Scenario: Finding without sources is rejected

- **WHEN** `ResearchRepository.create_finding(run_id, kind, label, summary, source_ids=[])` is called
- **THEN** the repository raises a validation error
- **AND** no row is written to `research_findings`

### Requirement: Research stage does not write to the shard queue

The system SHALL NOT, during the research stage, write any file to `work/shards/pending/`, modify `work/shards/`, or invoke `ShardQueue.split_*`. Promotion of approved candidate problems to the shard queue is the responsibility of a later approval stage.

#### Scenario: Research run does not touch the shard queue

- **WHEN** `ResearchRunner.execute(generation_request_id)` runs to completion against an empty `work/shards/pending/`
- **THEN** `work/shards/pending/` remains empty
- **AND** `ShardQueue.list_pending()` returns the same set as before the run

### Requirement: Hermes Research Agent prompt contract

The system SHALL render Hermes Research Agent prompts from `prompts/research_prompt.md` containing: the challenge category (`web | pwn | re`), the topic, the target count, the difficulty distribution, the operator-supplied seed URLs (possibly empty), and an explicit output contract requiring a single JSON object on stdout with `sources[]` and `findings[]` arrays. The prompt SHALL instruct the Agent to keep all findings within the declared category and to refuse cross-category material rather than silently mixing it in. Every entry in `findings[]` SHALL declare `source_indices: int[]` of length ≥ 1, each value a valid 0-based index into `sources[]`. The prompt SHALL show at least one sample matching this contract.

#### Scenario: Rendered prompt includes the output contract

- **WHEN** the Research prompt is rendered for any generation request
- **THEN** the rendered text contains the JSON schema description for `sources` and `findings`
- **AND** the text contains a worked example whose `findings[0].source_indices` is non-empty

#### Scenario: Hermes invocation does not touch the database

- **WHEN** the Hermes Research Agent subprocess runs
- **THEN** the subprocess environment does not include `DATABASE_URL`
- **AND** the `hermes` package source contains no import from `persistence`

### Requirement: Research artifacts on disk are tracked by path

The system SHALL store the raw fetched page text under `work/research/sources/<run_id>/<index>.txt` when raw text is captured, and the Hermes stdout/stderr log under `work/research/logs/<run_id>.log`. The corresponding `research_sources.raw_text_path` and `research_runs.hermes_log_path` columns SHALL hold these paths. PostgreSQL SHALL NOT store the raw text or the full Hermes log in any column.

#### Scenario: Source raw text is on disk, not in the database

- **WHEN** a research run persists three sources, two of which captured raw text
- **THEN** two files exist under `work/research/sources/<run_id>/`
- **AND** the two `research_sources` rows reference those paths via `raw_text_path`
- **AND** no `research_sources` column holds the raw text

#### Scenario: Hermes log is on disk

- **WHEN** a research run completes
- **THEN** `work/research/logs/<run_id>.log` exists and is non-empty
- **AND** `research_runs.hermes_log_path` for that run equals that path
