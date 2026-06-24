## Context

The live pipeline is:

`GenerationRequest -> ResearchRun -> DesignTask -> ChallengeDesign ->
BuildAttempt -> ChallengeValidator`.

The previous diversity work governs `technique_family` and
`sub_technique`. The latest difficulty work adds `asset_flow` and
`unintended_solutions` for medium-and-harder designs. These are necessary but
not sufficient:

1. semantic labels do not uniquely determine player actions or implementation;
2. Design attempts do not reserve implementation space before parallel work;
3. Build can still make design-defining choices through defaults;
4. design claims are not checked against built artifacts;
5. individually valid challenges can still form a repetitive corpus.

## Goals

- Move every diversity-defining decision to planning/Design.
- Make each Design traceable to research evidence and to the corpus entries it
  considered.
- Preserve parallel Design execution through transactional reservations.
- Make Build a strict construction layer with a closed contract.
- Validate actual artifacts rather than trusting metadata declarations.
- Gate release on batch/history diversity, not only per-challenge solvability.

## Non-Goals

- Serializing every Design worker.
- Making LLM prose deterministic.
- Replacing human review for borderline similarity.
- Retrofitting full evidence to historical artifacts.

## Decisions

### D0 - Four governed diversity axes

Every new DesignTask SHALL have one profile with these axes:

```json
{
  "semantic": {
    "family": "runtime",
    "sub_technique": "ptrace anti debug"
  },
  "solve": {
    "analysis_mode": "dynamic",
    "required_action": "runtime_hook",
    "chain_shape": "trace-hook-recover",
    "required_tool_class": "debugger"
  },
  "implementation": {
    "artifact_format": "elf",
    "language": "rust",
    "interaction": "file_input",
    "control_structure": "callback_graph",
    "flag_concealment": "runtime_derived_key"
  },
  "presentation": {
    "scenario_type": "malware_triage",
    "input_model": "captured_sample"
  }
}
```

`semantic` states what is taught. `solve` states the indispensable player
action. `implementation` states the construction shape. `presentation` avoids
theme/input-model repetition. A difference on semantic labels alone is not
enough to claim implementation diversity.

Closed per-category vocabularies SHALL live in code and be rendered into
prompts/docs. Unknown profile values are validation failures for new rows;
unlike research family classification, implementation governance cannot
silently coerce to `other`.

`src/domain/design/profile_taxonomy.py` is the vocabulary authority. It defines
the field schema and allowed values for each category. A versioned profile
policy in `generation-profiles.json` references those values, selects compatible
subsets/combinations, and defines quotas; it cannot invent vocabulary values.
Each reservation stores both taxonomy and policy version.

### D1 - Research diversity counts designable findings only

The research quality gate and DesignTask primary-finding allocation SHALL use
only findings whose `kind` is `technique` or `variant` for the distinct
sub-technique floor. `scenario` and `prerequisite` remain available as
supporting evidence and scenario material, but cannot satisfy or consume a
primary technique slot.

Default production behavior remains strict:
`distinct_designable_subtechniques >= target_count`. The existing soft-pass
environment setting may be retained for explicit trial runs, but a soft-passed
run is marked by `research_runs.trial_only = true` and cannot be released
through the production corpus gate. The marker is not duplicated on
GenerationRequest; downstream consumers follow DesignEvidence back to its
source ResearchRun.

### D2 - Profile reservation precedes parallel Design

`design_profile_reservations` stores:

- `id`, `design_task_id`, `generation_request_id`, `reservation_version`;
- `profile jsonb`;
- `profile_signature` (canonical SHA-256);
- `exclusive_signature_key` (nullable text derived from policy);
- `state in {reserved, committed, released}`;
- `taxonomy_version`, `policy_version`, `ledger_version`;
- `created_at`, `committed_at`, `released_at`.

The database enforces:

- `unique(design_task_id, reservation_version)`;
- at most one `state in {reserved, committed}` reservation per DesignTask via a
  partial unique index;
- active rows with non-null `exclusive_signature_key` are unique within the
  policy-defined occupancy scope;
- `design_tasks.current_reservation_id` references the current version.

