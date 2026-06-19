## ADDED Requirements

### Requirement: Pool dispatch is exact, authorized, and capacity bounded

The worker pool SHALL dispatch DB-known build attempts to project agents. A
claim SHALL require an enabled non-deleted agent, an available reserved slot,
and `build:<category>` capability. The selected attempt SHALL be converted to
one exact `build_attempt_id`; worker/profile names SHALL NOT select categories.

#### Scenario: Web slot cannot consume Pwn work

- **GIVEN** Web and Pwn attempts are queued
- **AND** agent `web-01` has only `build:web`
- **WHEN** one of its slots claims work
- **THEN** the claim identifies one exact Web build attempt
- **AND** no Pwn shard is moved or leased

#### Scenario: Concurrent claim does not exceed capacity

- **GIVEN** agent `web-01` has `max_concurrency = 2`
- **AND** two slots already own active executions
- **WHEN** another supervisor loop attempts a claim
- **THEN** no third execution is assigned

### Requirement: Execution ownership uses leases and fencing

Every pool execution SHALL have a unique id, claim token, owner agent and slot,
heartbeat, and lease expiry. Heartbeat, publication, completion, and failure
updates SHALL match the current execution id, owner, and claim token. Recovery
of an expired execution SHALL issue a new token.

#### Scenario: Expired process cannot publish

- **GIVEN** execution `E1` loses its lease and recovery issues a new claim token
- **WHEN** the old process finishes generation
- **THEN** its publication request is rejected
- **AND** its output remains quarantined for audit or cleanup

#### Scenario: Heartbeat keeps a valid lease

- **GIVEN** a running execution with a current token
- **WHEN** its slot heartbeats before expiry
- **THEN** the lease is extended atomically
- **AND** no other slot may recover it

### Requirement: Build execution uses a unique ephemeral sandbox

Each build execution SHALL use a uniquely named non-persistent terminal
sandbox. The current attempt input SHALL be mounted read-only and its staging
output SHALL be the only writable artifact root. A build attempt SHALL NOT use
the shared Hermes terminal task `default` or reuse terminal workspace state
from another attempt.

#### Scenario: Stale Pwn workspace is invisible to Web execution

- **GIVEN** a previous Pwn execution created files in its sandbox
- **WHEN** a Web execution starts
- **THEN** it receives a different empty task workspace
- **AND** the previous Pwn files cannot be read or modified

#### Scenario: Parallel executions have distinct output roots

- **GIVEN** two slots run two Web attempts concurrently
- **WHEN** both write generated files
- **THEN** each writes only under its own execution output root
- **AND** neither output tree is visible as writable to the other

### Requirement: Runtime path preflight precedes model invocation

Prompt paths SHALL be paths visible inside the execution runtime. Before model
invocation, preflight SHALL verify from the same runtime boundary that the exact
leased shard and required references are readable, the output root is writable,
and shard id/category matches the execution. Failure SHALL be classified as
infrastructure failure without invoking Hermes.

#### Scenario: Host-only absolute shard path fails closed

- **GIVEN** prompt preparation references a host path absent from the sandbox
- **WHEN** preflight runs
- **THEN** Hermes is not invoked
- **AND** the execution error identifies the inaccessible required input

#### Scenario: Agent does not search for replacement input

- **GIVEN** the leased shard cannot be read
- **WHEN** preflight fails
- **THEN** no model process starts
- **AND** no other shard or challenge tree is searched as a substitute

### Requirement: Progress reporting crosses a supported boundary

Build progress SHALL be recorded by the host runner or through an authenticated
local side channel reachable from the sandbox. Prompts SHALL NOT require a
container to invoke a host-only interpreter or CLI path.

#### Scenario: Container cannot access host progress CLI

- **GIVEN** the configured sandbox cannot execute the host progress command
- **WHEN** execution preflight evaluates progress reporting
- **THEN** it selects the supported host-owned reporting path or fails closed
- **AND** it does not provide the unusable command to Hermes

### Requirement: Artifact publication is allowlisted and fenced

Hermes SHALL write only to execution staging. The host publisher SHALL reject
symlinks, special files, absolute/traversal paths, unexpected category roots,
unexpected challenge ids, and metadata identity mismatches. It SHALL run
deterministic validation, recheck the claim token, and atomically publish only
accepted output.

#### Scenario: Web execution emits Pwn directory

- **GIVEN** a Web execution output contains `pwn/pwn-0001-*`
- **WHEN** publication validation runs
- **THEN** the execution fails scope validation
- **AND** no Pwn or Web output from that execution is published

#### Scenario: Validation failure leaves production unchanged

- **GIVEN** staged output fails deterministic challenge validation
- **WHEN** publication is attempted
- **THEN** the existing `work/challenges` tree is unchanged
- **AND** staged logs and manifest remain attributable to the execution

### Requirement: A single-host supervisor manages slots safely

The system SHALL provide a bounded local supervisor with a singleton leadership
lease or equivalent DB coordination. It SHALL reconcile enabled agents into
slots subject to per-agent and global concurrency limits, heartbeat its process
and active executions, apply restart backoff, and stop replacement claims for
draining or disabled agents.

#### Scenario: Two dashboard processes do not create two pools

- **GIVEN** two server processes attempt to start local supervision
- **WHEN** both contend for leadership
- **THEN** at most one owns the supervisor lease
- **AND** the other reports standby/conflict without spawning pool workers

#### Scenario: Draining reaches stopped without replacement

- **GIVEN** a draining agent owns one execution
- **WHEN** that execution reaches a terminal state
- **THEN** no replacement is claimed
- **AND** the agent has zero active slots after reconciliation

### Requirement: Pool audit snapshots are immutable

Each execution SHALL retain the agent/profile/category values used at claim
time, sandbox policy version, input and output manifest hashes, token generation,
timestamps, exit classification, and log locations. Later agent/profile changes
or soft deletion SHALL NOT rewrite execution history.

#### Scenario: Profile rebinding preserves running history

- **GIVEN** execution `E1` used profile `web-v1`
- **WHEN** the agent is later rebound to `web-v2`
- **THEN** `E1.profile_name_used` remains `web-v1`
- **AND** later executions record `web-v2`

### Requirement: Unsafe partial pool configurations fail closed

Pool build start SHALL be unavailable unless constrained dispatch, lease/fencing,
runtime preflight, isolated staging, and guarded publication are enabled. The
server SHALL derive readiness; the client SHALL NOT infer it from configured
agents alone.

#### Scenario: Registry exists but sandbox isolation is unavailable

- **GIVEN** agents and capabilities are configured
- **AND** isolated execution staging is unavailable
- **WHEN** an operator attempts to start the pool
- **THEN** the request is rejected as not ready
- **AND** no legacy global worker is started as fallback
