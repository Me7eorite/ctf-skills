## 1. Prerequisites and schema

- [ ] 1.1 Confirm or incorporate `add-category-safe-build-dispatch`; do not
  enable pool claims while exact-attempt dispatch is absent.
- [ ] 1.2 Add `agents`, normalized capability assignments, supervisor identity,
  agent slots, and execution rows with lease/fencing fields and constraints.
- [ ] 1.3 Add immutable execution snapshot fields for agent/profile/category,
  sandbox policy, manifests, timestamps, exit class, and log paths.
- [ ] 1.4 Add nullable current/latest execution attribution to build attempts
  without rewriting historical attempts.
- [ ] 1.5 Define indexes and uniqueness constraints that prevent duplicate active
  slot ownership and capacity oversubscription.
- [ ] 1.6 Preserve existing `agent_roles` and `hermes_profile_bindings`.

## 2. Agent registry and profile wrapper

- [ ] 2.1 Add DTOs, repository/service methods, and validation for names,
  profiles, capabilities, limits, control transitions, and soft deletion.
- [ ] 2.2 Derive `stopped`, `idle`, `running`, `offline`, and `error` separately
  from operator control state.
- [ ] 2.3 Wrap Hermes profile list/show/create/delete with argv arrays, timeouts,
  redacted structured errors, and configured executable resolution.
- [ ] 2.4 Reject profile deletion while referenced by agents, active executions,
  or existing Hermes role bindings.
- [ ] 2.5 Keep profile contents, `.env`, memory, sessions, and secrets out of API
  responses and PostgreSQL.

## 3. Atomic dispatch, leases, and fencing

- [ ] 3.1 Implement transactional exact-attempt claim with capability, control
  state, per-agent capacity, and global capacity checks.
- [ ] 3.2 Issue a random fencing token and lease on every claim/recovery.
- [ ] 3.3 Require owner/token predicates for heartbeat, fail, complete, cancel,
  and publication authorization.
- [ ] 3.4 Recover expired leases without allowing the old process to publish.
- [ ] 3.5 Integrate file-queue movement as a reconciled side effect, not the pool
  ownership authority.
- [ ] 3.6 Define retry/backoff and terminal classifications for infrastructure,
  generation, validation, scope, timeout, cancellation, and lost-lease failure.

## 4. Isolated execution preparation

- [ ] 4.1 Create a unique execution staging root with read-only input, writable
  output, logs, and manifest locations.
- [ ] 4.2 Materialize only the exact shard, generation profile, common guidance,
  and current category reference required by the execution.
- [ ] 4.3 Render prompt paths from an explicit runtime path map; do not expose
  inaccessible host absolute paths.
- [ ] 4.4 Force a unique non-persistent Hermes terminal task/sandbox for each
  execution; reject shared `task=default` execution.
- [ ] 4.5 Define controlled access to the stable Hermes profile without sharing
  task workspace, shell state, or file caches.
- [ ] 4.6 Add same-runtime preflight for readable inputs, writable output,
  identity/category consistency, and supported progress reporting.
- [ ] 4.7 Move progress updates to the host runner or an authenticated local side
  channel instead of a host-only CLI command in the sandbox prompt.

## 5. Guarded artifact publication

- [ ] 5.1 Generate a host-computed input manifest before Hermes invocation.
- [ ] 5.2 Reject symlinks, special files, traversal, absolute paths, unexpected
  roots, categories, ids, and metadata identity mismatches in output.
- [ ] 5.3 Run deterministic challenge validation against staging output.
- [ ] 5.4 Recheck the fencing token immediately before publication.
- [ ] 5.5 Atomically publish the complete accepted output set or publish nothing.
- [ ] 5.6 Record output manifest hashes and retain/quarantine failed staging data
  according to a bounded retention policy.
- [ ] 5.7 Verify that production challenge trees outside the target attempt are
  unchanged by construction, not only by post-hoc diff.

## 6. Single-host supervisor

- [ ] 6.1 Implement supervisor leadership with a PostgreSQL advisory lock or
  renewable singleton lease.
- [ ] 6.2 Reconcile enabled agents into bounded slots under per-agent and global
  concurrency limits.
- [ ] 6.3 Start workers with explicit agent, slot, execution, profile, and token
  context and use process groups for cleanup.
- [ ] 6.4 Heartbeat supervisor, slots, and executions; surface lost ownership and
  stop late completion/publication.
- [ ] 6.5 Implement drain semantics, graceful shutdown, crash reconciliation,
  and restart backoff.
- [ ] 6.6 Start with one slot behind a readiness gate, then enable concurrency
  only after fault-injection tests pass.

## 7. API and dashboard

- [ ] 7.1 Add agent list/create/detail/patch/enable/disable/drain/soft-delete
  APIs and separate safe profile helper APIs.
- [ ] 7.2 Add pool status/start/stop/drain endpoints returning server-derived
  readiness and leadership state.
- [ ] 7.3 Add execution list/detail/log endpoints with agent, attempt, category,
  lease, sandbox, manifest, and exit information.
- [ ] 7.4 Add Agents and Executions dashboard views with control state distinct
  from health and desired capacity distinct from active slots.
- [ ] 7.5 Remove the legacy global worker action from category/build-attempt pool
  surfaces; retain it only in explicitly labeled legacy shard administration.
- [ ] 7.6 Never expose profile secrets, raw environment contents, or unredacted
  subprocess configuration.

## 8. Verification and rollout gates

- [ ] 8.1 Add migration/repository/API/UI tests for registry, capabilities,
  lifecycle, capacity, profile protection, and immutable audit.
- [ ] 8.2 Add concurrency tests proving atomic capacity and exact-category claim.
- [ ] 8.3 Seed stale Pwn/Re files in a prior sandbox and prove a Web execution
  cannot observe or modify them.
- [ ] 8.4 Prove inaccessible host-only prompt paths fail before Hermes invocation.
- [ ] 8.5 Prove an old fencing token cannot heartbeat, complete, or publish after
  lease recovery.
- [ ] 8.6 Prove wrong-category, unexpected-id, symlink, traversal, and partial
  outputs publish nothing.
- [ ] 8.7 Kill Hermes, a slot process, the supervisor, and the dashboard at each
  state transition and verify deterministic recovery.
- [ ] 8.8 Run one-slot soak tests before enabling multi-slot execution.
- [ ] 8.9 Run multi-slot tests with two Web attempts and mixed Web/Pwn queues.
- [ ] 8.10 Run `openspec validate add-agent-worker-pool-management --strict`.

## 9. Migration

- [ ] 9.1 Ship schema and read-only dashboard views with pool execution disabled.
- [ ] 9.2 Enable exact-attempt dispatch and isolated staging for one manual run.
- [ ] 9.3 Enable one supervised slot after readiness checks pass.
- [ ] 9.4 Enable bounded concurrency after soak and fault-injection approval.
- [ ] 9.5 Document rollback: stop new claims, drain/fence active executions, keep
  audit/staging, and restore legacy administration without category UI fallback.