The allocator reads:

1. the task's semantic assignment and hard request constraints;
2. sibling `reserved` and `committed` profiles;
3. live committed and published historical evidence for the category;
4. category policy from `generation-profiles.json`.

It chooses deterministically using stable candidate ordering, quota, cooldown,
and combination uniqueness. Reservation creation for one request occurs under
the existing parent-request lock. Cross-request allocation additionally locks a
category-scoped `design_profile_ledgers` row, increments its monotonic
`ledger_version`, and writes all request reservations or none. Hard-exclusive
signature occupancy is represented by the policy-derived
`exclusive_signature_key` and protected by a partial unique index over active
rows. Non-exclusive profiles store NULL. Conflicts retry from a fresh ledger
snapshot. This supports parallel Design workers and parallel requests without
allowing both to reserve the same hard-exclusive space.

No profile candidate is silently relaxed across a hard constraint. If no
candidate satisfies hard uniqueness/compatibility, planning returns
`design_diversity_exhausted` with exhausted dimensions. Operators may revise
policy, reduce batch size, or re-run research.

Initial default policy:

- same batch + same sub-technique: hard reject;
- identical semantic + solve + implementation signature: hard reject against
  active batch reservations/live evidence and published history;
- same `solve.required_action`: maximum 30% of a batch;
- same `implementation.flag_concealment`: maximum 20%;
- same implementation language/runtime: maximum 40%;
- same artifact format: maximum 60%;
- presentation dimensions: soft cooldown, warning on relaxation.

Percent caps use `ceil(target_count * ratio)` with minimum one. Operators can
override category values in `generation-profiles.json`.

Superseded, rejected, or `design_unbuildable` evidence remains searchable as
similarity/risk context but does not consume hard quota or exclusive-signature
occupancy. This prevents failed design experiments from permanently exhausting
finite profile space. Revision of the same task may retain its prior profile
under the explicit same-task exemption.

The closed profile vocabulary and compatibility matrix are versioned policy.
Every reservation stores `policy_version`; canonical signatures include both
normalized profile content and policy version. A policy edit cannot reinterpret
an existing reservation. `ledger_version` is the category ledger row's
monotonic integer, incremented once per committed allocation/release
transaction. A ledger advance conflicts with Design completion only when it
changes occupancy relevant to the reservation's hard-exclusive signature or
quota dimensions; unrelated advances do not force retries.

### D3 - Ledger context is bounded and authoritative

Before a Design prompt is rendered, the service constructs a ledger snapshot:

- aggregate occupancy/quota counts over every sibling reservation and committed
  design;
- a bounded nearest-neighbor list of sibling profiles/designs relevant to this
  task;
- the nearest historical committed designs by profile dimensions;
- quota usage and remaining capacity;
- forbidden signatures and repeated actions;
- the reservation's `ledger_version`.

The prompt includes a bounded summary, not entire historical payloads. Default
limit is 10 nearest historical entries plus 20 nearest sibling entries; full
sibling influence is preserved through aggregate quota/signature counts.
Similarity ranking is deterministic and uses structured profile distance before
text similarity.

The Design attempt records the ledger version it consumed. Completion fails
with `stale_design_ledger` if a conflicting committed profile appeared after
that version. Non-conflicting ledger advancement is allowed.

### D4 - Design evidence is a first-class committed artifact

`design_evidence` stores:

- `id`, `design_task_id`, `evidence_version`, `challenge_design_id`;
- `research_finding_ids`;
- committed `profile`;
- `profile_signature`;
- `distinctness_claim`;
- `compared_challenge_ids`;
- `evidence jsonb`;
- `build_contract jsonb`;
- `ledger_version`;
- timestamps and optional supersession metadata.

The database enforces `unique(design_task_id, evidence_version)` plus a partial
unique index allowing at most one non-superseded evidence row.
`design_tasks.current_design_evidence_id` references the current version.

A valid evidence object must:

- cite only findings from the task's ResearchRun;
- cite at least one designable finding;
- identify concrete research claims used;
- compare against actual sibling/historical IDs supplied by the ledger;
- explain differences on solve and implementation axes;
- exactly match the reserved governed profile;
- provide a complete build contract.

