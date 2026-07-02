## Context

Batch build attempts currently inherit a mostly single-attempt repair model: failures are classified, but the retry loop does not strongly distinguish timeout from service-readiness problems, and repeated failures can keep consuming repair budget without improving the outcome. The batch operator needs throughput, not endless optimism.

## Goals / Non-Goals

**Goals:**
- Make failure handling class-aware and attempt-local.
- Stop repeated no-progress repair loops from consuming batch capacity.
- Preserve independent progress for sibling attempts in the same batch.
- Reuse existing persistence and progress-event infrastructure.

**Non-Goals:**
- No new database tables.
- No wholesale redesign of the build queue model.
- No attempt to make every broken challenge auto-fixable.

## Decisions

1. Use a normalized failure classification layer instead of ad hoc log parsing in each service.
   The closed API/repair-level class set is `timeout`, `service-readiness`, `prompt`, `contract`, and `solver`. These slugs are the canonical wire values; lower-level validation statuses and diagnostic codes remain input evidence, not separate members of this set.

2. Keep repair policy attempt-scoped, not batch-scoped.
   A batch may contain many failures, but each attempt gets its own repair budget and exhaustion state. This prevents one pathological challenge from draining unrelated attempts.

3. Persist only stable outcomes, not a new retry-state table.
   The normalized class is derived from the latest validation result and existing diagnostics. It may be copied into existing progress-event or attempt-summary payloads for operator visibility, but those copies are not the durable source of truth and no new table or required schema field is introduced.

4. Treat repeated identical failure signatures as a stop signal.
   When an attempt fails with the same class and effectively the same signature across repair rounds, the system should stop auto-repairing and hand control back to the operator instead of looping.

5. Keep sibling attempts independent during validation and repair.
   The orchestration path should continue processing other attempts in the batch even if one attempt times out or exhausts repair budget.

## Risks / Trade-offs

- [Risk] Early stop rules may leave some solvable attempts unrepaired. -> Mitigation: keep the policy class-specific and conservative for first rollout.
- [Risk] Failure signatures may be noisy. -> Mitigation: compare both normalized class and a trimmed signature derived from the latest diagnostic text.
- [Risk] Operators may want more visibility into why a retry stopped. -> Mitigation: keep the class and summary in the existing failure summary fields and progress events.
- [Risk] Changing repair flow could regress valid retries. -> Mitigation: add coverage for all five classes and repeated-same-signature cases before rollout.

## Migration Plan

1. Add the normalized failure classification and policy routing.
2. Thread the class through validation, repair, and API summaries.
3. Add bounded stop conditions for repeated identical failures.
4. Verify batch isolation so sibling attempts keep progressing.
5. Roll out behind existing batch submission paths; no schema migration is required.

## Open Questions

- Should the system eventually persist failure signatures for historical reporting, or is progress-event reconstruction sufficient?
- Should the UI expose a batch-level failure histogram, or is per-attempt detail enough for the first release?
