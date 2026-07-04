## ADDED Requirements

### Requirement: Batch failure taxonomy is normalized and stable

The system SHALL map build-attempt validation-phase failures into a normalized, closed set of failure classes whose allowed slugs are stable across runner invocations. The first-rollout closed set SHALL be exactly `timeout`, `service-readiness`, `contract`, and `solver`. These exact slugs are the canonical API and repair-policy values for attempts whose terminal runner phase is `validation`. The `timeout` class SHALL mean a validator or `validate.sh` wrapper timeout during validation; Hermes subprocess timeouts, including `hermes_timeout` runner-phase failures and `HERMES_TIMEOUT` selection, SHALL remain outside this validation taxonomy. Phase 1 assumes the current one-build-attempt-to-one-challenge flow: under that precondition, every failed validation-phase attempt SHALL have exactly one attempt-level class assigned for the latest validation round. If a future flow reintroduces multi-challenge build-attempt shards, the system SHALL expose per-challenge classes or define an explicit aggregation rule before emitting one attempt-level `validation_failure_class`. Attempts that fail before or outside validation, including runner phases such as `hermes_auth`, `hermes_rate_limit`, `hermes_timeout`, `terminal_workspace`, `materialize`, and `contract_prepare`, SHALL NOT be assigned a normalized validation failure class by this capability. The class SHALL be derivable from the final validation result and existing diagnostic evidence without requiring new storage tables or a new durable source-of-truth field. Phase 1 derivation SHALL use structured `validation_failure_details` from the latest failed validation result in `work/executions/<attempt_id>/current/state/validation-history.json` as the primary source when present, then fall back to report entries that preserve `validation_failure_details`, `validation_status`, `validation_contract_errors`, latest terminal validation progress-event messages, and artifact metadata. The system MAY copy the derived class into existing progress-event or attempt-summary payloads for visibility, but such copies SHALL NOT replace derivation as the source of truth. Pwn readiness evidence SHALL be interpreted as one of `established`, `failed-fresh-connection`, or `unavailable`; classifiers SHALL NOT collapse missing readiness evidence into a failed readiness observation. A missing field, absent probe, or bare `readiness_established=false` value SHALL be treated as not-established evidence, not as explicit failed-fresh-connection evidence, unless accompanied by a readiness-failure diagnostic code or an explicit `failed-fresh-connection` observation.

#### Scenario: Validation timeout is classified deterministically
- **WHEN** a build attempt fails because the validator or `validate.sh` wrapper exceeds its allotted validation time
- **THEN** the attempt SHALL be classified as `timeout`
- **AND** the failure summary SHALL preserve the timeout cause
- **AND** a Hermes subprocess timeout SHALL remain a non-validation runner-phase failure instead of receiving this class

#### Scenario: Service readiness is distinguished from exploit logic
- **WHEN** a pwn attempt fails during validation because a fresh readiness probe cannot observe a real banner or menu before solver payloads are sent
- **THEN** the attempt SHALL be classified as `service-readiness`
- **AND** the summary SHALL point the operator toward probe or startup issues rather than exploit payload tuning

#### Scenario: Missing readiness evidence is not treated as failed readiness
- **WHEN** a pwn attempt fails with generic prompt/menu EOF evidence
- **AND** the latest failed validation result lacks a fresh readiness observation
- **THEN** the attempt SHALL preserve a missing-readiness-evidence diagnostic
- **AND** after required contracts have passed, the attempt SHALL prefer the `solver` route rather than `service-readiness`

#### Scenario: Latest validation history supplies structured details
- **WHEN** a failed validation attempt has `validation_failure_details` recorded in `current/state/validation-history.json`
- **THEN** the classifier SHALL use the latest failed validation result from that history as the primary structured source
- **AND** it SHALL NOT rely only on artifact `metadata.json` or progress messages when structured history is available

#### Scenario: Validation history has an interoperable shape
- **WHEN** a fresh failed validation round is written for a build-attempt-attributed challenge
- **THEN** `current/state/validation-history.json` SHALL contain a JSON array of round objects
- **AND** each round object SHALL contain `runner_phase="validation"` and a `results` array
- **AND** each failed result SHALL preserve `challenge_id`, `solve_status`, `validation_status`, concise validation error or summary, `validation_failure_details` when available, bounded stdout/stderr tails when available, and explicit unavailable markers for repair-critical fields that were not captured
- **AND** readers SHALL derive an attempt-level class only when the latest usable failed round contains exactly one failed challenge result

