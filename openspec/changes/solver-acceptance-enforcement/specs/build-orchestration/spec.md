## ADDED Requirements

### Requirement: Build attempts require solver acceptance before success

Build orchestration SHALL consume solver acceptance evidence for Web and Pwn attempts before marking an attempt successful. A build attempt SHALL remain in the existing `failed` status, with blocked reason diagnostics when appropriate, unless the current execution workspace contains a final passed validation round with solver acceptance passed for every Web/Pwn challenge in the attempt. `blocked` SHALL NOT be introduced as a new `build_attempts.status` value by this change. Retry, repair, and revalidate flows SHALL use the same solver acceptance requirement before changing the parent design task to built.

#### Scenario: Successful shard without solver acceptance remains failed
- **WHEN** a Web or Pwn shard reaches a terminal runner state
- **AND** the artifact directory exists
- **BUT** the latest final validation evidence does not show solver acceptance passed
- **THEN** build orchestration SHALL NOT mark the attempt as succeeded
- **AND** the parent design task SHALL NOT become built

#### Scenario: Retry must pass solver acceptance
- **WHEN** an operator retries a failed Web or Pwn build attempt
- **AND** the retry produces artifacts but the reference solver still fails acceptance
- **THEN** the retry attempt SHALL remain `failed` with solver blocked diagnostics when appropriate
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

Build orchestration SHALL treat solver-only regeneration and solver blocked outcomes as attempt-scoped diagnostics. One attempt's solver repair exhaustion or regeneration route SHALL NOT consume the repair budget, retry budget, or status of sibling attempts in the same batch.

#### Scenario: Solver blocked attempt does not stop sibling
- **GIVEN** a batch contains attempts A and B
- **WHEN** attempt A remains `failed` with blocked reason `solver_unrepairable`
- **THEN** attempt B SHALL continue through its own validation and repair lifecycle
- **AND** B SHALL receive its own solver repair and regeneration budgets

#### Scenario: Challenge regeneration is recorded as future human action`r`n- **WHEN** solver-only routes fail and diagnostics prove an artifact contradiction`r`n- **THEN** orchestration SHALL keep the attempt `failed` and record `challenge_regeneration_required` as a blocked reason`r`n- **AND** this change SHALL NOT automatically create a regenerated challenge attempt

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


## MODIFIED Requirements

### Requirement: build_attempts five-state machine

Build attempts SHALL continue to use only the existing five persisted statuses: `queued`, `running`, `succeeded`, `failed`, and `lost`. For Web and Pwn attempts, the `running -> succeeded` and logical `queued -> running -> succeeded` reconciliation paths SHALL require both the existing passed artifact evidence and final solver acceptance passed for the current output manifest. Solver blocked outcomes SHALL be represented as failed-attempt diagnostics such as blocked reason, solver acceptance status, validation details, report fields, or progress evidence; they SHALL NOT add a sixth persisted status.

#### Scenario: Done Web/Pwn shard with solve_status but no acceptance remains failed
- **WHEN** the reconciler observes a Web or Pwn done shard whose metadata has `solve_status = 'passed'`
- **AND** the current attempt evidence does not contain final solver acceptance passed for the current output manifest
- **THEN** the row's `status` SHALL become or remain `failed`
- **AND** the row SHALL expose solver acceptance unavailable or failed diagnostics when available

#### Scenario: Done non-Web/Pwn shard keeps existing promotion rule
- **WHEN** the reconciler observes a non-Web/Pwn done shard whose existing success conditions pass
- **THEN** this change SHALL NOT add a Web/Pwn solver acceptance requirement to that shard

### Requirement: Existing per-attempt revalidation is race-safe and recoverable

The same-attempt revalidation endpoint SHALL preserve its existing race-safety and locking behavior, but Web and Pwn promotion on `POST /api/build-attempts/{id}/revalidate` SHALL require clean host validation plus solver acceptance passed for the current output manifest. A `validate.sh` exit code `0` and matching flag are not sufficient for Web/Pwn promotion if solver acceptance evidence is missing, unavailable, stale, or failed.

#### Scenario: Web/Pwn revalidate without acceptance remains failed
- **GIVEN** build_attempt B is `failed` with a present Web or Pwn `resulting_challenge_dir`
- **AND** `validate.sh` exits `0` and prints the expected flag
- **BUT** final solver acceptance for the current output manifest is missing, unavailable, stale, or failed
- **WHEN** `POST /api/build-attempts/{B}/revalidate` is invoked
- **THEN** B SHALL remain `failed`
- **AND** the response SHALL expose solver acceptance diagnostics or a blocked reason rather than setting `status="succeeded"`

#### Scenario: Web/Pwn revalidate promotes only accepted solver
- **GIVEN** build_attempt B is `failed` with a present Web or Pwn `resulting_challenge_dir`
- **AND** clean validation passes with solver acceptance passed for the current output manifest
- **WHEN** `POST /api/build-attempts/{B}/revalidate` is invoked
- **THEN** B MAY become `succeeded` according to the existing revalidation success path
- **AND** the parent design task MAY become `built`
