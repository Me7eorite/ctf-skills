## ADDED Requirements

### Requirement: Design-task generation applies family governance and sub-technique diagnostics

Design-task generation SHALL allocate findings with two **unequally-weighted**
axes derived from `src/domain/design/technique_taxonomy.py`. Diversity is a
greedy preference, never a hard gate: generation SHALL ALWAYS produce exactly
`target_count` tasks, and low diversity SHALL only annotate tasks (see the
`diversity_flags` requirement), never fail, block, or shrink the set.

- **Family is the governance axis** (`resolve_family`). It carries a soft quota
  `ceil(target_count / distinct_families_in_pool)` and a cooldown window
  `cooldown_window` (default `1`) discouraging reuse of a family by the
  immediately preceding task when an alternative exists. When the pool cannot
  satisfy the family quota the planner SHALL relax it and record
  `family_quota_exceeded`. The family quota divisor and cooldown window SHALL be
  the only configurable diversity knobs, via `generation-profiles.json`
  (`technique_quota`, `cooldown_window`) with sane defaults when absent.

- **Sub-technique is the diagnostic axis** (`resolve_sub_technique`). Among the
  family-preferred candidates the planner SHALL also prefer one whose
  sub-technique no sibling task has used (best-effort avoidance), and SHALL
  record `subtechnique_duplicate` when no such candidate remains. Sub-technique
  SHALL NOT have its own quota knob and SHALL NOT trigger a separate fallback
  ladder; it only re-orders preference within the family-preferred set and
  annotates.

`technique_family` SHALL be treated as a soft governance signal, not absolute
truth: upstream taxonomy drift SHALL affect only governance quality, never the
produced count or task validity. When both axes are unsatisfiable the planner
SHALL fall back to the existing round-robin. When the optional Hermes planner
runs for a hard/expert task it SHALL receive the sub-technique keys already used
by sibling tasks as an avoid-set.

The sub-technique axis SHALL remain a single best-effort preference plus a
diagnostic flag. Implementations SHALL NOT introduce a second quota, second
`Counter`-driven gate, or independent fallback ladder for sub-technique; family
is the only axis with a quota and the only axis that can trigger a relaxation
step.

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

### Requirement: Sub-technique normalization is canonical and synonym-folded

`resolve_sub_technique(label) -> str` SHALL reduce a label to a canonical key by,
in order: (1) lowercasing; (2) trimming and collapsing internal separators
(whitespace, hyphens, underscores) to a single space; (3) stripping a closed
list of generic qualifier tokens that do not change technique identity (`decode`,
`decoding`, `decrypt`, `decryption`, `encrypt`, `encryption`, `cipher`, `attack`,
`technique`, `vuln`, `bug`); and (4) applying a preset alias/synonym map to a
canonical term. As a result `xor`, `XOR`, `xor-decrypt`, and `xor decrypt` SHALL
all map to the same key, so they cannot masquerade as distinct sub-techniques and
dilute the `subtechnique_duplicate` signal. Normalization SHALL be deterministic
(pure string operations plus fixed maps).

Normalization SHALL be **conservative**: it canonicalizes surface spellings and
generic qualifiers of the *same* technique and SHALL NOT merge genuinely distinct
techniques — `base64` and `base32` SHALL remain distinct keys, `xor` and `rc4`
SHALL remain distinct, and `xor key recovery` SHALL NOT normalize to `xor` (the
qualifier list deliberately excludes `key`/`recovery`). The qualifier list and
alias map SHALL live in `technique_taxonomy.py` as the single source of truth and
are consumed both by this change's diversity dedup and by
`fix-difficulty-step-inflation`'s mechanical fold. Because over-broad aliases
silently erode both signals, the conservatism rule SHALL be guarded by a
regression test pinning a list of must-stay-distinct technique pairs; adding an
alias that collapses any pinned pair SHALL fail that test.

#### Scenario: xor surface variants collapse to one key

- **GIVEN** labels `xor`, `XOR`, `xor-decrypt`, and `xor decrypt`
- **WHEN** `resolve_sub_technique` is applied to each
- **THEN** all four yield the same canonical key

#### Scenario: Distinct techniques and analysis steps stay distinct

- **GIVEN** labels `base64`, `base32`, and `xor key recovery`
- **WHEN** `resolve_sub_technique` is applied
- **THEN** `base64` and `base32` yield different keys
- **AND** `xor key recovery` does NOT normalize to `xor`

### Requirement: Diversity allocation is deterministic

