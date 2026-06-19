## Context

Hermes profiles are execution identities, not queue permissions or filesystem
sandboxes. The current shard runner combines four concerns that a pool must
separate: queue selection, execution ownership, model workspace, and artifact
publication. It also invokes Hermes from the repository root with host absolute
paths while the configured terminal may use an unmounted persistent container.

This design establishes a safe single-host worker pool. PostgreSQL is the
authority for agents, slots, leases, and execution history. The file queue may
remain as a compatibility/publication substrate during migration, but filename
ordering is never a pool dispatch policy.

## Goals / Non-Goals

**Goals:**

- Manage named project agents and bind each to an existing Hermes profile.
- Run multiple bounded local worker slots with explicit capabilities.
- Claim only authorized category/attempt work using atomic leases and fencing.
- Give every attempt a clean workspace with accessible, immutable inputs.
- Prevent a Web attempt from reading, modifying, or publishing Pwn/Re artifacts.
- Recover safely from worker, Hermes, dashboard, and host process failures.
- Preserve an auditable snapshot of the identity and configuration used.

**Non-Goals:**

- No cross-host scheduler or remote deployment protocol.
- No storage of profile secrets or `.env` contents in PostgreSQL.
- No live editing of Hermes secret/config files from the dashboard.
- No migration of existing research/design bindings in this change.
- No promise that a Hermes profile itself is a security sandbox.

## Decisions

### Decision 1: distinguish agent, slot, execution, profile, and sandbox

- **Agent**: project-owned schedulable identity and capability set.
- **Slot**: one unit of local concurrency belonging to an agent.
- **Execution**: one leased attempt handled by one slot.
- **Hermes profile**: stable model/provider configuration identity.
- **Sandbox**: unique ephemeral filesystem/process environment for one execution.

These identifiers must not be substituted for one another. In particular,
profile names and worker display names never imply queue category.

### Decision 2: project capabilities authorize dispatch

Capabilities are normalized project-owned rows. Initial codes are `research`,
`design`, `build:web`, `build:pwn`, and `build:re`. Claim code resolves the
agent, requires `enabled` control state and a matching capability, and selects a
DB-known eligible attempt. Unknown capability strings are rejected.

Build dispatch uses `build_attempt_id` as the exact identity. A category may be
used to select the next eligible DB row, but the resulting execution always
claims one exact attributed attempt. Payload category is verified again before
execution.

### Decision 3: database lease and fencing token own an execution

Claim is a single transaction that:

1. locks/selects an eligible queued attempt;
2. verifies agent capability and available slot capacity;
3. creates an execution row with `agent_id`, `slot_id`, a random claim token,
   lease expiry, and immutable agent/profile/category snapshots;
4. transitions the attempt to the claimed/running protocol state.

Heartbeat and every terminal mutation require execution id, agent id, and the
current claim token. Lease recovery issues a new token. A process holding an old
token may finish locally but cannot publish artifacts or mark completion.

The file move may mirror DB state during migration, but it is not sufficient
ownership for pool execution.

### Decision 4: control state and process health are separate

Operator state is `enabled`, `draining`, or `disabled`.

- Enabled agents may receive new work while capacity exists.
- Draining agents finish owned executions and receive no replacements.
- Disabled agents receive no new work; disabling does not silently kill active
  work unless an explicit force-stop operation is added later.

Health is derived from supervisor/slot heartbeat and execution state. An agent
with no started supervisor is `stopped`, not `offline`. `offline` means a
previously running supervisor missed its heartbeat. This avoids making a newly
created enabled agent permanently unable to start.

### Decision 5: a bounded local supervisor enforces capacity

One supervisor instance owns a process identity and heartbeat. It reconciles
desired enabled agents into at most `max_concurrency` slots per agent and a
configured global limit. Capacity reservation and claim happen atomically so
two supervisor loops cannot oversubscribe the same agent.

The supervisor starts workers with explicit `agent_id`, `slot_id`, and
execution id. It records process start/exit, applies backoff after repeated
failures, stops replacement claims while draining, and recovers expired leases.
Multiple dashboard server processes must not each create an uncoordinated pool;
supervisor leadership uses a DB advisory lock or equivalent singleton lease.

