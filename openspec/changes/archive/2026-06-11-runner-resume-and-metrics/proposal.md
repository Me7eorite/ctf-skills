## Why

Long-running Hermes shards currently waste work and obscure failure causes:
service port rules conflict with common Web stacks, interrupted runs redo whole
shards, timeouts are only configurable through an environment variable,
generated validation scripts rebuild images every time, and SQLite progress
events do not expose per-stage duration.

This change defines a host-owned execution protocol so retries can resume from
verified evidence, validation results are recorded consistently, and operators
can tune and inspect long shard runs without changing the five-stage model.

## What Changes

- Relax the Web prompt's container port and root-process rules so Apache/nginx
  may use their normal root master plus non-root worker pattern, while business
  processes still cannot run permanently as root or request broad privileges.
- Add a host-precomputed resume protocol that uses the original shard name,
  historical progress events, and deterministic artifact evidence to skip only
  the continuous passed stage prefix for each challenge.
- Move validate-stage execution ownership from Hermes to `HermesRunner` and
  `ChallengeValidator`: Hermes generates `validate.sh` and `solve/solve.py`,
  while the runner performs mandatory per-challenge validation and records
  `validate/*` events.
- Make non-dry-run `challenge-factory run` always validate; remove the optional
  `run --validate` switch while keeping the standalone `challenge-factory
  validate` command.
- Add explicit `run --timeout INT` with precedence `CLI flag > HERMES_TIMEOUT
  > default 1500`, and print the effective timeout source before claim work.
- Update the validate-script prompt contract to reuse existing Web/Pwn Docker
  images via `docker image inspect "$IMAGE" >/dev/null 2>&1 || docker build`.
- Add read-only StateStore event queries, snapshot reset for new runs, monotonic
  snapshot percent upserts, and structured report merging for per-challenge
  validation outcomes.
- Add backend per-stage duration metrics for the latest claim window and a
  `challenge-factory durations --challenge <id> --shard <original>.json` JSON
  CLI.
- Add `core.docker.image_exists(image)` as the only Docker image-inspection
  subprocess helper used by resume evidence checks.

**BREAKING**: `challenge-factory run --validate` is removed because validation
becomes mandatory for non-dry-run execution.

## Capabilities

### New Capabilities

- `hermes-execution-protocol`: Hermes shard execution contract covering resume
  decisions, normalized shard identity, runner-owned validation, timeout
  selection, Web service prompt rules, validate.sh image reuse, and stage
  duration metrics.

### Modified Capabilities

- None.

## Impact

- Updates `prompts/shard_prompt.md`, especially Web container rules, Resume
  Check instructions, Stage 4 validation responsibilities, mandatory progress
  reporting, and validate.sh examples.
- Updates `src/hermes/runner.py`, `src/hermes/prompt.py`, `src/cli.py`,
  `src/core/state.py`, `src/core/queue.py`, `src/domain/validation.py`, and
  report handling.
- Adds `src/domain/resume.py`, `src/domain/metrics.py`, and
  `src/core/docker.py`.
- Adds tests for resume planning, state queries, dry-run isolation, timeout
  precedence, Docker helper behavior, validation ownership, report merging,
  mixed-result shards, snapshot monotonicity, and duration metrics.