The finding-to-task allocation SHALL be a pure deterministic function of its
inputs — the ordered finding list, the difficulty distribution, and the profile
knobs (`technique_quota`, `cooldown_window`) — covering family/sub-technique
assignment, cooldown, fallback, and the resulting `diversity_flags`. The same
inputs SHALL always produce the same allocation and the same flags. The
implementation SHALL NOT use randomness or wall-clock time, and ties SHALL be
broken by a stable key (e.g. ascending finding index / id).

The optional Hermes planner enrichment for hard/expert tasks MAY vary its prose
(scenario seed, chain outline) across runs, but it SHALL NOT change which
finding/family/sub-technique is allocated to a slot; the deterministic
allocation decision is taken before, and independently of, any Hermes call, so
the diversity outcome is reproducible even when Hermes output differs.

#### Scenario: Same inputs produce the same allocation

- **GIVEN** a fixed ordered finding list, difficulty distribution, and profile knobs
- **WHEN** design tasks are generated twice
- **THEN** both runs assign the same family and sub-technique to each task_no
- **AND** both runs produce identical `diversity_flags`

#### Scenario: Hermes prose variation does not change allocation

- **GIVEN** a hard task whose Hermes enrichment returns different prose on a second run
- **WHEN** design tasks are generated both times
- **THEN** the allocated finding/family/sub-technique for that task_no is identical across runs

### Requirement: Design tasks store machine-readable diversity flags

Each generated `design_task` SHALL store a `diversity_flags` object computed
once at plan time with shape
`{"family": <lane>, "sub_technique": <key>, "warnings": [<enum>...]}` where each
warning is one of `family_quota_exceeded | subtechnique_duplicate |
family_other`. Downstream consumers (dashboard, API, logs) SHALL read this
stored object and SHALL NOT recompute diversity. The `warnings` field is a list
so a task MAY carry multiple warnings simultaneously. The warnings are **not
co-equal**: `family_quota_exceeded` reports a governance relaxation while
`subtechnique_duplicate` is a finer best-effort diagnostic hint; consumers SHALL
treat the sub-technique flag as advisory, not as an absolute dedup verdict.

All `diversity_flags.warnings` (`family_quota_exceeded`, `subtechnique_duplicate`,
`family_other`) are **diagnostic and intervention signals only**. They SHALL NOT
block any state transition, SHALL NOT gate `draft -> queued`, and SHALL NOT be a
precondition for approval. Neither the backend nor the UI SHALL treat a warning
as an additional approval gate; the **only** queue precondition is
`plan_reviewed_at`. A task carrying any warning remains fully queueable once its
plan is approved, leaving the decision to act on a warning entirely with the
operator.

#### Scenario: diversity_flags is persisted and exposed

- **GIVEN** a generated design task
- **WHEN** the design-task API resource is read
- **THEN** the response includes the stored `diversity_flags` object
- **AND** the object is not recomputed from the findings at read time

#### Scenario: A flagged task is still queueable after approval

- **GIVEN** an approved `draft` task whose `diversity_flags.warnings` contains `subtechnique_duplicate`
- **WHEN** the operator queues it
- **THEN** the transition succeeds (the warning does not block queueing)
- **AND** the only precondition enforced is `plan_reviewed_at`

### Requirement: Draft tasks require plan review before queue release

The `draft -> queued` transition SHALL be gated by a `plan_reviewed_at` marker:
a task whose plan has not been reviewed (NULL `plan_reviewed_at`, and not
grandfathered as review-exempt) SHALL NOT be queued and the transition SHALL be
rejected with the machine-readable reason `plan_not_reviewed`. Drafts created
before this requirement SHALL be treated as review-exempt so in-flight work is
not stranded and no backfill is required.

`plan_reviewed_at` is **review metadata, not a status or a new stage in the
design-task state machine**: the status set
(`draft|queued|designing|designed|failed|archived|building|built|build_failed`)
is unchanged, and the marker only adds a precondition to the *existing*
`draft -> queued` transition.

Because approval does not change status, a task SHALL remain `draft` throughout
review and regeneration SHALL stay available the entire time; the review gate
SHALL NOT widen the window in which regeneration is blocked (that window opens
only after `draft -> queued`, unchanged from today). Plan review, full
regeneration, and single-task regeneration SHALL all be performed through
`DesignTaskPlanningService` under the existing parent-request lock. The dashboard
SHALL invoke these as service-backed HTTP actions and SHALL NOT write design-task
rows directly.

Approval SHALL apply to the specific draft version it stamped, never to the
request as a whole and never as a permanent authorization. Any regeneration —
full (`regenerate_plan`) or single-task (`regenerate_task`) — that replaces a
task SHALL clear that task's `plan_reviewed_at`, invalidating the prior approval,
so queueing that task again requires a fresh approval. After a single-task
regeneration only the regenerated task loses its approval; untouched sibling
tasks keep theirs.