#### Scenario: Readiness detail codes outrank contract status
- **WHEN** a validation result has `validation_status` `contract_failed` but `validation_failure_details` includes readiness-specific codes such as `pwn_port_only_readiness` or `pwn_bad_readiness_probe`
- **THEN** the attempt SHALL be classified as `service-readiness`
- **AND** the classifier SHALL NOT route it as `contract` solely because the coarse validation status is `contract_failed`

#### Scenario: Non-validation runner phases remain outside the validation taxonomy
- **WHEN** a build attempt fails before validation with runner phase `hermes_auth`, `hermes_rate_limit`, `hermes_timeout`, `terminal_workspace`, `materialize`, or `contract_prepare`
- **THEN** the attempt SHALL preserve that runner phase as the failure category
- **AND** the attempt SHALL NOT expose `timeout`, `service-readiness`, `contract`, or `solver` as a normalized validation failure class

#### Scenario: Prompt failures are deferred until prompt diagnostics exist
- **WHEN** a build, validation, or repair prompt cannot be rendered or supplied because required prompt inputs are missing or invalid
- **THEN** the attempt SHALL preserve the existing runner failure category and diagnostic summary
- **AND** the attempt SHALL NOT be assigned a normalized validation failure class unless a future change adds stable prompt capture points and diagnostic fields

#### Scenario: Contract failures remain separate from runtime failures
- **WHEN** validation fails because a required file, field, or evidence contract is missing
- **THEN** the attempt SHALL be classified as `contract`
- **AND** the result SHALL not be classified as a timeout, service-readiness, or solver failure

#### Scenario: Solver runtime failures remain separate from contracts
- **WHEN** `validate.sh` runs and the reference solver exits non-zero, emits a wrong flag, or otherwise fails after required files and service readiness have been established
- **THEN** the attempt SHALL be classified as `solver`
- **AND** the summary SHALL preserve the solver-runtime evidence instead of routing the failure as a contract or service-readiness problem

#### Scenario: Solver dependency failures stay repairable as solver failures
- **WHEN** validation fails because `writenup/exp.py` raises `ModuleNotFoundError`, imports an undeclared local helper, or otherwise cannot load a solver dependency after validation has started
- **THEN** the attempt SHALL be classified as `solver`
- **AND** the failure signature SHALL include the missing module or dependency name when available
- **AND** the repair summary SHALL point toward standard-library, vendored-helper, or declared-runtime fixes rather than service-readiness tuning

#### Scenario: Timeout signatures preserve the actionable subreason
- **WHEN** validation times out and available diagnostics identify a stable cause such as solver I/O, service readiness, wrapper bounds, or missing diagnostic capture
- **THEN** the attempt SHALL still expose normalized class `timeout`
- **AND** the failure signature SHALL preserve the stable timeout subreason when available
- **AND** the repair route MAY use that subreason to choose bounded diagnostic, solver-context, or escalation behavior without adding a fifth normalized class

### Requirement: Observed reference solver stability gaps remain diagnostic-first

The system SHALL preserve Web/Pwn reference solver stability diagnostics that are already visible in validation results, structured diagnostics, stdout/stderr tails, or repair context. Phase 1 SHALL NOT add broad static solver-quality scanners, new document-completion blockers, generation-quality gates, or hard rejection for default-target, dependency, bounded-I/O, or evidence gaps that are not already surfaced by validation evidence. For Web/Pwn challenges, diagnostics SHOULD make it clear when the default solver path does not connect to the live validation target through `CHAL_HOST` and `CHAL_PORT`, and when Pwn reads or local process interactions appear unbounded. Explicit local debug paths such as `LOCAL=1` MAY use local binaries, loopback hosts, or `process()` for bounded smoke tests, but Phase 1 SHALL treat local-only behavior as diagnostic evidence rather than a hard blocker. Later enforcement phases MAY turn deterministic stability gaps into hard blockers only after a follow-up change defines enforcement tests and acceptance criteria.

#### Scenario: Local debug may adapt the loader with the shipped runtime pieces
- **WHEN** a Pwn challenge provides a matching loader/`ld` alongside the binary
- **THEN** the local debug path MAY use `patchelf` to point the local binary at that loader so the binary runs against the shipped runtime more closely
- **AND** when only `libc` is provided without a matching loader, the local debug path MAY use `xclibc` or an equivalent loader shim to bind the delivered libc to the local binary
- **AND** these local-only aids SHALL remain outside the default validation path and SHALL NOT replace `remote(CHAL_HOST, CHAL_PORT)` as the authoritative solve path

