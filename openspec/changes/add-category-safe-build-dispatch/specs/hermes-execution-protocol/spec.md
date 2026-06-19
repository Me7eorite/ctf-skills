## ADDED Requirements

### Requirement: Hermes shard runner claims one worker-owned shard before prompting

The shard runner SHALL support constrained claiming in addition to the legacy
whole-queue claim. The public runner and CLI contracts SHALL accept optional
`category`, `build_attempt_id`, and build-attempt-attribution filters. When a
filter is present, the runner SHALL claim only a shard whose JSON payload
matches every requested filter and SHALL render the prompt from that claimed
shard.

The existing unconstrained runner behavior remains valid for whole-queue
legacy operation.

`--build-attempt` SHALL be mutually exclusive with `--loop`; it names one
specific shard execution target. `--category` MAY be combined with `--loop`.

#### Scenario: CLI category filter reaches the queue

- **WHEN** `challenge-factory run --worker W --category web` is invoked
- **THEN** the runner passes `category = web` to the shard queue claim
- **AND** no Pwn or Re shard is claimed by that worker invocation

#### Scenario: CLI attributed category filter skips legacy shards

- **GIVEN** a legacy Web shard and an attributed Web build-attempt shard are
  both pending
- **WHEN** `challenge-factory run --worker W --category web
  --build-attempts-only` is invoked
- **THEN** the runner passes `category = web` and build-attempt attribution
  required to the shard queue claim
- **AND** the legacy Web shard is not claimed

#### Scenario: CLI build-attempt filter reaches the queue

- **WHEN** `challenge-factory run --worker W --build-attempt A` is invoked
- **THEN** the runner passes `build_attempt_id = A` to the shard queue claim
- **AND** no shard lacking `build_attempt_id = A` is claimed

#### Scenario: Build-attempt run rejects loop mode

- **WHEN** `challenge-factory run --worker W --build-attempt A --loop` is
  invoked
- **THEN** the CLI exits with code 2 before claiming any shard

#### Scenario: No matching shard is not a failed generation

- **GIVEN** pending shards exist but none match the requested category or build
  attempt
- **WHEN** the constrained runner executes
- **THEN** it exits without invoking Hermes
- **AND** no shard is moved to `failed/`
