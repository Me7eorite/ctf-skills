## ADDED Requirements

### Requirement: Design-task generation enforces two-axis technique diversity

Design-task generation SHALL allocate findings to tasks using two diversity
axes derived from `src/domain/design/technique_taxonomy.py`:

- a coarse **family** axis (`resolve_family`), with a soft quota of
  `ceil(target_count / distinct_families_in_pool)` and a cooldown window
  `cooldown_window` (default `1`) discouraging reuse of a family by the
  immediately preceding task when an alternative exists; and
- a fine **sub-technique** axis (`resolve_sub_technique`), with a cap of
  `ceil(target_count / distinct_sub_techniques_in_pool)`.

The quota divisors and cooldown window SHALL be configurable via
`generation-profiles.json` (`technique_quota`, `cooldown_window`) with sane
defaults when absent.

When the finding pool cannot satisfy a quota, the planner SHALL relax the family
axis first, then the sub-technique axis, then fall back to round-robin
allocation, and SHALL ALWAYS produce exactly `target_count` tasks. Low diversity
SHALL NOT fail generation; it SHALL be recorded as a warning (see the
`diversity_flags` requirement). When the optional Hermes planner runs for a
hard/expert task it SHALL receive the sub-technique keys already used by sibling
tasks as an avoid-set.

#### Scenario: Monocultural pool still produces target_count tasks, all flagged

- **GIVEN** a research run whose findings all resolve to one sub-technique
- **AND** a generation request with `target_count = 4`
- **WHEN** design tasks are generated
- **THEN** exactly four `design_tasks` rows are created
- **AND** every task's `diversity_flags.warnings` contains `subtechnique_duplicate`

#### Scenario: Same family, distinct sub-techniques is allowed with a mild flag

- **GIVEN** a pool of findings all in family `injection` but with distinct sub-techniques
- **AND** `target_count` exceeds the family quota
- **WHEN** design tasks are generated
- **THEN** affected tasks carry `family_quota_exceeded`
- **AND** no task carries `subtechnique_duplicate`

#### Scenario: Diverse pool yields no diversity warnings

- **GIVEN** a pool with at least `target_count` distinct sub-techniques across multiple families
- **WHEN** design tasks are generated
- **THEN** no task's `diversity_flags.warnings` is non-empty

### Requirement: Design tasks store machine-readable diversity flags

Each generated `design_task` SHALL store a `diversity_flags` object computed
once at plan time with shape
`{"family": <lane>, "sub_technique": <key>, "warnings": [<enum>...]}` where each
warning is one of `family_quota_exceeded | subtechnique_duplicate |
family_other`. Downstream consumers (dashboard, API, logs) SHALL read this
stored object and SHALL NOT recompute diversity. The `warnings` field is a list
so a task MAY carry multiple warnings simultaneously.

#### Scenario: diversity_flags is persisted and exposed

- **GIVEN** a generated design task
- **WHEN** the design-task API resource is read
- **THEN** the response includes the stored `diversity_flags` object
- **AND** the object is not recomputed from the findings at read time

### Requirement: Draft tasks require plan review before queue release

The `draft -> queued` transition SHALL be gated by a `plan_reviewed_at` marker
on the design task. A task whose plan has not been reviewed (NULL
`plan_reviewed_at`, and not grandfathered as review-exempt) SHALL NOT be queued;
the transition SHALL be rejected with the machine-readable reason
`plan_not_reviewed`. Drafts created before this requirement SHALL be treated as
review-exempt so in-flight work is not stranded and no backfill is required.

Plan review, full regeneration, and single-task regeneration SHALL all be
performed through `DesignTaskPlanningService` under the existing parent-request
lock. The dashboard SHALL invoke these as service-backed HTTP actions and SHALL
NOT write design-task rows directly.

#### Scenario: Unreviewed draft cannot be queued

- **GIVEN** a design task with `status = "draft"` and NULL `plan_reviewed_at` (not exempt)
- **WHEN** the operator queues it
- **THEN** the transition is rejected with reason `plan_not_reviewed`
- **AND** the status remains `draft`

#### Scenario: Approved draft can be queued

- **GIVEN** a design task with `status = "draft"` whose plan has been approved via the service
- **WHEN** the operator queues it
- **THEN** the status becomes `queued`

#### Scenario: Grandfathered draft is queueable without review

- **GIVEN** a `draft` task created before this requirement and marked review-exempt
- **WHEN** the operator queues it
- **THEN** the transition is allowed

### Requirement: Single-task regeneration preserves sibling diversity

The planning subsystem SHALL support regenerating exactly one task slot via
`DesignTaskPlanningService.regenerate_task(request_id, task_no)`. The
re-planned slot SHALL receive the families and sub-techniques of all *other*
current draft tasks for the request as an avoid-set, so a single regeneration
cannot re-introduce a duplicate it was meant to remove. The operation SHALL run
under the parent-request lock and SHALL be rejected if any task for the request
has left `draft`/`archived`, consistent with the existing
"regeneration only before queue release" requirement.

#### Scenario: Regenerating one slot removes its duplicate

- **GIVEN** a request whose tasks are all `draft` and one task carries `subtechnique_duplicate`
- **WHEN** the operator regenerates just that task
- **THEN** the regenerated task avoids every sibling's sub-technique where the pool allows
- **AND** the sibling tasks are unchanged

#### Scenario: Single regeneration is blocked after queue release

- **GIVEN** a request with at least one task in `queued`
- **WHEN** the operator regenerates a single task
- **THEN** the operation is rejected
- **AND** no task is modified

### Requirement: Dashboard renders a diversity-aware plan matrix

The dashboard design-task view SHALL render a plan matrix showing, per task,
`task_no`, `difficulty`, family, sub-technique, and scenario seed, reading the
stored `diversity_flags`. The matrix SHALL visually distinguish
`family_quota_exceeded` (mild) from `subtechnique_duplicate` (the
duplicate-考点 signal). It SHALL expose approve, regenerate-all, and
regenerate-one actions backed by the planning service, and SHALL show a
"research diversity insufficient — consider re-running research" hint when
`subtechnique_duplicate` occurrences exceed a threshold.

#### Scenario: Duplicate sub-technique is highlighted distinctly from family quota

- **GIVEN** a generated batch where one task carries `subtechnique_duplicate` and another carries only `family_quota_exceeded`
- **WHEN** the operator views the plan matrix
- **THEN** the two warnings are rendered with distinct severity styling
- **AND** the matrix exposes a regenerate-one action for the duplicate task

#### Scenario: Pervasive duplicates surface the re-research hint

- **GIVEN** a batch where `subtechnique_duplicate` occurrences exceed the configured threshold
- **WHEN** the operator views the plan matrix
- **THEN** the view shows the "research diversity insufficient" hint