### Decision 6: every execution receives an ephemeral sandbox

The runner creates an execution root such as:

```text
work/executions/<execution-id>/
  input/
  output/
  logs/
  manifest.json
```

`input/` contains the claimed shard, generation profile, and only required
category/common references. It is mounted read-only. `output/` is the only
writable artifact mount. The terminal sandbox is non-persistent and uniquely
named by execution id; the shared Hermes `task=default` workspace is forbidden.

Stable profile configuration may be read through a controlled profile home,
but terminal workspace, shell state, file caches, and task memory are not reused
between build attempts.

### Decision 7: prompts use runtime-visible paths and preflight is mandatory

Prompt rendering receives an explicit runtime path map, for example
`/input/shard.json`, `/input/references`, and `/output`. It must not embed host
paths that are absent from the sandbox.

Before invoking the model, the runner probes from the same runtime boundary and
verifies that the shard and required references are readable, output is
writable, and the shard id/category matches the leased execution. Preflight
failure marks the execution infrastructure-failed without consuming a model
attempt. The model must never search for substitute shard files.

Progress reporting moves to a host-owned side channel or authenticated local
API. A prompt must not instruct a container to execute a host-only Python path.

### Decision 8: output is staged, validated, and atomically published

Hermes never writes directly to `work/challenges`. After it exits, the host:

1. rejects symlinks, absolute paths, traversal, devices, and unexpected roots;
2. requires output only for the leased category and challenge ids;
3. verifies metadata id/category and the expected directory count;
4. runs deterministic validation from a controlled host context;
5. rechecks the fencing token;
6. atomically publishes accepted directories and records hashes/manifest.

Any Pwn/Re output from a Web execution is an execution failure and is never
published. Existing challenge trees outside the staging root are unreachable
to the sandbox, so rollback does not depend on detecting arbitrary mutations.

### Decision 9: profile lifecycle remains separate and guarded

The backend wraps Hermes profile list/show/create/delete using argv arrays,
validated names, timeouts, and structured errors. Profile creation and agent
creation are not transactionally coupled. Profile deletion is rejected while
referenced by any non-deleted agent, active execution, or existing
`hermes_profile_bindings` row. Secrets and profile file contents are not
returned by APIs or persisted in PostgreSQL.

### Decision 10: immutable audit snapshots explain historical behavior

Execution rows retain nullable foreign keys plus immutable values used at
claim time: agent name, profile name, capabilities/category, model/provider
identifier when available, sandbox policy version, input manifest hash, output
manifest hash, claim token generation, timestamps, exit classification, and
log paths. Later agent/profile changes never rewrite history.

### Decision 11: legacy execution is explicitly separated

Legacy `challenge-factory run --worker W` remains available only for explicit
whole-queue shard administration during migration. Category/build-attempt UI
and pool APIs cannot call it. Pool build execution is disabled until constrained
dispatch, sandbox preflight, fencing, and staged publication are all active.

### Decision 12: rollout is fail-closed and phased

1. Add schema, registry, audit, and profile wrappers.
2. Add constrained exact-attempt dispatch.
3. Add isolated execution staging and publication without concurrency.
4. Enable one supervised slot and run fault-injection tests.
5. Enable multiple slots under a global limit.
6. Remove the legacy dashboard global-worker action from build surfaces.

Feature readiness is server-derived. The UI must not offer pool start when a
required safety component is missing.

## Risks / Trade-offs

- Staging duplicates some files and images, but provides a hard publication
  boundary and deterministic cleanup.
- Database leases add complexity, but file rename alone cannot fence a stale
  process after recovery.
- A single-host leader is not highly available; it is intentionally simpler
  than an unsafe pseudo-distributed pool.
- Stable Hermes profiles can still accumulate profile-level memory. Build
  prompts use fresh conversation history, and task filesystem/shell state is
  always isolated. If profile memory can influence correctness, build profiles
  must disable it or use an execution-specific derived home.
- Force-stopping native build processes requires process-group termination and
  is deferred unless needed for shutdown correctness; lease fencing still
  prevents late publication.

## Migration Plan

The phased rollout in Decision 12 is normative. Existing queued attempts remain
compatible, but pool execution claims only attributed, schema-valid build
attempt shards. No build agent is enabled by default.
