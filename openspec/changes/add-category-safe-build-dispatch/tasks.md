## 1. Queue and runner contract

- [ ] 1.1 Add `category`, `build_attempt_id`, and `require_build_attempt`
  optional filters to `ShardQueue.claim`, including payload validation helpers.
- [ ] 1.2 Add unit tests proving a Web-constrained claim skips Pwn shards even
  when Pwn sorts first.
- [ ] 1.3 Add tests for UUID-named attributed shards where category exists only
  in the payload.
- [ ] 1.4 Add tests proving `require_build_attempt=True` skips legacy category
  shards even when their category matches.
- [ ] 1.5 Thread the same filters through `HermesRunner.process_one` and
  `HermesRunner.run`.
- [ ] 1.6 Preserve legacy unconstrained claim behavior and test that malformed
  legacy shards are still claimable without filters.

## 2. CLI and HTTP

- [ ] 2.1 Add `challenge-factory run --category <web|pwn|re>`.
- [ ] 2.2 Add `challenge-factory run --build-attempt <uuid>`.
- [ ] 2.3 Add `challenge-factory run --category <web|pwn|re>
  --build-attempts-only`, rejecting `--build-attempts-only` without category.
- [ ] 2.4 Reject invalid category/UUID values, `--build-attempts-only` without
  `--category`, and `--build-attempt` with `--loop` with CLI exit code 2 and no
  queue mutation.
- [ ] 2.5 Add constrained build-worker HTTP endpoints for "next queued attempt
  in category" and single build-attempt execution.
- [ ] 2.6 Return clear conflict responses when no matching pending shard exists
  or the matching attempt is already running/terminal.
- [ ] 2.7 Reuse the existing local task/process guard so constrained starts
  conflict while any dashboard worker or validation subprocess is already
  running.
- [ ] 2.8 Run build staging recovery before deciding that a queued
  build-attempt has no matching pending shard.
- [ ] 2.9 Make category start choose the first eligible queued attempt by
  `(created_at, id)` so tests and operator behavior are deterministic.

## 3. Dashboard

- [ ] 3.1 Stop the Build Attempts view from calling `/api/actions/worker`.
- [ ] 3.2 In list mode, start a category-constrained worker only when the
  category filter is explicit; otherwise require the operator to choose a
  category.
- [ ] 3.3 Ensure list-mode category starts resolve to one DB-known queued
  build attempt and then launch by `build_attempt_id`, so they cannot consume
  legacy or unknown attributed shards.
- [ ] 3.4 In detail mode, start a build-attempt-constrained worker for that
  attempt.
- [ ] 3.5 Keep any legacy global worker control visually separate from
  build-attempt/category controls.

## 4. Verification

- [ ] 4.1 Add focused pytest coverage for queue, runner, CLI, and build-worker
  HTTP behavior.
- [ ] 4.2 Add JS/static checks for the updated build-attempts view.
- [ ] 4.3 Smoke-test pending Pwn + pending Web where a Web-constrained worker
  claims only Web.
- [ ] 4.4 Run `openspec validate add-category-safe-build-dispatch --strict`.
