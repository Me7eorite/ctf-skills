## Why

The current shard worker claims the first JSON file under
`work/shards/pending/` by filename order. It does not know which category the
operator intended to build, and the dashboard starts a single global local
worker. When `pwn-*` and `web-*` shards coexist, a worker started from a Web
context can claim and execute a Pwn shard because `pwn` sorts before `web`.

Build attempts now make this more confusing: the Build Attempts view filters
rows by category, but its Start Worker action still calls the legacy global
worker endpoint. The visible row/category and the shard actually claimed by
the file queue can diverge.

## What Changes

- Add explicit category-safe and build-attempt-safe claim semantics to the
  file-backed shard queue.
- Add CLI options that let operators run the existing Hermes shard worker for
  one category or one build attempt instead of the whole pending queue.
- Make build-attempt worker actions use the constrained build-dispatch path
  instead of the legacy global dashboard worker action. Category actions from
  the Build Attempts view resolve a DB-known queued build attempt in that
  category, then start by build-attempt id.
- Keep the legacy unconstrained `run` behavior available for hand-written
  matrix shards and compatibility, but stop using it from category-specific UI
  controls.
- Add tests proving a Web-constrained worker cannot claim a Pwn shard when both
  are pending.

## Capabilities

### Modified Capabilities

- `build-orchestration`: build worker dispatch becomes explicit about category
  and build-attempt identity.
- `hermes-execution-protocol`: the CLI/Hermes runner contract gains
  constrained shard selection.

### New Capabilities

None. This is a correction to existing build execution semantics.

## Impact

- **Code**: update `core.queue.ShardQueue`, `hermes.runner.HermesRunner`,
  `cli.py`, build-attempt endpoints, and the build-attempts static view.
- **Database**: no schema change required.
- **Filesystem**: no directory layout change; constrained filtering reads
  existing shard payloads before claiming.
- **Compatibility**: existing unconstrained `challenge-factory run --worker W`
  remains valid.
- **Tests**: add queue unit tests, runner/CLI tests, build-attempt API tests,
  and dashboard interaction coverage for constrained worker actions.