Completion of Design, insertion of `ChallengeDesign`, insertion of
`DesignEvidence`, and `reservation reserved -> committed` happen in one
transaction. Retry after validation failure keeps the reservation. Explicit
task regeneration releases/supersedes the reservation and evidence under the
parent lock.

Persisting a Design with a failed quality gate leaves the task inspectable in
`designed`, so this change also adds an explicit revision path:
`request_design_revision(design_task_id)`. It is allowed for `designed`,
`build_failed`, or `built` tasks with no active BuildAttempt, provided the built
version has not been included in a released production corpus batch. Under the
task/request lock it
supersedes the live ChallengeDesign and DesignEvidence, releases the old
reservation, allocates a fresh reservation (the same profile may be selected
for a revision of the same task), clears stale plan review, and returns the task
to `draft`. The task must pass the existing plan-review checkpoint again before
`draft -> queued` and a new Design attempt. Revision never edits a committed
contract in place.
Prior BuildAttempts and observations remain immutable history. A
production-released challenge cannot be revised in place; it requires a new
DesignTask/version so released bundles stay reproducible.

Every BuildAttempt stores the `design_evidence_id` and contract hash it builds.
After revision, prior attempts remain terminal history but no longer own parent
task roll-forward. BuildReconciler SHALL update the DesignTask only from an
attempt whose `design_evidence_id` equals
`design_tasks.current_design_evidence_id`. This prevents an old successful
attempt from moving a revised draft back to `built`.

### D5 - Build contract defines the construction boundary

`build_contract` contains:

```json
{
  "required_profile": {},
  "required_player_actions": [],
  "required_components": [],
  "required_asset_flow": [
    {
      "stage_id": "recover-key",
      "produced_asset_or_capability": "runtime key",
      "verification_harness": {
        "test_kind": "fixture_assertion",
        "fixture_ref": "key-recovery-output",
        "assertion": "non_empty"
      },
      "dependency_harness": {
        "test_kind": "solver_without_fixture",
        "fixture_ref": "runtime-key",
        "assertion": "must_fail"
      }
    }
  ],
  "forbidden_shortcuts": [
    {
      "id": "direct-run",
      "test_kind": "artifact_direct_run",
      "artifact_ref": "primary",
      "input_fixture": null,
      "assertion": "stdout_not_contains_flag"
    }
  ],
  "acceptance_tests": [],
  "allowed_implementation_freedom": []
}
```

Design does not provide executable names or shell commands. Each test is a
declarative invocation of a closed host-owned harness, for example:

```json
{
  "id": "direct-run",
  "test_kind": "artifact_direct_run",
  "artifact_ref": "primary",
  "input_fixture": null,
  "assertion": "stdout_not_contains_flag"
}
```

The host maps `test_kind`, fixture references, and closed assertions to fixed
implementations. Unknown test kinds, arbitrary executables, shell strings, path
traversal, or undeclared fixture references are invalid. Category policy
defines mandatory baseline harnesses.

Every required asset-flow stage has a stable `stage_id`, one verification
harness proving its declared output/capability exists, and one dependency
harness proving the downstream solve fails when that output/capability is
withheld or invalidated.

Build may choose local engineering details listed under
`allowed_implementation_freedom`, such as file names, function names, and
dependency patch versions. It may not change:

- semantic/solve/implementation profile;
- required player actions;
- artifact format/language/runtime;
- interaction and flag-concealment mechanisms;
- required asset flow;
- negative-test meaning.

If construction is infeasible, Build reports `design_unbuildable` with concrete
evidence. It does not substitute C/ELF/XOR or redesign the challenge.

### D6 - Quality and evidence are hard build admission gates

`BuildOrchestrationService` accepts a new build only when:

- task status is otherwise eligible;
- latest Design exists;
- `quality_gate_passed = true`;
- live committed DesignEvidence exists;
- reservation is `committed`;
- DesignEvidence profile signature matches the reservation;
- build contract validates for the category.

The full contract and evidence ID are embedded in the attributed shard.
Governed matrix values have no generic defaults. Missing governed values cause
`build_contract_incomplete`.