#### Scenario: Web/Pwn solver default path uses validation target environment
- **WHEN** a Web or Pwn challenge provides `writenup/exp.py`
- **THEN** Phase 1 SHALL preserve contract or solver diagnostics when current validation evidence shows the default validation path does not use `CHAL_HOST` and `CHAL_PORT` to reach the running service
- **AND** later hard enforcement MAY require hardcoded `127.0.0.1`, `localhost`, container names, or fixed challenge ports to appear only inside explicit local debug branches

#### Scenario: Pwn solver interactions are bounded
- **WHEN** a Pwn solver uses pwntools, sockets, subprocesses, or local process execution
- **THEN** Phase 1 SHALL preserve contract, solver, or timeout diagnostics when current validation evidence shows prompt reads, leak reads, shell reads, or local process runs are not bounded by short timeouts or equivalent deterministic limits
- **AND** timeout or missing-diagnostic evidence SHALL route through bounded repair or escalation rather than unbounded repeated validation

#### Scenario: Solver repair receives complete exp evidence
- **WHEN** a validation failure is classified as `solver`
- **THEN** the next Hermes repair prompt SHALL include the latest `writenup/exp.py`, `validate.sh`, structured `validation_failure_details`, stdout/stderr tails, concise failure summary, and `writenup/pwn_debug_report.json` when present
- **AND** the repair route SHALL preserve whether the failure appears to be dependency, synchronization, flag mismatch, offset/payload, leak parsing, or remote/local mismatch evidence

### Requirement: Initial reference solver quality gaps are diagnostic-only in Phase 1

The system SHALL treat initial `writenup/exp.py` quality as a validation-governed diagnostic surface when validation evidence can observe the defect. In Phase 1, Web/Pwn solver stability and evidence gaps SHALL be preserved when they appear in structured validation diagnostics, stdout/stderr tails, failure summaries, or repair context, without adding new document-completion blockers, solver-quality gates, mandatory Pwn evidence profiles, or broad static inspection. Later enforcement phases MAY require the reference solver to satisfy static stability contracts and bounded solve evidence before documentation completion only after a follow-up change promotes those diagnostics into hard gates. Pwn challenges with non-trivial payload logic SHOULD preserve structured debug evidence for offsets, mitigations, libc/PIE assumptions, gadgets, menu synchronization, leak parsing, local smoke results, and remote/container solve results when available. Simple Pwn challenges MAY provide concise evidence, and missing evidence or an explicit inability to run a bounded smoke test SHALL be recorded rather than hidden when current validation evidence can observe it.

#### Scenario: Poor initial exp produces diagnostics before hard enforcement
- **WHEN** a generated Web/Pwn challenge contains `writenup/exp.py` but the solver violates default-target, dependency, bounded-I/O, or basic evidence expectations
- **THEN** Phase 1 SHALL preserve available contract or solver diagnostics that identify the missing solver-quality evidence
- **AND** later enforcement phases MAY block document completion after those diagnostics are visible and covered by tests

#### Scenario: Pwn payload assumptions are evidence-backed
- **WHEN** a Pwn solver uses overflow offsets, libc symbols, PIE bases, ROP gadgets, leak parsing, or menu synchronization assumptions
- **THEN** Phase 1 SHALL preserve any available validation or debug evidence that identifies the source of those assumptions
- **AND** guessed or stale constants SHALL be surfaced as solver-quality diagnostics when current validation or repair evidence can identify them

#### Scenario: Menu synchronization evidence separates solver bugs from readiness bugs
- **WHEN** a Pwn solver fails while waiting for a banner, prompt, or menu token
- **THEN** the diagnostics SHALL preserve whether the service readiness probe saw the prompt on a fresh connection
- **AND** prompt/menu EOF SHALL classify as `service-readiness` only when explicit readiness evidence shows no real application prompt on a fresh connection
- **AND** prompt/menu EOF SHALL classify as `solver` when readiness is established and the reference solver later loses synchronization
- **AND** generic EOF evidence without a fresh-connection readiness observation SHALL preserve a missing-readiness-evidence diagnostic and SHALL NOT be treated as `service-readiness` solely because readiness is unknown
- **AND** after required contracts have passed, generic EOF without freshness/readiness evidence SHALL prefer the `solver` route until explicit failed readiness evidence appears

#### Scenario: Solver dependency gaps are diagnostic in Phase 1
- **WHEN** `writenup/exp.py` imports a non-standard helper module
- **THEN** Phase 1 SHALL preserve the missing dependency diagnostic when validation or repair evidence identifies the missing helper
- **AND** a missing helper SHALL produce a solver dependency signature containing the missing module name
- **AND** later enforcement phases MAY require the helper to be generated or declared before document completion

### Requirement: Validation diagnostics are sufficient for repair

