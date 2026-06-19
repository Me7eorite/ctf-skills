## MODIFIED Requirements

### Requirement: build_attempts table is the editorial unit of building

In addition to the existing build attempt fields and state-machine behavior,
build execution audit SHALL be able to record nullable project agent identity
and the Hermes profile name used for agent-owned execution. These values are
historical audit data: changing an agent's profile binding later or
soft-deleting an agent SHALL NOT rewrite completed or running attempt history.

#### Scenario: Build attempt records agent and profile used

- **GIVEN** agent `web-01` is bound to Hermes profile `web-builder-01`
- **WHEN** that agent claims and executes build attempt `A`
- **THEN** attempt `A` records the project agent id and agent name used
- **AND** attempt `A` records `profile_name_used = 'web-builder-01'`

### Requirement: BuildOrchestrationService submits and retries builds

Build submission and retry SHALL retain the existing recoverable publication
protocol for creating build attempts and staging build work. Agent
authorization and worker-pool claim decisions SHALL be owned by worker-pool
claim code, not by submission itself.

#### Scenario: Submission does not require an agent

- **GIVEN** a design task is approved for build
- **WHEN** build submission creates a build attempt and stages work
- **THEN** submission succeeds without requiring an agent id
- **AND** the later worker-pool claim is responsible for assigning an agent

#### Scenario: Retry preserves historical audit

- **GIVEN** build attempt `A` was previously executed by agent `web-01`
- **WHEN** a retry creates or restages another execution attempt
- **THEN** historical audit values on the previous execution are not rewritten
- **AND** the new execution records the agent/profile values used by that run