Historical designs without evidence remain readable and revalidatable, but a
new production build/rebuild requires explicit migration into a reviewed
contract or an operator-only `legacy_trial` mode that can never pass the
production corpus gate.

### D7 - Host observation, not metadata, establishes implementation truth

`artifact_observations` stores:

- `id`, `build_attempt_id` (unique);
- `design_evidence_id`, `contract_sha256`, `artifact_manifest_sha256`;
- `observed_profile jsonb`;
- `contract_checks jsonb`;
- `negative_test_results jsonb`;
- `fingerprints jsonb`;
- `status in {passed, failed, inconclusive}`;
- timestamps.

The host extracts category-appropriate facts:

- artifact format, architecture, toolchain/language evidence;
- imports/APIs/syscalls and selected structural markers;
- interaction model;
- solver behavior and target references;
- flag exposure/concealment evidence;
- normalized source, solver, and intended-path fingerprints.

Observation plugins are category-specific and return `unknown` when a fact
cannot be established. A governed required field observed as `unknown` is not a
pass; the observation becomes `inconclusive`. A separate
`observation_review_decisions` row may accept an inconclusive observation with
actor, reason, scope, and timestamp when policy permits it. Failed observations
and hard mismatches are never review-overrideable.

The validator then:

1. runs existing artifact/solver checks;
2. runs positive acceptance tests;
3. runs declared negative tests;
4. compares required and observed profiles;
5. verifies required asset stages using contract-specific tests;
6. computes artifact-manifest and contract hashes;
7. persists the observation before terminal build reconciliation.

An observation is reusable only when
`(build_attempt_id, design_evidence_id, contract_sha256,
artifact_manifest_sha256)` still matches. Any contract or artifact change
invalidates reuse and requires validation again.

Runner/reconciler authority is:

- normal validation invokes the contract-aware validator and records the bound
  observation before writing validate/complete success;
- resume/all-skipped may carry forward success only when a bound observation
  still matches every hash and is effectively accepted;
- Reconciler may set BuildAttempt `succeeded` only when the attributed
  observation is `passed`, or `inconclusive` with a valid allowed observation
  review, and all existing solve/artifact conditions pass;
- `metadata.solve_status = passed` without accepted observation is insufficient
  for new governed builds.

Closed failure codes include:

- `build_contract_incomplete`;
- `implementation_contract_mismatch`;
- `unintended_solution_succeeded`;
- `asset_flow_not_required`;
- `solver_not_artifact_derived`;
- `observation_inconclusive`.

### D8 - Corpus gate is a release gate

The new corpus service compares a candidate batch with:

- other candidates in the batch;
- committed/published historical observations.

Fingerprints:

- semantic profile signature;
- solve profile signature;
- implementation profile signature;
- combined governed signature;
- normalized source-token MinHash/Jaccard input;
- normalized solver-token MinHash/Jaccard input;
- intended-path signature.

Default decisions:

- identical combined governed signature: `blocked`;
- same sub-technique within one production batch: `blocked`;
- source token Jaccard >= 0.65: `blocked`;
- source token Jaccard >= 0.45: `review_required`;
- solver token Jaccard >= 0.75: `blocked`;
- solver token Jaccard >= 0.55: `review_required`;
- quota violation: `blocked`;
- observation `failed`: `blocked`;
- observation `inconclusive` without an allowed observation review: `blocked`;
- observation `inconclusive` with an allowed observation review: corpus result
  cannot exceed `review_required`.

Thresholds are configurable per category. Text similarity is supporting
evidence, never the only reason to declare semantic identity. The service
stores matched challenge IDs and scores so an operator can review concrete
pairs.

Production publication requires corpus result `passed`. `review_required`
requires an explicit operator decision with actor, reason, and timestamp.
Overrides cannot bypass exact combined-signature duplicates or failed
validation.

Corpus persistence is explicit:

- `corpus_batches`: id, mode, category scope, policy version, status, creator,
  timestamps;
- `corpus_batch_members`: batch id, build attempt id, design evidence id, and
  immutable observation/fingerprint references;