The validation path SHALL preserve a bounded diagnostic envelope whenever validation or solver execution fails and those fields are available from the current validator, wrapper, or report merge path. The envelope SHOULD include, when applicable, compose or container service state, recent service logs, readiness probe result, exact validation command, `validate.sh` stdout tail, `validate.sh` stderr tail, solver stdout/stderr tails when the wrapper exposes them separately, solver exit code when available, validation status, structured `validation_failure_details`, and any final stdout flag candidate. Phase 1 SHALL NOT require every existing `validate.sh` wrapper to be rewritten to capture every envelope field; instead, repair contexts and API summaries SHALL preserve available fields, synthesize explicit unavailable markers for missing fields needed by repair, and cap diagnostic text by line and byte budgets with truncation markers.

#### Scenario: Solver failure captures stdout and stderr evidence
- **WHEN** `writenup/exp.py` exits non-zero or prints the wrong flag during validation
- **THEN** the latest validation result SHALL preserve bounded `validate.sh` stdout and stderr tails
- **AND** it SHALL preserve bounded solver stdout and stderr tails plus the solver exit code when the wrapper exposes solver-specific streams or status separately
- **AND** otherwise it SHALL mark solver-specific streams or exit status as unavailable when those fields are needed for repair
- **AND** the next repair prompt SHALL include the captured tails and unavailable markers

#### Scenario: Insufficient diagnostics becomes actionable
- **WHEN** validation fails but the latest result lacks solver stdout/stderr tails, readiness evidence, service logs, or structured failure details needed for repair
- **THEN** the attempt SHALL expose a diagnostic-quality failure summary or detail
- **AND** the next repair route SHALL improve validation diagnostics before attempting speculative exploit payload changes when a safe wrapper or context-only diagnostic repair is available

#### Scenario: Repair context marks truncated diagnostics
- **WHEN** solver stdout, solver stderr, service logs, or debug reports exceed the repair-context budget
- **THEN** the repair prompt SHALL include the most relevant bounded tail or summary
- **AND** it SHALL explicitly mark that content was truncated so the repair agent does not treat the evidence as complete

### Requirement: Automatic repair stops after repeated identical failures

The system SHALL stop runner automatic validation repair for a build attempt when the same normalized validation failure class and essentially the same failure signature repeat across repair rounds inside the same active runner validation/repair invocation without observable progress. The signature SHOULD be derived from structured `validation_failure_details` code/message/path data when available, then fall back to validation status, concise error text, and stdout/stderr tail evidence. The stop condition SHALL be attempt-local and invocation-local, and SHALL be evaluated after validation reruns caused by deterministic repair as well as after Hermes repair rounds. Reaching that stop condition SHALL leave the attempt failed and SHALL not affect the repair budget or progress of sibling attempts in the same batch. Cross-request suppression across separate dashboard manual repair, retry, or revalidate requests is out of scope unless a future change adds durable failure-signature storage. Those operator-triggered paths SHALL receive the latest class and signature as context but SHALL NOT be suppressed by Phase 1 invocation-local state.

#### Scenario: Repeated timeout stops repair for one attempt
- **WHEN** the same build attempt times out repeatedly with the same structured-or-derived signature and no progress change inside one validation/repair invocation
- **THEN** the system SHALL stop further automatic repair for that attempt
- **AND** the attempt SHALL remain failed with the latest timeout diagnostic

#### Scenario: Different solver signatures can continue within budget
- **WHEN** a build attempt first fails with a solver dependency signature and then fails with a materially different solver signature such as flag mismatch or prompt EOF after a repair changed the output
- **THEN** the system SHALL NOT treat the second failure as the same repeated failure solely because both are classified as `solver`
- **AND** the attempt MAY continue through its bounded repair policy if budget remains

#### Scenario: Volatile values do not create fake new signatures
- **WHEN** repeated validation failures differ only by elapsed time, container id, random port, absolute execution workspace prefix, or non-address-specific memory address noise
- **THEN** the signature comparison SHALL normalize those volatile values before deciding whether the failure is repeated
- **AND** stable values such as detail code, missing module, path, traceback frame, prompt marker, and validation status SHALL remain part of the signature

#### Scenario: A different attempt still gets its own budget
- **GIVEN** attempt A has already exhausted its automatic repair budget
- **WHEN** attempt B in the same batch fails later
- **THEN** attempt B SHALL receive its own fresh repair budget
- **AND** attempt A's exhaustion SHALL not reduce attempt B's retry opportunities

### Requirement: Batch processing isolates attempts

