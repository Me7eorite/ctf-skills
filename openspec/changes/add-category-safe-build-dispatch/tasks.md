## 1. Queue and runner contract

- [x] 1.1 Add `category`, `build_attempt_id`, and `require_build_attempt`
  optional filters to `ShardQueue.claim`, including payload validation helpers.
- [x] 1.2 Add unit tests proving a Web-constrained claim skips Pwn shards even
  when Pwn sorts first.
- [x] 1.3 Add tests for UUID-named attributed shards where category exists only
  in the payload.
- [x] 1.4 Add tests proving `require_build_attempt=True` skips legacy category
  shards even when their category matches.
- [x] 1.5 Validate filter arguments before scanning, require valid UUID
  attribution, and skip malformed payloads, non-regular files, and symlinks in
  constrained mode.
- [x] 1.6 Thread the same filters through `HermesRunner.process_one` and
  `HermesRunner.run`.
- [x] 1.7 Preserve legacy unconstrained claim behavior and test that malformed
  legacy shards are still claimable without filters.
- [x] 1.8 Require canonical `<build_attempt_id>.json` names for exact claims and
  test that duplicate noncanonical files remain pending.

## 2. CLI and HTTP

- [x] 2.1 Add `challenge-factory run --category <web|pwn|re>`.
- [x] 2.2 Add `challenge-factory run --build-attempt <uuid>`.
- [x] 2.3 Add `challenge-factory run --category <web|pwn|re>
  --build-attempts-only`, rejecting `--build-attempts-only` without category.
- [x] 2.4 Reject invalid category/UUID values, `--build-attempts-only` without
  `--category`, `--build-attempts-only` with `--build-attempt`, and
  `--build-attempt` with `--loop` with CLI exit code 2 and no queue mutation.
- [x] 2.5 Add constrained build-worker HTTP endpoints for "next queued attempt
  in category" and single build-attempt execution.
- [x] 2.6 Return clear conflict responses when no matching pending shard exists
  or the matching attempt is already running/terminal.
- [x] 2.7 Reuse the existing local task/process guard so constrained starts
  conflict while any dashboard worker or validation subprocess is already
  running.
- [x] 2.8 Run build staging recovery before deciding that a queued
  build-attempt has no matching pending shard.
- [x] 2.9 Make category start choose the first eligible queued attempt by
  `(created_at, id)` so tests and operator behavior are deterministic.
- [x] 2.10 Match the exact persisted `shard_basename` and verify payload
  `build_attempt_id`, `design_task_id`, and category against the selected DB
  rows before launch; pass both attempt and category filters to the runner.
- [x] 2.11 Extend `TaskManager` with one atomic guarded exact-command start;
  launch exact attempts without `--loop` and return `202` with the selected id.

## 3. Dashboard

- [x] 3.1 Stop the Build Attempts view from calling `/api/actions/worker`.
- [x] 3.2 In list mode, start a category-constrained worker only when the
  category filter is explicit; otherwise require the operator to choose a
  category.
- [x] 3.3 Ensure list-mode category starts resolve to one DB-known queued
  build attempt and then launch by `build_attempt_id`, so they cannot consume
  legacy or unknown attributed shards.
- [x] 3.4 In detail mode, start a build-attempt-constrained worker for that
  attempt.
- [x] 3.5 Keep the legacy global worker endpoint for API compatibility without
  adding another dashboard control for it.

## 4. Verification

- [x] 4.1 Add focused pytest coverage for queue, runner, CLI, and build-worker
  HTTP behavior.
- [x] 4.2 Add JS/static checks for the updated build-attempts view.
- [x] 4.3 Smoke-test pending Pwn + pending Web where a Web-constrained worker
  claims only Web.
- [x] 4.4 Run `openspec validate add-category-safe-build-dispatch --strict`.
- [ ] 4.5 Rebase `add-agent-worker-pool-management` implementation on this
  dispatch contract before enabling its replacement endpoints.
- [x] 4.6 Verify every `ADDED` requirement name is absent from the corresponding
  base spec, because strict validation does not detect duplicate names.
