## MODIFIED Requirements

### Requirement: Validation is mandatory and host-owned

After Hermes returns successfully (or timeout recovery determines that all
required design/implement/build/document evidence exists), the host runner SHALL
run validation for every non-skipped challenge before moving a shard to `done`.
Hermes SHALL NOT mark a challenge validated on its own, and prompt text SHALL
continue to instruct Hermes not to execute `validate.sh`.

Host validation consists of:

1. resolving the current challenge directory,
2. running the quality gate over design, implementation, build, document, and
   validation-entrypoint evidence,
3. writing `validate/running` only after the quality gate passes, and
4. invoking `ChallengeValidator.validate_challenge(challenge_id)`.

The same host-owned validation behavior MAY be invoked by a build-attempt
revalidation service for an existing failed attempt. That service path SHALL NOT
invoke Hermes and SHALL NOT recompute the original resume/carry-forward
decision; it SHALL use current disk lookup and evidence for the validation
decision.

#### Scenario: Build-attempt revalidation uses current disk evidence only

- **GIVEN** a failed build attempt references a shard whose prior
  `validate/failed` event came from stale host lookup state
- **AND** the challenge directory now exists on disk
- **WHEN** the build-attempt revalidation service invokes host validation
- **THEN** validation resolves the current challenge directory before the gate
- **AND** it does not invoke Hermes
- **AND** it does not create carry-forward events from a newly computed resume
  plan
