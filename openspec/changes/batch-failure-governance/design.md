## Context

Batch build attempts currently inherit a mostly single-attempt validation repair model: validation failures are classified, but the retry loop does not strongly distinguish timeout from service-readiness problems, and repeated validation failures can keep consuming repair budget without improving the outcome. The batch operator needs throughput, not endless optimism.

## Goals / Non-Goals

**Goals:**
- Make validation failure handling class-aware and attempt-local.
- Stop repeated no-progress validation repair loops from consuming batch capacity.
- Preserve independent validation/repair progress for sibling attempts in the same batch.
- Reuse existing persistence and progress-event infrastructure.

**Non-Goals:**
- No new database tables.
- No wholesale redesign of the build queue model.
- No attempt to make every broken challenge auto-fixable.
- No replacement of the existing runner-phase taxonomy (`hermes_auth`, `hermes_rate_limit`, `hermes_timeout`, `terminal_workspace`, `materialize`, `contract_prepare`, `validation`, etc.).
- No change to the sequential driver's existing consecutive infrastructure fail-fast behavior.

## Decisions

1. Use a normalized validation failure classification layer instead of ad hoc log parsing in each service.
   The first rollout classifies only attempts whose terminal runner phase is `validation`. The closed API/repair-level class set for validation failures is `timeout`, `service-readiness`, `contract`, and `solver`. These slugs are the canonical wire values; lower-level validation statuses and diagnostic codes remain input evidence, not separate members of this set. Prompt-input failures are reserved until prompt rendering has stable capture points and diagnostic fields.

2. Keep runner-phase failures outside this normalized validation class unless an explicit mapping exists.
   Existing runner-phase categories remain authoritative for non-validation failures. API responses should not invent a validation failure class for phases such as `hermes_auth`, `hermes_rate_limit`, `hermes_timeout`, `terminal_workspace`, `materialize`, or `contract_prepare`.

   Classification should use this precedence: timeout status or timeout diagnostics first; readiness-specific `validation_failure_details[].code` next; contract/gate diagnostics next; solver/runtime statuses last. This matters because readiness problems may currently surface as `contract_failed` with readiness-specific detail codes.

   | Evidence source | Example status or diagnostic | Normalized validation class |
   | --- | --- | --- |
   | validation status or detail code | `timeout` | `timeout` |
   | validation detail code | `pwn_service_readiness_failed`, `pwn_prompt_eof`, `pwn_port_only_readiness`, `pwn_bad_readiness_probe` | `service-readiness` |
   | validation status, phase, or detail code | `contract_failed`, `missing_validation`, `invalid_metadata`, `phase=contract`, `phase=gate`, missing required file/field/metadata/attachment/validation script/evidence contract | `contract` |
   | validation status or detail code | `nonzero_exit`, `flag_mismatch`, exploit/runtime failure after required contracts and readiness are established | `solver` |
   | runner phase | `hermes_auth`, `hermes_rate_limit`, `hermes_timeout`, `terminal_workspace`, `materialize`, `contract_prepare` | no normalized validation class |
   | prompt rendering | missing `shard.json`, manifest, resume context, or other render input before validation starts | no class in first rollout; future prompt diagnostic work |

3. Keep repair policy attempt-scoped, not batch-scoped.
   A batch may contain many failures, but each attempt gets its own repair budget and exhaustion state. This prevents one pathological challenge from draining unrelated attempts.

4. Persist only stable outcomes, not a new retry-state table.
   The normalized class is derived from the latest validation result and existing diagnostics for the current attempt. Derivation should prefer the latest failed result in `work/executions/<attempt_id>/current/state/validation-history.json`, because artifact `metadata.json` and the current report merge path do not necessarily preserve `validation_failure_details`. If history is unavailable, derivation may fall back to report challenge entries after `merge_validation_into_report()` is extended to preserve `validation_failure_details`, then to `validation_status`, `validation_contract_errors`, progress-event terminal messages, and artifact metadata. It may be copied into existing progress-event or attempt-summary payloads as `validation_failure_class` for operator visibility, but those copies are not the durable source of truth and no new table or required schema field is introduced.

5. Treat repeated identical failure signatures as an invocation-local stop signal.
   When an attempt fails with the same class and effectively the same signature across repair rounds inside the same active runner validation/repair invocation, the system should stop auto-repairing and hand control back to the operator instead of looping. Cross-request comparison across separate revalidate/retry calls is out of scope until a durable signature source is introduced.

6. Add a policy router in front of deterministic repair.
   The existing deterministic repair service applies a bundle of mechanical repairs to a challenge directory. This change should introduce a small class-aware repair policy/router before or inside that service so `timeout`, `service-readiness`, `contract`, and `solver` can select a bounded route without hard-coding class checks at unrelated call sites. A route may run deterministic mechanical repair, invoke Hermes repair with structured diagnostics, or stop/escalate when deterministic repair would be unsafe. Contract and service-readiness classes may use deterministic repairs when the detail code maps to an existing safe mechanic; solver failures should normally go to Hermes repair with file context and diagnostics rather than pretending deterministic auto-repair can tune exploit logic.

7. Keep sibling attempts independent during validation and repair.
   The orchestration path should continue processing other attempts in the batch when one attempt has a validation/repair failure or exhausts validation repair budget. This does not override the sequential driver's existing consecutive infrastructure fail-fast behavior, which may still abort tail attempts for repeated infrastructure failures.

8. Populate retry and repair context with structured validation evidence.
   `BuildAttemptRepairService` already renders a `Structured failure details` prompt section, but the current helper can be empty. This change should thread latest `validation_failure_details`, stdout/stderr tails, and the concise `failure_summary` into both retry context and direct/manual repair context while preserving `validation_contract_errors` / `contract_errors` compatibility for existing callers and prompts. Retry diagnostics and manual repair should use the same latest-failed-result source so `/retry`, `/repair`, and attempt-detail API summaries classify the same failure consistently.

## Risks / Trade-offs

- [Risk] Early stop rules may leave some solvable attempts unrepaired. -> Mitigation: keep the policy class-specific and conservative for first rollout.
- [Risk] Failure signatures may be noisy. -> Mitigation: compare both normalized class and a trimmed invocation-local signature derived from the latest diagnostic text.
- [Risk] Operators may want more visibility into why a retry stopped. -> Mitigation: keep the class and summary in the existing failure summary fields and progress events.
- [Risk] Changing repair flow could regress valid retries. -> Mitigation: add coverage for the first-rollout validation classes, repair routing, structured repair context, API summaries, repeated-same-signature cases, and existing infra fail-fast behavior before rollout.

## Migration Plan

1. Add the normalized validation failure classification and policy routing.
2. Thread the class through validation, repair, and API summaries.
3. Add bounded invocation-local stop conditions for repeated identical validation failures.
4. Verify validation/repair batch isolation so sibling attempts keep progressing.
5. Roll out behind existing batch submission paths; no schema migration is required.
6. Verify that one build attempt still maps to one challenge for this flow; if multi-challenge shards return, add an explicit aggregation rule before exposing a single `validation_failure_class`.

## Open Questions

- Should the system eventually persist failure signatures for historical reporting and cross-request repair suppression?
- Should the UI expose a batch-level failure histogram, or is per-attempt detail enough for the first release?
- Should prompt rendering errors become a fifth normalized class after prompt capture points and diagnostics are defined?
