## ADDED Requirements

### Requirement: Agents are persisted worker-pool members

The system SHALL maintain a project-owned `agents` registry. Each agent row
SHALL have a stable id, unique name, optional description, a
`hermes_profile_name`, operator `control_state`, max concurrency, lease
seconds, heartbeat timestamp, last error, soft-delete timestamp, and
timestamps. Valid control states SHALL be `enabled`, `disabled`, and
`draining`.

Hermes profile contents SHALL NOT be persisted in PostgreSQL.
Agent names and Hermes profile names SHALL be validated before persistence or
subprocess invocation, using a conservative ASCII shape compatible with
letters, digits, `_`, `.`, and `-`.

#### Scenario: Operator creates an agent bound to an existing profile

- **GIVEN** Hermes reports that profile `web-builder-01` exists
- **WHEN** the operator creates an agent named `web-01` bound to that profile
- **THEN** the agent row is persisted with `hermes_profile_name =
  'web-builder-01'`
- **AND** no profile secrets or profile files are copied into PostgreSQL

#### Scenario: Agent name is unique

- **GIVEN** an agent named `web-01` already exists
- **WHEN** another create request uses name `web-01`
- **THEN** the request is rejected before any new row is written

#### Scenario: Invalid profile name is rejected before subprocess use

- **WHEN** an operator submits profile name `web builder;rm`
- **THEN** the request is rejected by validation
- **AND** no database row is written
- **AND** no Hermes subprocess is invoked

#### Scenario: Existing role bindings are not migrated

- **GIVEN** existing `agent_roles` and `hermes_profile_bindings` rows configure
  research or design profile resolution
- **WHEN** the agent registry schema is added
- **THEN** those existing bindings remain authoritative for their current
  execution paths
- **AND** no research or design worker-pool agent is created unless a later
  migration explicitly does so

### Requirement: Agent capabilities gate task claiming

The system SHALL store project-owned agent capabilities separately from the
agent row. Initial capabilities SHALL include `research`, `design`,
`build:web`, `build:pwn`, and `build:re`. Queue claim code SHALL use these
capabilities as the authorization source. Unknown capability codes SHALL be
rejected before persistence.

#### Scenario: Unknown capability is rejected

- **WHEN** an operator creates or updates an agent with capability
  `build:crypto`
- **THEN** the request is rejected unless that capability code has first been
  added to the project-owned allowed set
- **AND** no assignment row is written for the unknown capability

#### Scenario: Build agent can claim only its category

- **GIVEN** agent `web-01` has capability `build:web` and lacks `build:pwn`
- **WHEN** the worker pool asks `web-01` to claim build work
- **THEN** it may claim Web build work
- **AND** it cannot claim Pwn build work even if the bound Hermes profile name
  contains `pwn`

#### Scenario: Profile name does not grant permission

- **GIVEN** an agent is bound to Hermes profile `web-builder`
- **AND** the agent has no `build:web` capability
- **WHEN** the agent asks to claim Web build work
- **THEN** the claim is rejected

### Requirement: Agent control state and health control worker-pool membership

Agents SHALL support operator control states `enabled`, `disabled`, and
`draining`. Runtime health values such as `idle`, `running`, `offline`, and
`error` SHALL be derived from active work, heartbeat, and last-error data.
Disabled, draining, offline, and error agents SHALL NOT claim new work.
Draining agents MAY finish already-owned work.

#### Scenario: Disabled agent cannot claim work

- **GIVEN** agent `web-01` has control state `disabled`
- **WHEN** the worker pool attempts to claim work for that agent
- **THEN** no task is claimed
- **AND** the reason identifies the disabled control state

#### Scenario: Draining agent finishes but does not claim

- **GIVEN** agent `web-01` has control state `draining` and owns one running
  task
- **WHEN** the task finishes
- **THEN** the active-work summary no longer shows that running task
- **AND** it does not claim a replacement task while draining

#### Scenario: Offline health rejects claims

- **GIVEN** agent `web-01` has control state `enabled`
- **AND** its heartbeat is stale enough for derived health `offline`
- **WHEN** the worker pool attempts to claim work for that agent
- **THEN** no task is claimed
- **AND** the reason identifies offline health

### Requirement: Dashboard exposes agent management

The dashboard SHALL expose an Agents view. The view SHALL list agents with
profile name, capabilities, control state, derived health, heartbeat, last
error, and active-work summary. It SHALL provide create, edit, enable,
disable, drain, and soft-delete actions. Deleting an agent SHALL NOT delete
the underlying Hermes profile.