The system SHALL treat each build attempt in a batch as an independent failure domain for validation and repair. One attempt's timeout, service-readiness failure, solver failure, contract failure, or validation repair exhaustion SHALL NOT block other attempts in the same batch from being validated, repaired, or reported. Validation-phase failures SHALL remain validation failures and SHALL NOT increment the sequential driver's consecutive infrastructure streak. This capability SHALL NOT disable the existing sequential consecutive-infrastructure fail-fast behavior for non-validation infrastructure failures.

#### Scenario: One failed attempt does not stall its siblings
- **GIVEN** a batch contains attempts A, B, and C
- **AND** A fails with a timeout during validation
- **WHEN** the batch continues processing
- **THEN** B and C SHALL continue through their own validation paths
- **AND** A's validation failure SHALL not abort the batch

#### Scenario: Consecutive infrastructure fail-fast is preserved
- **GIVEN** the sequential driver observes enough consecutive infrastructure failures to trigger its configured fail-fast threshold
- **WHEN** the threshold is reached
- **THEN** the sequential driver MAY still abort tail attempts with the existing `consecutive_infra` reason
- **AND** this behavior SHALL NOT be treated as a violation of validation/repair batch isolation

#### Scenario: Attempt-local failure history remains separate
- **WHEN** two attempts in the same batch fail for different reasons
- **THEN** each attempt SHALL retain its own derived failure class, invocation-local signature state, and summary
- **AND** neither attempt SHALL overwrite the other's diagnostic state

### Requirement: Pwn validation preserves readiness and solver evidence before repair

The system SHALL preserve available Pwn application-level readiness and solver evidence before routing repair, and SHALL explicitly mark missing evidence as unavailable when that absence affects repair choice. A port-open check, container `Up` state, or xinetd `...done` log SHALL NOT by itself prove service readiness. The readiness evidence SHOULD prefer a fresh connection that reads an application banner, menu, prompt, or other protocol-specific token before the reference solver sends exploit payloads. Solver repair SHALL receive bounded available evidence from `validate.sh`, `writenup/exp.py`, solver stdout/stderr, service logs, readiness probes, and `writenup/pwn_debug_report.json` when present; Phase 1 SHALL NOT reject otherwise inspectable artifacts solely because every possible evidence field is not present.

#### Scenario: Port-open readiness is insufficient
- **WHEN** a Pwn validation script only proves the TCP port is open or xinetd has started
- **THEN** the attempt SHALL record a readiness diagnostic such as `pwn_port_only_readiness` or `pwn_service_readiness_failed`
- **AND** repair SHALL prioritize readiness evidence, startup diagnostics, or probe normalization before exploit payload changes

#### Scenario: Established readiness routes later failure to solver repair
- **WHEN** a fresh readiness probe observes the application prompt or menu
- **AND** the reference solver later exits non-zero, loses synchronization, or fails to print the expected flag
- **THEN** the attempt SHALL be classified as `solver`
- **AND** the Hermes repair context SHALL include the prompt/readiness evidence so repair does not incorrectly rewrite service startup

#### Scenario: Missing diagnostic envelope is repaired before payload guesses
- **WHEN** a Pwn validation failure lacks solver stdout/stderr tails, service logs, readiness probe output, exact solver command, exit code, or structured failure details
- **THEN** the next automatic route SHALL first normalize validation diagnostics when a safe wrapper or context-only diagnostic repair is available
- **AND** Hermes SHALL NOT be asked to tune arbitrary offsets, gadgets, or leak parsing from an empty generic `nonzero_exit` summary
- **AND** this diagnostic-normalization step SHALL NOT change the normalized class unless a new validation result supplies explicit evidence for a different class

#### Scenario: Diagnostic-envelope completeness is evaluated after failure
- **WHEN** pre-validation inspection can see `validate.sh` but no validation failure has occurred yet
- **THEN** the system MAY normalize only context-only diagnostic hooks in the wrapper, such as bounded output capture, truncation markers, unavailable markers, or readiness-observation capture
- **AND** it SHALL NOT rewrite challenge-specific solver payload logic, scaffold layout, Dockerfile behavior, xinetd startup, flag placement, service ports, or generated source in Phase 1 diagnostic normalization
- **AND** it SHALL defer the completeness judgement for captured stdout/stderr tails, service logs, readiness output, solver command, exit code, and structured details until a failed validation result exists

#### Scenario: Deep exploit evidence is diagnostic-first in the first rollout
- **WHEN** a non-trivial Pwn exploit lacks rich `pwn_debug_report.json` evidence for offsets, mitigations, gadgets, leak parsing, or local/container observations
- **THEN** Phase 1 SHALL preserve that gap as repair context and a solver-quality diagnostic
- **AND** Phase 1 SHALL NOT reject every simple passing ret2win or ret2text challenge solely because it lacks a full advanced evidence profile
