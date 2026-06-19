## Context

Hermes profiles are useful execution identities. A profile can have its own
configuration, environment, memory, sessions, skills, and status directory, and
Hermes can be invoked with a profile name. The project should use that as the
Hermes-side identity, but it must not treat profiles as queue permissions or
filesystem isolation.

The project already has limited role-to-profile binding for research. That is
not enough for a worker pool: pool members need stable ids, enabled/draining
state, capabilities, health information, and a way for the dashboard to add or
remove them.

## Goals / Non-Goals

**Goals:**

- Let operators create and delete project agents from the dashboard.
- Let agents join or leave the worker pool without editing database rows by
  hand.
- Bind each agent to a Hermes profile name.
- Assign project-owned capabilities such as `research`, `design`, `build:web`,
  `build:pwn`, and `build:re`.
- Define the contract that future build workers use to claim only categories
  allowed by their agent capabilities.
- Provide profile discovery/creation/deletion helpers while keeping profile
  contents out of PostgreSQL.

**Non-Goals:**

- No full supervisor implementation is required in this proposal.
- No sandboxing or filesystem permission isolation through Hermes profiles.
- No direct dashboard editing of Hermes profile `.env` or secret values.
- No cross-host distributed worker deployment in this change.
- No replacement of the existing `agent_roles` or `hermes_profile_bindings`
  research/design profile bindings.

## Decisions

### Decision 1: project agents are separate from Hermes profiles

The project owns an `agents` table. Each row has a stable UUID, unique name,
optional description, `hermes_profile_name` string, `control_state`, execution
settings, heartbeat/error fields, soft-delete timestamp, and timestamps. The
profile name is passed to Hermes invocation, but the database does not store
profile config, environment variables, memory, sessions, or secrets.

Deleting an agent soft-deletes the project row by default and disables future
claims. Hard deletion is not part of the normal dashboard flow because build
attempt audit rows may reference historical agent identities. Deleting the
underlying Hermes profile is a separate explicit destructive action.

### Decision 2: capabilities are project-owned authorization

Agent capabilities are stored in PostgreSQL and are the only source used by
queue claim code. A Hermes profile named `web-builder` does not gain Web claim
permission unless the agent row has `build:web`.

Capability assignments are stored separately from the `agents` row so future
capability codes do not require a new agent table shape. The valid code set is
still project-owned and constrained by a lookup table or equivalent validated
enumeration so worker authorization never trusts arbitrary user-provided
strings. The initial capability set is:

- `research`
- `design`
- `build:web`
- `build:pwn`
- `build:re`

The schema should allow adding future capabilities without a new table shape.

### Decision 3: control state is separate from runtime state

Agents have operator-controlled pool membership:

- `enabled`: may claim new work when capabilities match.
- `disabled`: cannot claim new work.
- `draining`: cannot claim new work but may finish active work.

Runtime display state is derived, not directly edited:

- `idle`: enabled and no active lease.
- `running`: has one or more active leases.
- `offline`: expected heartbeat is missing.
- `error`: last health check or process start failed.

Worker-pool claim must reject agents with `control_state` `disabled` or
`draining`, and it must reject runtime states `offline` or `error` unless a
later supervisor design defines an explicit repair/re-enable flow.

### Decision 4: Hermes profile commands are wrapped, not trusted

The backend may expose list/create/show/delete helpers that call Hermes profile
commands through the same configured Hermes executable resolution used
elsewhere in the project. The wrapper validates profile names, passes names as
argument-array entries, captures stdout/stderr, applies timeouts, and returns
structured errors. It does not parse or persist profile secrets. It does not
make profile existence equivalent to agent existence: operators can bind an
agent only to a profile that passes the wrapper's show or list check. Profile
creation and agent row creation are modeled as separate operations so the UI
cannot imply an atomic cross-system transaction.

Agent names and Hermes profile names use the same conservative validation
shape unless an existing Hermes command rejects them more strictly:
`[A-Za-z0-9_.-]{1,64}`. Values outside that shape are rejected before any
database write or subprocess invocation.

Profile deletion must also check existing `hermes_profile_bindings`, not only
the new `agents` table, so the current research/design role bindings cannot be
broken through the new dashboard helper.

### Decision 5: worker pool uses agent id for claims and audit

Future worker-pool claim APIs take `agent_id`, resolve the agent, validate its
control state, derived runtime state, and capabilities, then claim a matching
task. For build work, the claim uses the constrained build-dispatch contract
from `add-category-safe-build-dispatch` so category capability and file-queue
claim cannot diverge. If that dependency is not present, the implementation may
ship agent registry/profile management only, but it must not expose an
agent-owned build claim path.

Historical rows record nullable `agent_id`, `agent_name_used`, and
`profile_name_used` values for agent-owned execution attempts. Legacy
non-agent executions remain valid with these values unset or partially unset.
Later profile changes or agent soft-deletion do not rewrite history.

### Decision 6: existing role bindings remain authoritative for current research

The current `agent_roles` and `hermes_profile_bindings` tables remain
authoritative for the already-implemented research/design profile resolution
paths. This change may display profile names that also appear in those
bindings, but it does not migrate or reinterpret those bindings as worker-pool
agents. A later migration can map role bindings into agents after the worker
pool is operational.

## Risks / Trade-offs

- **Profile is not a sandbox.** The design explicitly keeps filesystem and
  queue authorization in the project.
- **Deleting a profile can break other agents or existing role bindings.**
  Profile deletion must reject live bindings in both the new agent registry and
  existing Hermes profile binding tables.
- **Worker state can drift from process reality.** Heartbeats and health checks
  are needed before any supervisor is treated as authoritative.
- **Existing research role binding overlaps conceptually.** The migration plan
  leaves the old binding in place until a later cleanup change.
- **Max concurrency is only a registry setting until a supervisor exists.**
  The dashboard may store it, but current local start buttons must not imply
  that multiple supervised workers are already implemented.

## Migration Plan

1. Add registry schema and seed no build agents by default.
2. Add API and dashboard management without starting new workers.
3. Add Hermes profile wrapper endpoints.
4. Connect build claim to agent capabilities after
   `add-category-safe-build-dispatch` is present.
5. Add supervisor/start-stop behavior in a later implementation pass.