- `corpus_decisions`: one current decision per member and one aggregate batch
  decision;
- `corpus_matches`: compared pair, fingerprint type, score, threshold, reason;
- `corpus_review_decisions`: actor, reason, scope, timestamp, and decision.
- `corpus_history_entries`: append-only normalized governed signatures and
  fingerprints for published or retired challenges.

Membership is immutable once evaluation starts. Rebuilt/revised challenges enter
a new batch membership version. The pack command names one corpus batch
explicitly; there is no implicit "current batch".

The existing delivery packer is the production publication boundary. In
production mode it selects only challenges whose BuildAttempt has a passing
ArtifactObservation, or an inconclusive observation with an allowed observation
review, and whose explicitly selected member decision is passed (or has an
allowed corpus review). The aggregate batch decision must also be passed.
Observation review and corpus review are independent records. Selection by
`metadata.build_status` alone is no longer sufficient.
Shadow/trial packing remains available only through explicit mode flags and
records that the output is non-production.

Operational deletion of a published challenge may remove mutable task/build
rows and files according to resource-deletion policy, but SHALL retain a
minimal corpus history entry unless the operator invokes a separate explicit
governance-history purge. Non-published failed/trial records may be fully
deleted. This keeps duplicate prevention stable without retaining full
artifacts or sensitive logs.

### D9 - Lifecycle and regeneration

Reservation/evidence lifecycle follows DesignTask lifecycle:

- initial task generation: reserve;
- Design success: commit reservation + evidence;
- Design retry: preserve reservation;
- explicit Design revision: supersede current design/evidence, release and
  re-reserve, then return the task to `draft` for plan review before queue;
- regenerate one/all before queue: release old reservation, supersede evidence,
  allocate fresh reservation, clear review marker;
- delete non-published task/request: cascade mutable governance rows under
  resource-deletion rules;
- delete published/retired challenge: retain minimal corpus history projection;
- build retry without Design change: reuse committed evidence/contract;
- contract revision: new Design attempt/evidence version, then clean build.

No released reservation counts against quota. Committed evidence remains in
history after supersession so the allocator does not recreate the same idea
without seeing it.

### D10 - Dashboard boundaries

The dashboard displays:

- reserved profile and quota explanation;
- evidence citations and compared challenge IDs;
- build-contract summary;
- observed-vs-required differences;
- negative-test results;
- corpus matches and decision.

It exposes service-backed actions only: regenerate/re-reserve at planning,
retry Design, submit Build, request corpus review, record allowed review
decision. It never computes profiles, fingerprints, similarity, or admission
policy client-side.

## Risks / Trade-offs

- **Profile vocabulary can become too rigid.** Mitigate through versioned
  category policy and explicit `design_diversity_exhausted`, not silent
  fallback.
- **Observation cannot infer every semantic fact.** Unknown is surfaced as
  review-required; metadata is never accepted as proof.
- **Corpus comparison cost grows with 500+ challenges.** Store fingerprints and
  shortlist by category/profile before exact Jaccard comparison.
- **Reservation churn can reduce capacity.** Release only under parent locks
  and retain committed/superseded evidence as historical context.
- **Negative test execution is security-sensitive.** Use closed host-owned
  harness kinds, bounded timeout/output, challenge-local cwd, and no arbitrary
  executable or shell.

## Migration

Additive migrations create the three tables and nullable references. No
historical backfill is mandatory.

- historical DesignTasks have no reservation/evidence and are `legacy`;
- historical successful artifacts may be asynchronously fingerprinted for
  comparison without becoming production-contract-compliant;
- existing revalidation continues to use existing checks;
- new production build submissions require the new evidence path;
- a one-time operator tool may import selected historical challenges as
  reviewed evidence after manual normalization.

## Rollout

1. Shadow mode: allocate profiles and compute observations/corpus findings
   without blocking.
2. Trial mode: hard-gate Design evidence and build admission for a 20-challenge
   batch; corpus review is manual.
3. Production mode: hard-gate observations and corpus decisions.
4. Scale checkpoints: 50, 150, then 500 challenges, with threshold review after
   each checkpoint.