#### Scenario: Agent deletion preserves profile by default

- **GIVEN** agent `web-01` is bound to profile `web-builder-01`
- **WHEN** the operator deletes the agent
- **THEN** the project agent is soft-deleted and disabled for future claims
- **AND** profile `web-builder-01` is not deleted from Hermes

#### Scenario: Active agent deletion is rejected

- **GIVEN** agent `web-01` owns running work
- **WHEN** the operator requests deletion
- **THEN** the response is a conflict
- **AND** the UI offers disable or drain instead

### Requirement: HTTP API exposes agent management

The system SHALL expose HTTP APIs for agent list, create, detail, patch,
enable, disable, drain, soft-delete, and Hermes profile helpers. Agent
responses SHALL include control state and derived health separately. Profile
helper endpoints SHALL be separate from `/api/agents` so agent deletion cannot
implicitly delete a Hermes profile.

`GET /api/agents` SHALL exclude soft-deleted agents by default and provide an
explicit include-deleted option for audit/debug views.

#### Scenario: API patch separates control from health

- **GIVEN** agent `web-01` has derived health `running`
- **WHEN** the operator patches `control_state` to `draining`
- **THEN** the response shows `control_state = 'draining'`
- **AND** derived health remains computed from active work and heartbeat data

#### Scenario: Agent delete does not call profile deletion

- **GIVEN** agent `web-01` is bound to profile `web-builder-01`
- **WHEN** `DELETE /api/agents/{id}` succeeds
- **THEN** no Hermes profile delete wrapper is invoked
- **AND** the agent is returned or later listed as soft-deleted according to
  the API filtering rules

### Requirement: Hermes profile helpers are safe wrappers

The backend MAY expose Hermes profile list, show, create, and delete helpers.
The helpers SHALL validate profile names, pass names as subprocess arguments
rather than shell-concatenated strings, use the configured Hermes executable
resolution, enforce timeouts, and return structured errors. They SHALL NOT
expose or persist profile `.env` contents or secret values.

#### Scenario: Creating a profile before binding an agent

- **WHEN** the operator creates Hermes profile `web-builder-01` through the
  profile helper
- **THEN** the backend invokes only the Hermes profile creation wrapper
- **AND** no agent row is committed by the profile helper itself
- **WHEN** the operator later creates agent `web-01` bound to
  `web-builder-01`
- **THEN** the agent creation path verifies that the profile exists before
  committing the agent row

#### Scenario: Profile deletion refuses agent bindings

- **GIVEN** profile `web-builder-01` is referenced by one or more agents
- **WHEN** the operator requests profile deletion
- **THEN** the request is rejected
- **AND** the profile remains available to Hermes

#### Scenario: Profile deletion refuses existing role bindings

- **GIVEN** profile `default` is referenced by `hermes_profile_bindings`
- **WHEN** the operator requests profile deletion through the dashboard helper
- **THEN** the request is rejected
- **AND** the profile remains available to Hermes

### Requirement: Agent-owned build claims are capability gated

Future worker-pool build claim APIs SHALL accept a project `agent_id`, resolve
the agent, reject agents that cannot claim new work, require the matching
`build:<category>` capability, and then use the constrained build-dispatch
contract from `add-category-safe-build-dispatch` for the final file-queue
claim. If the constrained build-dispatch contract is not available, the system
SHALL NOT expose agent-owned build claim endpoints.

#### Scenario: Web agent cannot claim Pwn build work

- **GIVEN** Web and Pwn build attempts are queued
- **AND** agent `web-01` has only `build:web`
- **WHEN** the worker pool claims build work for `web-01`
- **THEN** only Web-attributed work may be claimed
- **AND** Pwn work remains available for an agent with `build:pwn`

#### Scenario: Missing constrained dispatch disables build claim

- **GIVEN** the agent registry is implemented
- **AND** constrained build dispatch is not implemented
- **WHEN** an operator or worker attempts an agent-owned build claim
- **THEN** the request is rejected as unsupported
- **AND** no shard is moved from the file queue

#### Scenario: Legacy non-agent worker remains valid

- **GIVEN** a legacy local `challenge-factory run` execution path starts
  without an `agent_id`
- **WHEN** it records or reconciles build execution state
- **THEN** nullable agent audit fields remain valid
- **AND** the legacy execution path is not forced to create an agent row
