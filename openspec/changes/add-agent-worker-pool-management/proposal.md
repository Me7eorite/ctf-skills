## Why

The project is moving toward a worker pool, but the current dashboard only
starts one local anonymous worker process. Operators need to manage named
agents, bind them to Hermes profiles, assign capabilities, and choose which
agents participate in the pool. Hermes profiles provide separate Hermes
configuration/state directories, but they are not a security sandbox and do
not express project queue permissions.

Agent management should therefore be project-owned: PostgreSQL records which
agents exist, which Hermes profile each one uses, which work categories it may
claim, and whether it is enabled, disabled, or draining. Runtime conditions
such as idle, running, offline, and error are derived from heartbeats, active
leases, and last-error fields instead of being operator lifecycle states.

## What Changes

- Add a project-level Agent Registry for worker-pool members.
- Bind each agent to one Hermes profile name without persisting profile
  contents or secrets in PostgreSQL.
- Add dashboard and HTTP APIs to list, create, update, disable, drain, and
  soft-delete agents.
- Add optional Hermes profile lifecycle helpers for list/create/delete, guarded
  by project-side validation and explicit destructive confirmation. Profile
  creation and agent creation remain separate operations because Hermes file
  changes cannot share a PostgreSQL transaction.
- Make worker-pool claim decisions use agent capabilities rather than the
  selected Hermes profile name.
- Depend on `add-category-safe-build-dispatch` so build agents can safely claim
  category- or attempt-scoped work.
- Preserve the existing `agent_roles` and `hermes_profile_bindings` research
  binding as-is; this change adds worker-pool agents and does not migrate the
  current research execution path.

## Capabilities

### New Capabilities

- `worker-agent-management`: persisted agents, Hermes profile bindings,
  capability assignment, dashboard management, and worker-pool membership.

### Modified Capabilities

- `build-orchestration`: build attempt audit can record agent-owned
  executions, and future agent-owned build claim paths claim only work
  permitted by the agent's capabilities.

## Impact

- **Code**: add persistence models/repositories, an agent service, web
  endpoints, and a dashboard Agents view.
- **Database**: add agent registry tables, capability assignments, and nullable
  historical audit columns on build attempts for agent id/name and profile name
  used by agent-owned executions.
- **Hermes**: wrap profile list/create/delete/show commands without storing
  profile files, `.env`, or secrets in the project database.
- **Worker pool**: agents become the identities used for future worker
  supervision and task claim. This proposal defines the registry and claim
  authorization contract; it does not by itself require a full multi-process
  supervisor.
- **Dependencies**: requires the explicit constrained build-dispatch contract
  from `add-category-safe-build-dispatch`.
- **Tests**: add repository/service/API/UI coverage and subprocess-wrapper
  tests for Hermes profile command handling.
