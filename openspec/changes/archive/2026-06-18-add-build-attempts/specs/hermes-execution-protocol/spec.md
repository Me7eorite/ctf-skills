## MODIFIED Requirements

### Requirement: Runner computes resume plans before current run events

`HermesRunner` SHALL compute a structured resume plan immediately after
claiming a shard and before writing the current run's shard-level
queued/running event. For an ordinary shard, the resume-read key and current
progress-write key SHALL both be `ShardQueue.original_name(running_path)`.
The plan MUST be injected into the rendered prompt. Hermes MUST follow the
host-provided plan and MUST NOT query the progress event store or infer
completed stages itself. Resume plan calculation SHALL go through the injected
`ProgressStore` protocol; the runner SHALL NOT import a concrete progress store
implementation.

When a generated shard contains a non-empty top-level
`resume_from_shard_basename`, the runner SHALL use that basename only as the
historical resume-read key passed to `latest_claim_event` and
`events_for_challenge`. It SHALL continue to use the current original basename
for snapshot reset, the current claim event, carry-forward events, rendered
`progress --shard` commands, validation events, and completion events.

The named resume source SHALL be a safe basename ending in `.json`; absolute
paths, path separators, `..`, and a value equal to the current basename SHALL
be rejected as malformed generated-shard input. Hand-written shards that omit
the field SHALL retain existing behavior.

#### Scenario: Retry reads previous attempt but writes current attempt

- **GIVEN** current shard `web-0001-attempt-2.json` contains
  `resume_from_shard_basename = "web-0001-attempt-1.json"`
- **WHEN** the runner computes and executes its resume plan
- **THEN** historical claim and challenge-event queries use
  `web-0001-attempt-1.json`
- **AND** all newly written progress and carry-forward events use
  `web-0001-attempt-2.json`

#### Scenario: Current queued event is not part of plan calculation

- **WHEN** a non-dry-run shard is claimed for retry
- **THEN** the runner computes the resume plan from the selected historical
  source before it resets current snapshots or records the current
  queued/running event

#### Scenario: Resume queries go through the protocol

- **WHEN** the runner builds a resume plan
- **THEN** all event reads go through `progress.events_for_shard`,
  `progress.events_for_challenge`, and `progress.latest_claim_event`
- **AND** no read goes through a SQLite cursor, legacy state-store attribute,
  or `work/state.sqlite3`

#### Scenario: Initial and hand-written shards are unchanged

- **WHEN** a shard omits `resume_from_shard_basename`
- **THEN** resume reads and current progress writes both use its current
  original basename

#### Scenario: Unsafe resume source is rejected

- **WHEN** `resume_from_shard_basename` is `../old.json`, `/tmp/old.json`, or
  contains a path separator
- **THEN** the runner rejects the shard before reading progress history