Approval and regeneration SHALL serialize under the same parent-request lock used
by `replace_draft_or_archived_tasks`, so concurrent calls cannot interleave:
approval is idempotent (re-approving an already-reviewed plan is a no-op or a
timestamp refresh, never an error), and clearing `plan_reviewed_at` on
regenerated rows prevents a stale approval from leaking a never-reviewed plan
into `queued`. The lock behaviour before and after queue release is consistent:
while tasks are `draft` the lock serializes approve/regenerate; once any task is
`queued` both regeneration paths are rejected (the existing "regeneration only
before queue release" rule), so there is no window in which a queued plan can be
re-approved or re-planned.

#### Scenario: Concurrent approve and regenerate do not interleave

- **GIVEN** a request whose tasks are all `draft`
- **WHEN** an approval and a regenerate-all are issued concurrently
- **THEN** they serialize under the parent-request lock
- **AND** if regeneration wins last, the regenerated rows have NULL `plan_reviewed_at` (must be re-approved before queueing)

#### Scenario: Re-approving an already-reviewed plan is idempotent

- **GIVEN** a design task already carrying a `plan_reviewed_at`
- **WHEN** the operator approves it again
- **THEN** the operation succeeds as a no-op or timestamp refresh
- **AND** no error is raised

#### Scenario: Regenerating one task invalidates only its own approval

- **GIVEN** a request whose draft tasks are all approved (`plan_reviewed_at` set)
- **WHEN** the operator regenerates a single task
- **THEN** that task's `plan_reviewed_at` is cleared and it must be re-approved before queueing
- **AND** the untouched sibling tasks keep their approval

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

### Requirement: Single-task regeneration returns a three-state outcome

`DesignTaskPlanningService.regenerate_task(request_id, task_no)` SHALL re-plan
exactly one task slot and SHALL return one of three outcome states:
`regenerated`, `regenerated_with_warning`, or `no_alternative`. This contract is
**deliberately asymmetric** with batch generation / regenerate-all: those must
preserve `count == target_count` and therefore fall back to a duplicate-flagged
slot, whereas regenerate-one has no count obligation and MAY decline. The two
SHALL NOT share a single code path that forces a fill.

The operation SHALL build its candidate set from findings matching the slot's
hard constraints (category, difficulty, port), exclude sibling sub-techniques,
and prefer candidates within the family quota / cooldown. It SHALL then resolve:

- **`regenerated`** — a candidate exists that differs from the current slot's
  `(family, sub_technique)`, is not a sibling sub-technique, and is within the
  family quota/cooldown. The slot is replaced; no diversity warning is added.
- **`regenerated_with_warning`** — a candidate exists that differs from the
  current slot and from all sibling sub-techniques, but is only available outside
  the family quota/cooldown. The slot is replaced and the new task's
  `diversity_flags` carries `family_quota_exceeded`. Family saturation SHALL
  surface here, never as `no_alternative` (family is a soft governance axis per
  D0; there is no `family_exhausted` no-alternative reason).
- **`no_alternative`** — the only remaining candidates are equivalent to the
  current slot (same `(family, sub_technique)`) or are sibling sub-technique
  duplicates. The operation SHALL be a **true no-op**: the slot is left
  unchanged, with no row replacement and no timestamp churn. It SHALL carry a
  machine-readable `reason` from the **closed set of exactly two values** —
  `research_diversity_insufficient` (the pool has no other distinct finding for
  the slot) or `subtechnique_exhausted` (distinct findings exist but all are
  sibling sub-technique duplicates). No other `no_alternative` reason is
  permitted; in particular there is no `family_exhausted` (family is soft and
  cannot cause a refusal). When both could apply, `research_diversity_insufficient`
  takes precedence (it is the more general, re-research-triggering signal).

The operation SHALL run under the parent-request lock and SHALL be rejected if
any task for the request has left `draft`/`archived`, consistent with the
existing "regeneration only before queue release" requirement.

#### Scenario: Clean regeneration when the pool allows

- **GIVEN** a request whose tasks are all `draft` and one task carries `subtechnique_duplicate`
- **AND** the pool has a within-quota finding using neither the slot's nor any sibling's sub-technique
- **WHEN** the operator regenerates just that task
- **THEN** the outcome is `regenerated`
- **AND** the new task avoids every sibling's sub-technique with no diversity warning
- **AND** the sibling tasks are unchanged

#### Scenario: Different finding but family saturated yields a warning, not a refusal

- **GIVEN** the only sibling-avoiding candidate belongs to an already-quota-exceeded family
- **WHEN** the operator regenerates the task
- **THEN** the outcome is `regenerated_with_warning`
- **AND** the new task's `diversity_flags.warnings` contains `family_quota_exceeded`
- **AND** the outcome is never `no_alternative` on account of family saturation

#### Scenario: Only sibling duplicates remain refuses as a no-op

- **GIVEN** a request whose distinct findings for the slot are all sibling sub-technique duplicates
- **WHEN** the operator regenerates the task
- **THEN** the outcome is `no_alternative` with reason `subtechnique_exhausted`
- **AND** the slot is unchanged (no row replacement, no timestamp change)

#### Scenario: No distinct finding refuses with the re-research reason

- **GIVEN** a request whose pool offers no finding distinct from the slot's current `(family, sub_technique)`
- **WHEN** the operator regenerates the task
- **THEN** the outcome is `no_alternative` with reason `research_diversity_insufficient`
- **AND** the slot is unchanged

#### Scenario: Single regeneration is blocked after queue release

- **GIVEN** a request with at least one task in `queued`
- **WHEN** the operator regenerates a single task
- **THEN** the operation is rejected
- **AND** no task is modified

### Requirement: Dashboard is a read-only plan-matrix review surface with three actions

The dashboard design-task view SHALL be a **read-only review surface plus
exactly three service-backed actions** — approve, regenerate-all, and
regenerate-one — and SHALL NOT compute diversity or planning policy itself nor
expose additional intervention actions (the three-action cap keeps it from
drifting into a planning console). It SHALL render a plan matrix showing, per
task, `task_no`, `difficulty`, family, sub-technique, and scenario seed by
**reading the stored `diversity_flags`** (never recomputing). The dashboard
SHALL NOT re-derive `family` or `sub_technique` from `label` client-side; it
renders only the server-computed values, so the UI and the planner can never
disagree on classification. The matrix SHALL
visually distinguish `family_quota_exceeded` (mild governance signal) from
`subtechnique_duplicate` (finer diagnostic hint). It SHALL show a "research
diversity insufficient — consider re-running research" hint when a
regenerate-one action returns `no_alternative` with reason
`research_diversity_insufficient` — that machine-readable response, not a fuzzy
warning-count threshold, is the authoritative trigger for the hint.

#### Scenario: Duplicate sub-technique is highlighted distinctly from family quota

- **GIVEN** a generated batch where one task carries `subtechnique_duplicate` and another carries only `family_quota_exceeded`
- **WHEN** the operator views the plan matrix
- **THEN** the two warnings are rendered with distinct severity styling
- **AND** the matrix exposes a regenerate-one action for the duplicate task

#### Scenario: A no_alternative regeneration surfaces the re-research hint

- **GIVEN** a duplicate task whose regenerate-one returns `no_alternative` with reason `research_diversity_insufficient`
- **WHEN** the operator views the plan matrix
- **THEN** the view shows the "research diversity insufficient — consider re-running research" hint

### Requirement: Pre-change rows are grandfathered without backfill

All new columns SHALL be nullable and the system SHALL operate on rows that
predate this change without any data backfill:

- A `research_finding` with NULL `technique_family` SHALL resolve its family via
  `resolve_family` (label derivation, else `other`); it SHALL NOT be rejected.
- A `design_task` with NULL `diversity_flags` SHALL be tolerated everywhere: the
  API SHALL return it as absent/empty and the dashboard SHALL render it with no
  diversity warning rather than erroring or recomputing.
- A `draft` design task created before this change SHALL be review-exempt — its
  `draft -> queued` transition SHALL be allowed despite NULL `plan_reviewed_at`.
  Exemption is determined by a generation-time marker, or equivalently by NULL
  `plan_reviewed_at` together with a `created_at` preceding the migration
  revision, so historical drafts are never stranded by the new queue gate.

Only design tasks generated after this change carry the review obligation, the
two-axis allocation, and `diversity_flags`.

#### Scenario: Legacy draft queues without review

- **GIVEN** a `draft` task created before this change with NULL `plan_reviewed_at`
- **WHEN** the operator queues it
- **THEN** the transition is allowed (review-exempt)

#### Scenario: Legacy task without diversity_flags renders cleanly

- **GIVEN** a `design_task` with NULL `diversity_flags`
- **WHEN** it is read via the API and shown on the plan matrix
- **THEN** the API returns it with no diversity flags
- **AND** the dashboard renders it without a diversity warning and without recomputing
