## ADDED Requirements

### Requirement: Build attempts require solver acceptance before success

Build orchestration SHALL consume solver acceptance evidence for Web and Pwn attempts before marking an attempt successful. A build attempt SHALL remain failed, repairing, or blocked unless the current execution workspace contains a final passed validation round with solver acceptance passed for every Web/Pwn challenge in the attempt. Retry, repair, and revalidate flows SHALL use the same solver acceptance requirement before changing the parent design task to built.

#### Scenario: Successful shard without solver acceptance remains failed
- **WHEN** a Web or Pwn shard reaches a terminal runner state
- **AND** the artifact directory exists
- **BUT** the latest final validation evidence does not show solver acceptance passed
- **THEN** build orchestration SHALL NOT mark the attempt as succeeded
- **AND** the parent design task SHALL NOT become built

#### Scenario: Retry must pass solver acceptance
- **WHEN** an operator retries a failed Web or Pwn build attempt
- **AND** the retry produces artifacts but the reference solver still fails acceptance
- **THEN** the retry attempt SHALL remain failed or blocked
- **AND** the previous attempt's status SHALL NOT be changed

#### Scenario: Revalidate can promote only accepted solver
- **WHEN** an operator revalidates a failed Web or Pwn attempt
- **AND** clean validation passes solver acceptance against the current output tree
- **THEN** orchestration MAY promote the attempt according to existing revalidation rules
- **AND** if solver acceptance fails, the attempt SHALL remain failed with solver acceptance diagnostics

### Requirement: Build diagnostics expose solver acceptance and blocked reason

Build attempt list and detail APIs SHALL expose solver acceptance status, solver acceptance fingerprint, solver-quality diagnostic summaries, and explicit blocked reasons when available. These fields SHALL be derived from the current attempt's validation history, report, progress evidence, or attempt summary and SHALL NOT require scanning unrelated execution histories.

#### Scenario: Attempt detail exposes blocked solver reason
- **WHEN** a Web or Pwn attempt fails because automatic solver repair made no progress
- **THEN** attempt detail SHALL expose the blocked reason and solver-quality diagnostics
- **AND** it SHALL retain the validation failure class and signature from failure governance when available

#### Scenario: Attempt list stays bounded
- **WHEN** the dashboard requests a page of build attempts
- **THEN** solver acceptance fields SHALL be derived only for returned rows or copied summaries
- **AND** the list path SHALL NOT scan all execution workspaces to build a global solver acceptance picture

### Requirement: Regeneration outcomes remain attempt-scoped

Build orchestration SHALL treat solver regeneration, challenge regeneration, and solver blocked outcomes as attempt-scoped. One attempt's solver repair exhaustion or regeneration route SHALL NOT consume the repair budget, retry budget, or status of sibling attempts in the same batch.

#### Scenario: Solver blocked attempt does not stop sibling
- **GIVEN** a batch contains attempts A and B
- **WHEN** attempt A becomes blocked with `solver_unrepairable`
- **THEN** attempt B SHALL continue through its own validation and repair lifecycle
- **AND** B SHALL receive its own solver repair and regeneration budgets

#### Scenario: Challenge regeneration creates a new attempt lineage entry
- **WHEN** orchestration chooses challenge regeneration after solver-only routes fail
- **THEN** the regeneration SHALL be recorded as part of the current attempt context or as a new explicit retry attempt
- **AND** the resulting artifact SHALL still require final solver acceptance before success

### Requirement: Build success uses manifest-bound solver acceptance

Build orchestration SHALL only promote Web and Pwn attempts using solver acceptance evidence tied to the current attempt's output manifest. Attempt list and detail derivation SHALL remain bounded to the returned attempts and SHALL NOT scan unrelated execution workspaces.

#### Scenario: Manifest-stale acceptance cannot promote
- **WHEN** a Web or Pwn attempt has an older passed solver acceptance round
- **AND** the current attempt output manifest no longer matches that round
- **THEN** build orchestration SHALL NOT mark the attempt as succeeded
- **AND** revalidation SHALL run again before promotion is allowed

#### Scenario: Current attempt workspace is the only repair source
- **WHEN** an operator repairs or revalidates a failed Web or Pwn attempt
- **THEN** orchestration SHALL select the current attempt workspace or canonical resulting challenge directory for that attempt
- **AND** it SHALL NOT search unrelated `work/executions/*` directories to find a passing solver acceptance record
