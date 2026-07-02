## 1. Taxonomy and policy plumbing

- [ ] 1.1 Extend the validation failure classifier with normalized batch-oriented failure classes and a stable failure signature.
- [ ] 1.2 Define a small policy object for class-specific retry ceilings and stop conditions.
- [ ] 1.3 Thread the normalized failure class through runner progress messages and attempt summaries.

## 2. Attempt-scoped repair behavior

- [ ] 2.1 Make deterministic auto-repair select its path from the normalized failure class.
- [ ] 2.2 Stop automatic repair when the same failure class and signature repeats without progress.
- [ ] 2.3 Ensure timeout and readiness failures do not consume a shared batch-wide retry budget.

## 3. Batch isolation and orchestration

- [ ] 3.1 Update build orchestration so one attempt's failure cannot block sibling attempts in the same batch.
- [ ] 3.2 Preserve per-attempt failure summaries and expose the normalized class in build-attempt API responses.
- [ ] 3.3 Keep retry and revalidate flows bounded by the latest attempt and its own diagnostic history.

## 4. Verification

- [ ] 4.1 Add tests for timeout, prompt, readiness, and contract-failure classification.
- [ ] 4.2 Add tests proving repeated identical failures stop auto-repair for one attempt only.
- [ ] 4.3 Add tests proving sibling attempts continue independently inside the same batch.
