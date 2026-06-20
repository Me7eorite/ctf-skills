# Research Logic Assessment - 2026-06-20

Tracked by openspec change `tighten-research-evaluation-flow`.

Scope: current research planning/execution implementation only. This is an
analysis record for a possible future OpenSpec proposal. No implementation
changes are included.

## Review Rounds

1. Memory/context check: confirmed prior project guidance that live code is the
   source of truth and worker/queue assumptions must be checked against current
   implementation.
2. Domain DTO and validators: reviewed `src/domain/research.py` and
   `src/domain/research_validators.py`.
3. Service state machine: reviewed `ResearchJobService` submit, claim,
   heartbeat, complete, fail, retry, and lease recovery paths.
4. Executor flow: reviewed `ResearchAgentExecutor` profile resolution,
   heartbeat loop, Hermes invocation, stdout parsing, stale-claim handling, and
   result persistence.
5. Worker and process manager: reviewed CLI worker loop and dashboard
   subprocess manager behavior.
6. Repository and schema: reviewed SQLAlchemy models, migration 0002, lookup
   tables, indexes, FK/check constraints, and repository queries.
7. API layer: reviewed request/runs/list/detail/submit/worker/log endpoints and
   display-status behavior.
8. OpenSpec alignment: compared current code against
   `openspec/specs/research-planning/spec.md`.
9. Test coverage inspection: reviewed unit and Postgres-marked tests for
   research domain, repository, lease, queue concurrency, executor, worker, API,
   and prompt rendering.
10. Runtime verification: ran focused non-Postgres tests:
   `uv run pytest tests/app/test_research_services_unit.py tests/app/test_research_api.py tests/app/test_hermes_research.py tests/app/test_research_prompt.py -q --basetemp .pytest-basetemp/research-assessment`
   Result: 48 passed, 1 warning.
11. Boundary pass: specifically checked subprocess environment inheritance,
   request-scoped worker validation, output parsing strictness, filesystem
   side effects, and UI display-state filtering.
12. Follow-up product pass: recorded two operator-observed gaps for future
   proposal scope: repeated topics likely generate repeated challenge focus
   points, and the research/design-task pages have UI/UX inconsistencies that
   should be corrected separately from backend state-machine fixes.

## Findings

### 1. Submit status contract is internally inconsistent

Evidence:

- Spec says `submit_request` should return a generation request with
  `status="researching"` and persist `generation_requests.status` as
  `researching` after queuing the first run.
- Implementation creates the request with `status="draft"` and returns it
  before any claim happens.
- API response separately returns `"status": "queued"`, while list/detail
  display logic maps a latest queued run back to `"draft"`.

Impact:

The system now has at least three meanings for the same phase: persisted
request status `draft`, display status `draft`, and submit response status
`queued`. This weakens downstream API/CLI semantics and makes status filters
ambiguous.

OpenSpec candidate:

- Define one canonical persisted submit state.
- Define whether queued research is displayed as `draft`, `queued`, or
  `researching`.
- Require CLI, API response, list filtering, detail response, and design-task
  eligibility to use the same state vocabulary or explicitly separated
  `stored_status`/`display_status` fields.

### 2. Hermes Research subprocess inherits `DATABASE_URL` despite the spec

Evidence:

- Spec says the Hermes Research Agent subprocess environment must not include
  `DATABASE_URL`.
- `src/hermes/research.py` builds `environment_map = os.environ.copy()` and
  passes it directly to `invoke_capture`.

Impact:

The research agent process may receive persistence credentials. That violates
the intended boundary that Hermes does not touch the database and increases the
blast radius of prompt/tool execution.

OpenSpec candidate:

- Require explicit environment allowlist or explicit deletion of persistence
  secrets before invoking Hermes research.
- Add tests proving `DATABASE_URL` and similar DB variables are absent while
  required Hermes/HERMES_HOME/custom-provider variables still work.

### 3. Output "evaluation" is mostly structural parsing, not research quality evaluation

Evidence:

- Parser requires JSON object with `sources` and `findings`, required string
  fields, and valid `source_indices`.
- It does not verify `content_hash` shape as lower-case sha256, URL shape,
  duplicate sources, duplicate findings, finding/category relevance, source
  credibility, or whether `target_count` and `difficulty_distribution` are
  sufficiently represented.
- Prompt tells the agent not to fabricate sources, but the implementation has
  no independent evaluation step.

Impact:

A syntactically valid but low-quality or off-category research result can be
persisted as `completed`, then feed design-task generation.

OpenSpec candidate:

- Add a research result evaluation gate with explicit pass/fail diagnostics.
- Validate URL/content_hash shape and optional source de-duplication.
- Define minimum finding coverage against target count/difficulty distribution.
- Define category-fit checks and whether failures retry or enter a separate
  `needs_review` state.

### 4. Raw text filesystem writes happen before DB transaction and are not rolled back

Evidence:

- `_normalize_source_payload` writes `work/research/sources/<run_id>/<index>.txt`
  during parsing before `complete_run_with_results`.
- If DB validation/persistence later fails, the transaction rolls back, but the
  raw text file remains.

Impact:

Failed or rejected runs can leave orphan source artifacts. This conflicts with
the otherwise strong DB atomicity story and makes deletion/retry cleanup harder.

OpenSpec candidate:

- Define artifact staging and commit semantics for research source raw text.
- Require rollback cleanup or a reconciler/deletion rule for orphan files.
- Require tests for parse-success/persist-failure leaving no committed source
  artifacts.

### 5. Request-scoped worker start accepts any UUID without checking queue/request existence

Evidence:

- `/api/research/requests/{request_id}/worker/start` validates UUID syntax and
  passes the string to the manager.
- The manager starts a subprocess with `--generation-request-id <uuid>`.
- If that request does not exist or has no queued runs, the worker can start and
  exit cleanly with an empty queue after process startup.

Impact:

The dashboard can report "started" for a request that will never be processed.
This is especially confusing for per-request action buttons.

OpenSpec candidate:

- Require request existence and queued/runnable run validation before spawning
  a scoped worker.
- Define response codes for nonexistent request, already researched/failed
  request, and request with no queued retry.

### 6. Dashboard worker startup health check is a 0.2s race

Evidence:

- `ResearchWorkerManager.start` sleeps 0.2 seconds, checks `process.poll()`,
  and returns started if the subprocess has not exited yet.

Impact:

Slow startup failures, DB connection failures after initial import, or Hermes
profile checks can still be reported as successful worker starts. The only
later signal is log/status polling.

OpenSpec candidate:

- Add a startup handshake or first-event readiness contract.
- Distinguish "process spawned" from "worker claimed/processed/empty/failed".

### 7. Lost-lease cancellation can leave the original row running until another claim recovers it

Evidence:

- Executor discards output and returns when `lost_lease` or Hermes cancellation
  is observed.
- Tests intentionally accept the row remaining `running` after claim mutation.

Impact:

This is recoverable by later lazy lease recovery, but it means the original
executor does not record a terminal explanation when cancellation happens. If no
later worker calls `claim_next_run`, the row remains running until observed.

OpenSpec candidate:

- Decide whether cancelled-after-lease-loss should remain purely lazy-recovered
  or have an explicit `abandoned/cancelled` terminal path guarded by fencing.
- Require queue stats to make such rows visible.

### 8. API status filtering uses display status, not persisted status

Evidence:

- `/api/research/requests?status=` validates against persisted
  `GenerationRequestStatus`.
- It fetches requests, builds rows with display status, then filters on
  `row["status"]`.
- Tests assert that stored `researching` plus latest queued run appears under
  `status=draft`.

Impact:

An API caller asking for `status=researching` will miss queued research
requests. This is defensible only if the API contract explicitly says the
parameter filters display status, not persisted lifecycle state.

OpenSpec candidate:

- Split query params into `stored_status` and `display_status`, or change the
  filter to persisted state and expose UI-only mapping separately.

### 9. Web submit drops runtime constraints

Evidence:

- `ResearchJobService.submit_request` supports `runtime_constraints`.
- HTTP submit extracts category/topic/count/distribution/seed URLs/max attempts
  but does not read or pass `runtime_constraints`.

Impact:

CLI/API parity is incomplete for operator intent. Any runtime requirement
submitted through the dashboard cannot reach prompt rendering.

OpenSpec candidate:

- Require dashboard/API submit to accept and validate `runtime_constraints`.
- Add shape constraints for allowed runtime keys if free-form JSON is too loose.

### 10. Research result count is not tied to requested target count

Evidence:

- Prompt includes target count and difficulty distribution.
- Parser and service accept any number of findings, including fewer than target
  count. Structurally, `findings=[]` passes `_parse_research_output`; DB
  completion also permits no findings if called with an empty findings list.

Impact:

A run can complete with no actionable research material, after which design
task generation may fail later or generate too little work.

OpenSpec candidate:

- Define minimum result thresholds for completion.
- Decide whether no-findings is `failed`, `needs_review`, or a successful but
  empty research result.

### 11. Queue stats near-expiry boundary is stricter than wording usually implies

Evidence:

- Repository uses `lease_expires_at < now() + interval '60 seconds'`.
- Test asserts exact 60-second boundary is excluded.

Impact:

This is likely acceptable, but the contract should say "less than 60 seconds"
instead of "within/near 60 seconds" if exact-boundary exclusion is intended.

OpenSpec candidate:

- Clarify near-expiry inclusion/exclusion at the 60-second boundary.

### 12. Research source de-duplication is implied but not enforced

Evidence:

- `content_hash` is stored and indexed with `(research_run_id, content_hash)`.
- There is no unique constraint or repository-level rejection for duplicate
  content hashes in a run.

Impact:

The same source body can be persisted multiple times. This may inflate evidence
counts and distort later planning.

OpenSpec candidate:

- Decide whether duplicate content_hash values per run are allowed, collapsed,
  or rejected.

### 13. Repeated topics can repeatedly generate the same challenge focus points

Evidence:

- Generation requests are independent rows keyed by category/topic/count and do
  not include a cross-request novelty or similarity check.
- The research prompt uses the current request topic as the main scope. If two
  requests use the same broad topic, for example `SQL`, the agent is likely to
  rediscover the same common techniques and scenarios.
- The current parser and persistence layer validate source/finding structure
  within one run, but do not compare new findings or later design tasks against
  prior requests, prior completed runs, or existing challenge designs.

Impact:

Two separate operator requests with the same topic can produce highly similar
research findings and then highly similar design tasks. This risks duplicate
challenge concepts, repeated learning objectives, repeated vulnerability
families, and wasted design/build capacity.

OpenSpec candidate:

- Add a novelty policy for research and design-task generation.
- Compare candidate findings/design tasks against prior completed research
  runs and existing design tasks in the same category/topic family.
- Store or derive comparable fields such as normalized topic, primary
  technique, learning objective, scenario fingerprint, and evidence/finding
  fingerprint.
- Define whether duplicates are rejected, merged, marked for manual review, or
  allowed only when the operator explicitly requests variants.

### 14. Research/design-task pages need UI cleanup and Chinese-facing copy

Evidence:

- Operator feedback indicates some page behavior looks strange, including at
  least one odd design-task presentation or workflow state.
- The current analysis focused mainly on backend research flow, but the UI is
  part of the research-to-design workflow and can currently expose confusing
  status/display semantics.
- The dashboard is allowed to use Chinese copy; future UI work should not be
  constrained to English labels if Chinese improves operator clarity.

Impact:

Even if backend state transitions are correct, unclear UI states can cause
operators to start the wrong worker, misread queued/researched/failed states,
or misunderstand why a design task exists. A strange design-task row or action
state is especially risky because design tasks are the bridge from research
findings to actual challenge generation.

OpenSpec candidate:

- Add a UI review/remediation section for research request, research run,
  research logs, and design-task pages.
- Permit and standardize Chinese UI copy for operator-facing labels, empty
  states, errors, confirmations, and action buttons.
- Define expected design-task row states, actions, summaries, and abnormal-state
  rendering.
- Include screenshot/manual QA acceptance criteria so the page is verified, not
  only API-tested.

## Existing Strengths To Preserve

- Claim, heartbeat, terminal writes, and completion are token-fenced.
- Expired lease recovery shares the same failure/retry logic as explicit
  failure.
- Repository rejects cross-run finding source references.
- Executor catches stale-claim writes and avoids stale result persistence.
- Hermes code does not import persistence modules.
- Non-Postgres unit/API/prompt tests passed during this assessment.

## Suggested OpenSpec Scope

Potential change name: `tighten-research-evaluation-flow`.

Candidate requirements:

1. Canonical research request state contract.
2. Research subprocess environment isolation.
3. Research result evaluation gate.
4. Research artifact staging/rollback semantics.
5. Request-scoped worker preflight and startup readiness.
6. API status filter semantics.
7. Runtime constraints parity for web submit.
8. Minimum result coverage and duplicate-source policy.
9. Cross-request novelty checks for repeated topics and repeated challenge
   focus points.
10. Research/design-task UI cleanup with Chinese-facing operator copy.
