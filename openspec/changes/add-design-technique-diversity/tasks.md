## 1. Shared classification foundation (Layer 1)

- [x] 1.1 Create `src/domain/design/technique_taxonomy.py` with a **closed `TechniqueFamily`
      lane set per category** (mirroring `category-tactics.md`), enumerated in full — not
      "their own lanes":
      - web: `auth | injection | server_side | client_side | upload | node_api | other`
      - pwn: `stack | format_string | heap | integer_oob | sandbox | kernel | other`
      - re:  `crackme | vm_bytecode | runtime | language | platform | visual_game | other`
      Every category includes `other` as the catch-all. Add a `label`→lane keyword map,
      `resolve_family(finding) -> str`
      (stored value → keyword derivation → `other`), and
      `resolve_sub_technique(finding) -> str`. `resolve_sub_technique` canonicalizes the
      label: lowercase → trim/collapse separators (space/hyphen/underscore) → strip a closed
      qualifier list (`decode|decoding|decrypt|decryption|encrypt|encryption|cipher|attack|technique|vuln|bug`)
      → apply a preset alias/synonym map. It MUST be conservative — collapse surface
      variants of the *same* technique (`xor`/`XOR`/`xor-decrypt`/`xor decrypt` → one key)
      but NOT merge distinct techniques (`base64`≠`base32`). The qualifier list and alias
      map live here as the single source of truth.
      The module is **classification-only**: it MUST NOT contain difficulty,
      mechanical-transform, or chain-folding logic, and MUST NOT import from
      services, web, or `difficulty.py`. It is the single source of truth for
      family/sub-technique normalization.
- [x] 1.2 Unit-test `resolve_family`/`resolve_sub_technique`: stored valid value wins;
      unknown stored value → `other` + warning; NULL value derives from `label`;
      `blind SQLi`/`second-order SQLi`/`SQLi login bypass` all resolve to family `injection`
      with distinct sub-techniques; `xor`/`XOR`/`xor-decrypt`/`xor decrypt` resolve to one
      sub-technique key; `base64` ≠ `base32`; layered-encoding labels resolve to a stable family.
- [x] 1.2a Alias-map conservatism guard: a regression test pins a list of must-stay-distinct
      technique pairs (e.g. `base64`/`base32`, `xor`/`rc4`, `sqli`/`ssti`, `ret2win`/`ret2libc`,
      `tcache poisoning`/`UAF`) and asserts `resolve_sub_technique` never folds any pinned pair
      to one key. Adding an over-broad alias that collapses a pinned pair MUST fail this test.
- [x] 1.3 Render the lane vocabulary into `prompts/research_prompt.md` from
      `technique_taxonomy.py` (injection point analogous to `{worked_example}`), so the
      prompt and the derivation share one definition.
- [x] 1.4 Make `category-tactics.md` a checked mirror of the code constants: either a
      generated lane section or a test asserting the doc lane list matches the
      `TechniqueFamily` enum, so the doc cannot silently drift from the authoritative code.

## 2. Research finding `technique_family` (weak enforcement)

- [ ] 2.1 Add nullable `technique_family` to `ResearchFinding` (`src/domain/research.py`)
      and to the research parser; unknown/missing values coerce to `other` with a
      logged warning, never raising.
- [ ] 2.2 Alembic revision: nullable `research_findings.technique_family` (text). No backfill.
- [ ] 2.3 Extend the research run report with a `technique_family` distribution and
      `other_ratio`; emit a neutral warning when `other_ratio > RESEARCH_FAMILY_OTHER_WARN_RATIO`
      (default `0.30`). Surface the distribution on the run report dashboard view.
- [ ] 2.4 Test: a run whose findings are 40% `other` produces the warning; a run with NULL
      `technique_family` findings still reports a derived distribution.

## 3. Greedy allocation: family governs, sub-technique diagnoses

- [ ] 3.1 Rewrite `_findings_for_task` (`design_task_planning_service.py`) as a single
      greedy pass: track `Counter[family]` and the set of sibling `sub_technique`s.
      **Family is the governance axis** — prefer candidates within the family quota +
      cooldown, relax (record `family_quota_exceeded`) when none fit.
      **Sub-technique is the diagnostic axis** — among the family-preferred candidates,
      additionally prefer an unused sub-technique (best-effort), record
      `subtechnique_duplicate` when none remain. Sub-technique has NO quota knob and NO
      separate fallback ladder. Final fallback is round-robin. Always preserve count.
- [ ] 3.2 Compute per-task `diversity_flags`
      (`{"family","sub_technique","warnings":[...]}`, warnings enum
      `family_quota_exceeded|subtechnique_duplicate|family_other`) during `_plan_candidates`
      and store on the candidate row.
- [ ] 3.3 Pass `avoid_techniques` (sibling sub-technique keys) into
      `HermesPlannerService.plan`; add the `SHOULD avoid reusing: {used}` clause to
      `prompts/design_planner_prompt.md`.
- [ ] 3.4 Add `technique_quota` / `cooldown_window` knobs (family-governance only) to
      `generation-profiles.json` and read them in the planner (sane defaults when absent).
      Do NOT add a sub-technique knob.
