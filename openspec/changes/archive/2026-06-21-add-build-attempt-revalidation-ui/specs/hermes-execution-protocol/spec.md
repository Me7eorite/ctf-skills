## MODIFIED Requirements

### Requirement: Runner owns validate execution and validate events

Hermes SHALL generate `validate.sh` and `writenup/exp.py` but SHALL NOT execute
validation or write validate progress events. For non-dry-run execution,
`HermesRunner` SHALL verify design, implement, build, and document prerequisites
and validate files after Hermes returns. If prerequisites are incomplete, the
runner MUST write validate/failed without invoking `ChallengeValidator`. If
prerequisites are complete and validate is not skipped, the runner MUST write
validate/running, call `ChallengeValidator.validate_challenge(challenge_id)`,
and map only `status == "passed"` to validate/passed.

Carry-forward `validate/passed` events written by the runner under the resume
protocol MUST start their message with the literal token `carry-forward:` and
MUST include the source historical event id. Fresh `validate/passed` and
`validate/failed` events written by the runner after invoking
`ChallengeValidator` MUST start their message with the literal token
`validator:` and MUST include the validator's status and elapsed time. The two
prefixes MUST be machine-distinguishable so audit tooling can separate inherited
historical validations from freshly executed ones.

The same host-owned validation behavior MAY be invoked by a build-attempt
revalidation service for an existing failed attempt. That service path SHALL NOT
invoke Hermes and SHALL NOT recompute the original resume/carry-forward
decision; it SHALL use current disk lookup and evidence for the validation
decision.

#### Scenario: Carry-forward and validator messages are distinguishable

- **WHEN** the same shard window contains one carry-forward `validate/passed`
  inherited from a prior run and one fresh `validate/passed` written after
  `ChallengeValidator` returned passed
- **THEN** the carry-forward event message starts with `carry-forward:` and
  cites the historical source event id, while the fresh event message starts
  with `validator:` and cites the validator status and elapsed time

#### Scenario: Validate gate blocks missing prerequisites

- **WHEN** design, implement, build, document, `validate.sh`, or `writenup/exp.py`
  evidence is incomplete
- **THEN** the runner writes validate/failed, includes missing items in the
  message/report, and does not call `validate_challenge`

#### Scenario: Validate failure makes challenge fail

- **WHEN** Hermes wrote design, implement, build, and document passed but
  `validate_challenge` returns a non-passed status
- **THEN** the runner writes validate/failed and final challenge complete/failed
  while leaving prior document/passed events append-only

#### Scenario: Skipped validate is not re-executed

- **WHEN** validate belongs to a verified resume skip prefix
- **THEN** the runner does not call `ChallengeValidator.validate_challenge` for
  that challenge in the current run

#### Scenario: Build-attempt revalidation uses current disk evidence only

- **GIVEN** a failed build attempt references a shard whose prior
  `validate/failed` event came from stale host lookup state
- **AND** the challenge directory now exists on disk
- **WHEN** the build-attempt revalidation service invokes host validation
- **THEN** validation resolves the current challenge directory before the gate
- **AND** it does not invoke Hermes
- **AND** it does not create carry-forward events from a newly computed resume
  plan
