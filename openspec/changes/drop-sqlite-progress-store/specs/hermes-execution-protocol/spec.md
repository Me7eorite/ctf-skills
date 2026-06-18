## MODIFIED Requirements

### Requirement: Runner computes resume plans before current run events

`HermesRunner` SHALL compute a structured resume plan immediately after claiming
a shard and before writing the current run's shard-level queued/running event.
The plan MUST use `ShardQueue.original_name(running_path)` as the shard key and
MUST be injected into the rendered prompt. Hermes MUST follow the host-provided
plan and MUST NOT query the progress event store or infer completed stages
itself. Resume plan calculation SHALL go through the injected `ProgressStore`
protocol; the runner SHALL NOT import a concrete progress store implementation.

#### Scenario: Resume plan uses original shard key

- **WHEN** worker `worker-02` claims `web-0001-0005.json` as
  `web-0001-0005.worker-02.json`
- **THEN** resume queries and rendered `progress --shard` commands use
  `web-0001-0005.json` and not the worker-suffixed filename

#### Scenario: Current queued event is not part of plan calculation

- **WHEN** a non-dry-run shard is claimed for retry
- **THEN** the runner computes the resume plan from historical events before it
  resets snapshots or records the current queued/running event

#### Scenario: Resume queries go through the protocol

- **WHEN** the runner builds a resume plan
- **THEN** all event reads go through `progress.events_for_shard`,
  `progress.events_for_challenge`, and `progress.latest_claim_event`
- **AND** no read goes through a SQLite cursor, a `core.state.StateStore`
  attribute, or a `work/state.sqlite3` file path

### Requirement: ProgressStore exposes resume-safe event queries

The `ProgressStore` protocol SHALL expose public read APIs for complete
event streams: `events_for_shard(shard, before_id=None)`,
`events_for_challenge(shard, challenge_id, after_id=None,
before_id=None)`, and `latest_claim_event(shard, before_id=None)`.
Events MUST be returned by ascending event id, `before_id` MUST be
exclusive, and `after_id` for challenge events MUST be inclusive.
`events_for_challenge` MUST return only events whose `challenge_id`
equals the parameter value and MUST exclude shard-level events that
have an empty `challenge_id`; shard-level events are accessed
exclusively via `events_for_shard` or `latest_claim_event`.
`ProgressStore.record()` SHALL return the inserted event id.
`reset_snapshots(shard)` SHALL delete only snapshots for the named
original shard and SHALL NOT delete events.

#### Scenario: Event boundaries are respected

- **WHEN** query APIs are called with `after_id` and `before_id`
- **THEN** returned events include only records inside the documented id window
  and remain ordered by id

#### Scenario: Snapshot reset preserves history

- **WHEN** `reset_snapshots("web-0001-0005.json")` is called
- **THEN** snapshots for that shard are removed and all progress events remain
  queryable

### Requirement: Snapshot percent is monotonic within a run

After snapshots are reset for a new non-dry-run claim, snapshot updates SHALL
preserve the maximum of the existing derived percent and the new event derived
percent, where the percent is computed by `_percent(stage, status)` in
`core/state.py`. The implementation SHALL NOT persist `percent` as a column.
When a newer event has a lower derived percent than the current snapshot, the
upsert SHALL keep the snapshot's `stage` and `status` and update only
`updated_at`, `worker`, and `message`. This is a deliberate behavior change
from the pre-existing SQLite upsert, which always overwrote `(stage, status)`
to the newest event and only constrained `percent`. The dashboard now displays
the `(stage, status)` of the highest-progress event seen in the window rather
than the last-arriving event.

#### Scenario: Lower-progress event does not reduce displayed percent

- **WHEN** document/passed is followed by validate/running in the same run
- **THEN** the validate/running event is appended to `progress_events`
- **AND** the snapshot keeps `stage=document` and `status=passed`
- **AND** the dashboard-visible derived percent does not fall below the
  document/passed percent

#### Scenario: Out-of-order regression is suppressed

- **WHEN** a snapshot is at `(stage=validate, status=running)` and a late
  build/passed event arrives for the same `(shard, challenge_id)` pair
- **THEN** the snapshot keeps `(validate, running)` but the new event is still
  appended to `progress_events`

## REMOVED Requirements

### Requirement: StateStore exposes resume-safe event queries

**Reason**: The `StateStore` class is removed. The same contract is now owned
by the `ProgressStore` protocol declared in `core/state.py`, with concrete
implementations `PostgresProgressStore` (production) and
`InMemoryProgressStore` (tests). See the new requirement "ProgressStore
exposes resume-safe event queries" in this capability and the
`progress-event-store` capability for the full protocol contract.

**Migration**: All `StateStore(paths)` constructions in `cli.py`, `web/`,
`hermes/runner.py`, and tests SHALL be replaced. Production code paths
receive a `PostgresProgressStore` injected at the composition root
(`cli.py`, `web/server.py`). Test code paths construct
`InMemoryProgressStore()` directly. No `from core.state import StateStore`
import SHALL remain anywhere in `src/` or `tests/`.