- [ ] 3.5 Tests: monocultural pool (all one sub-technique) still yields `target_count`
      tasks, each flagged `subtechnique_duplicate`; a diverse pool yields no warnings;
      same-family-different-sub-technique yields only `family_quota_exceeded`.
- [ ] 3.6 Determinism: allocation + `diversity_flags` are a pure function of (ordered
      findings, difficulty distribution, profile knobs); no randomness / wall-clock; ties
      broken by a stable key (finding index/id). Test: generating twice on identical inputs
      yields identical family/sub_technique per task_no and identical flags. Ensure the
      allocation decision is taken before/independently of any Hermes call so Hermes prose
      variation cannot change it.

## 4. `diversity_flags` persistence

- [ ] 4.1 Add `diversity_flags` (JSON) to `DesignTask` and the design_tasks schema; Alembic
      revision (nullable). Reconciler/serialization tolerate the new key.
- [ ] 4.2 Expose `diversity_flags` on the design-task API resource and request-detail view.

## 5. Plan-review checkpoint

- [ ] 5.1 Add nullable `plan_reviewed_at` (timestamptz) to `DesignTask` and schema; Alembic
      revision; legacy/in-flight drafts marked review-exempt to avoid backfill.
- [ ] 5.2 Add the `draft -> queued` guard in `design_task_validators.py`: NULL
      `plan_reviewed_at` (non-exempt) blocks queueing with a machine-readable reason
      (`plan_not_reviewed`).
- [ ] 5.3 Add `DesignTaskPlanningService.approve_plan(request_id)` (stamps
      `plan_reviewed_at` under the parent-row lock, idempotent — re-approve is a no-op /
      timestamp refresh) and `regenerate_plan(request_id)` (reuses
      `replace_draft_or_archived_tasks`, and SHALL clear `plan_reviewed_at` on regenerated
      rows so a stale approval can't leak an unreviewed plan into `queued`). Approve and
      both regenerate paths serialize under the same parent-request lock.
- [ ] 5.4 Add `DesignTaskPlanningService.regenerate_task(request_id, task_no)` returning a
      three-state outcome `regenerated | regenerated_with_warning | no_alternative`
      (with reason `research_diversity_insufficient | subtechnique_exhausted` for the last).
      Candidate set = slot hard constraints minus sibling sub-techniques, preferring within
      family quota/cooldown. `no_alternative` is a **true no-op** (no row replace, no
      timestamp churn). Family is soft — saturation yields `regenerated_with_warning` +
      `family_quota_exceeded`, never `no_alternative`. On a successful replace
      (`regenerated`/`regenerated_with_warning`) the regenerated task's `plan_reviewed_at`
      SHALL be cleared (approval is per draft version; siblings keep theirs). Same parent
      lock; reject if any sibling has left `draft`/`archived`. MUST NOT share a
      fill-forcing path with batch/regenerate-all.
- [ ] 5.5 Tests: unreviewed draft cannot be queued; approve then queue succeeds; clean pool
      → `regenerated`; sibling-avoiding but family-saturated → `regenerated_with_warning`
      + `family_quota_exceeded`; only sibling duplicates → `no_alternative` +
      `subtechnique_exhausted` (slot unchanged); no distinct finding → `no_alternative` +
      `research_diversity_insufficient`; regenerate blocked once any task is `queued`.
- [ ] 5.6 Concurrency + grandfather tests: concurrent approve + regenerate-all serialize and
      the regenerated rows end with NULL `plan_reviewed_at`; re-approving is idempotent;
      legacy `draft` (NULL `plan_reviewed_at`, pre-revision) queues review-exempt; legacy
      `design_task` with NULL `diversity_flags` reads/renders cleanly; legacy finding with
      NULL `technique_family` resolves via `resolve_family`.

## 6. Dashboard plan matrix

- [ ] 6.1 Add a plan-matrix view to the design-task dashboard (task_no / difficulty /
      family / sub_technique / scenario seed) reading `diversity_flags`; colour
      `family_quota_exceeded` (warn) vs `subtechnique_duplicate` (error) distinctly. The UI
      renders only server-computed `family`/`sub_technique`; it MUST NOT re-derive them from
      `label` client-side.
- [ ] 6.2 Wire approve / regenerate-all / regenerate-one buttons to the service-backed HTTP
      endpoints (no direct DB writes from the dashboard). Diversity warnings are display
      signals only — neither the buttons nor any backend check may gate queueing/approval on
      a warning; the sole queue precondition stays `plan_reviewed_at`.
- [ ] 6.3 Show the "research diversity insufficient — consider re-running research" hint when
      a regenerate-one action returns `no_alternative` with reason
      `research_diversity_insufficient` (authoritative trigger, not a warning-count
      threshold).

## 7. Docs

- [ ] 7.1 Note the new `technique_quota`/`cooldown_window` and
      `RESEARCH_FAMILY_OTHER_WARN_RATIO` knobs in `openspec/project.md`
      configuration section.
