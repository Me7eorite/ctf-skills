## Why

The project is moving toward a worker pool, but the current dashboard starts one
anonymous whole-queue process. A category-filtered screen can therefore start a
worker that claims another category. More seriously, Hermes currently receives
host-only absolute paths while its terminal may run in a persistent Docker
sandbox that does not mount the project cwd. A fresh Web conversation can fail
to read its Web shard, discover stale Pwn/Re files in the reused sandbox, and
continue the wrong work.

An agent registry and Hermes profile binding are necessary but insufficient.
The worker pool must make dispatch authorization, execution isolation, lease
ownership, capacity, and artifact publication enforceable outside the model.

## What Changes

- Add a project-owned Agent Registry with stable identities, Hermes profile
  bindings, capabilities, control state, capacity, heartbeat, and soft deletion.
- Add a bounded single-host supervisor that starts and monitors agent worker
  slots. Cross-host deployment remains outside this change.
- Require category- and build-attempt-constrained dispatch. Agent/profile names
  never select queue work implicitly.
- Add database-backed execution leases with fencing tokens and heartbeats so a
  stale process cannot publish or complete work after ownership is recovered.
- Run every build execution in a unique ephemeral task sandbox. Hermes profile
  state may be stable, but terminal workspace state must not be shared between
  attempts.
- Materialize a per-attempt input bundle with container-visible paths and fail
  before model invocation when required inputs are inaccessible.
- Write generation output only to an attempt staging directory. The host
  validates category/id/path allowlists and deterministic challenge checks,
  then atomically publishes accepted artifacts.
- Add dashboard and HTTP APIs for agent lifecycle, capacity, active executions,
  logs, and explicit start/drain/disable operations.
- Preserve existing research/design profile bindings while preventing the
  legacy global worker action from category-specific build surfaces.

## Capabilities

### New Capabilities

- `worker-agent-management`: persisted agents, profile bindings, capability
  assignment, control state, dashboard/API management, and audit identity.
- `worker-pool-execution`: single-host supervision, slots, lease/fencing,
  per-attempt sandboxing, staged output, and guarded publication.

### Modified Capabilities

- `build-orchestration`: build attempts become the dispatch and execution audit
  unit and record immutable agent/profile/execution ownership snapshots.
- `hermes-execution-protocol`: prompts use sandbox-visible paths and Hermes is
  invoked only after execution preflight succeeds.

## Impact

- **Code**: add persistence models/repositories, agent services, a local pool
  supervisor, constrained claim/lease services, sandbox preparation, guarded
  artifact publication, web endpoints, and dashboard views.
- **Database**: add agent/capability/execution tables and immutable audit fields
  on build attempts.
- **Filesystem**: add per-execution input/output staging roots. Agents do not
  write directly to `work/challenges`.
- **Hermes**: continue to use named profiles for configuration, but force a
  unique non-persistent terminal sandbox per execution and render only paths
  visible inside it.
- **Compatibility**: retain legacy CLI execution for explicit shard-management
  use, but do not expose it as a worker-pool or category-specific action.
- **Dependency**: constrained dispatch semantics from
  `add-category-safe-build-dispatch` must be implemented or incorporated before
  pool build claims are enabled.
- **Tests**: include concurrency, fencing, stale-sandbox, wrong-category,
  inaccessible-input, out-of-scope-write, crash recovery, and atomic-publication
  coverage.
