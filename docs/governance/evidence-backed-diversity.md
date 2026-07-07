# Evidence-Backed Diversity Operations

This governance layer separates three facts that older flows sometimes mixed:

- Build metadata is a compatibility hint.
- ArtifactObservation is validation-layer truth.
- Corpus admission is release-layer truth.

## Profile Policy

`generation-profiles.json` selects allowed combinations from the closed
taxonomy in `src/domain/design/profile_taxonomy.py`. Policy may choose quotas,
cooldowns, and compatible value sets, but it must not invent vocabulary values.
Reservations store both taxonomy and policy versions, and canonical profile
signatures include the policy version so a policy edit cannot reinterpret an old
reservation.

Default hard constraints reject identical semantic + solve + implementation
signatures against active reservations, live governed evidence, and published
history. Same sub-technique may still pass when solve and implementation axes
are distinct and quota policy allows it.

## Thresholds

Corpus decisions use raw member states plus an aggregate batch decision.

- `passed`: no blocking or review condition remains.
- `review_required`: an overrideable similarity or inconclusive-observation
  condition needs operator approval.
- `blocked`: a non-overrideable rule failed.

Default source-token thresholds are `0.45` for review and `0.65` for block.
Default solver-token thresholds are `0.55` for review and `0.75` for block.
Exact combined governed-profile duplicates outside the same-task revision
lineage, failed observations, hard profile mismatches, and trial-only research
in production are not overrideable.

## Modes

`shadow` records governance data and non-production outputs only. It cannot make
legacy builds production eligible.

`trial` requires governed Design and Build admission for trial batches, but
release remains explicit and non-production. `legacy_trial` is separate: it is
operator-only, non-production, and reserved for grandfathered artifacts without
committed governance.

`production` requires a current accepted ArtifactObservation, corpus-accepted
member decisions, an aggregate `passed` batch decision, and an explicit
`corpus_batch_id` at packing time.

`research_runs.trial_only = true` is only a source marker. It can feed governed
trial work, but production corpus admission blocks it.

## Recovery

When Build cannot implement a committed contract, it reports
`design_unbuildable`. Operators should request a Design revision rather than
editing the committed contract or letting Build substitute a generic shape.
Failed observations and hard mismatches also require Design or implementation
repair; review approval is allowed only for policy-permitted inconclusive
observations or corpus similarity reviews.

For reviewed historical artifacts, use:

```bash
challenge-factory corpus history-import /path/to/challenge --dry-run
challenge-factory corpus history-import /path/to/challenge --apply --audit-reason "manual review"
```

The import stores only the minimal corpus-history projection needed for future
duplicate comparison. Normal resource deletion detaches and retains that
projection; a separate governance-history purge is required to remove it.

## Rollout Evidence

Production mode remains disabled until rollout evidence is recorded. Generate
one shadow report for the current corpus and at least two governed trial batch
reports, then run:

```bash
challenge-factory corpus rollout-report \
  --shadow-report artifacts/governance/current-corpus-shadow.json \
  --trial-report artifacts/governance/trial-batch-001.json \
  --trial-report artifacts/governance/trial-batch-002.json \
  --out artifacts/governance/rollout-evidence.json
```

The shadow report should include required-vs-observed counts and similarity
decision counts. Each trial report should describe one 20-challenge
mixed-difficulty batch, including DesignEvidence pass count, build-contract
pass count, ArtifactObservation pass count, aggregate corpus decision, member
decision counts, profile distribution, blocked duplicate count, and any
false-positive review findings.

The generated rollout evidence is intentionally fail-closed. It reports
`production_mode_action = "manual_enable_allowed"` only when the shadow report
exists and the latest two trial batches both pass thresholds. Otherwise it
reports `keep_disabled`; do not enable production mode from OpenSpec validation
or unit-test success alone. After enablement, repeat the same checkpoint review
at cumulative passed trial/production counts of 50, 150, and 500.
