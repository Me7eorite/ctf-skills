# Implementation Inventory

This file records Task 0 findings before implementation. It is based on the live repo as of 2026-06-21 and should be reviewed before starting R1.

## 0.1 Failure Taxonomy Fixture

Current research `last_error` producers fall into these groups:

| Source | Current text shape | Proposed category |
| --- | --- | --- |
| `ResearchAgentExecutor.execute` | `Hermes exited with 124` | `timeout` |
| `ResearchAgentExecutor.execute` | `Hermes exited with <non-124>` | `runtime` |
| `ResearchJobService.claim_next_run` expired-run recovery fallback | `lease expired` | `lease_expired` |
| `ResearchAgentExecutor._resolve_profile_name` | `profile_not_bound` | `binding` |
| `ResearchAgentExecutor._resolve_profile_name` | `profile_disabled:<profile>` | `binding` |
| `ResearchAgentExecutor.execute` profile existence check | `Hermes profile '<profile>' does not exist` | `binding` |
| `ResearchAgentExecutor.execute` missing request guard | `generation_request <uuid> does not exist` | `runtime` |
| `_parse_research_output` terminal JSON extraction | `unparseable_output:no_terminal_json_object` | `parse_failure` |
| `apply_research_quality_gate` | `unparseable_output:sources_not_list` | `parse_failure` |
| `apply_research_quality_gate` | `unparseable_output:findings_not_list` | `parse_failure` |
| `apply_research_quality_gate` | `unparseable_output:source_not_object` | `parse_failure` |
| `apply_research_quality_gate` | `insufficient_findings:got=<n>,need=<n>` | `quality_gate` |
| `_parse_research_output` schema checks | `research output field 'sources' must be a list` | `field_validation` |
| `_parse_research_output` schema checks | `research output field 'findings' must be a list` | `field_validation` |
| `_normalize_source_payload` | `each source must be a JSON object` | `field_validation` |
| `_normalize_source_payload` | `source field '<name>' must be a non-empty string` | `field_validation` |
| `_normalize_source_payload` | `source raw_text must be a string when present` | `field_validation` |
| `_normalize_finding_payload` | `each finding must be a JSON object` | `field_validation` |
| `_normalize_finding_payload` | `finding field '<name>' must be a non-empty string` | `field_validation` |
| `_normalize_finding_payload` | `finding source_indices must be a list` | `field_validation` |
| `_normalize_finding_payload` | `finding source_indices must be non-empty` | `field_validation` |
| `_normalize_finding_payload` | `finding source_indices must contain integers, got <value>` | `field_validation` |
| `_normalize_finding_payload` / `_finding_source_ids` | `source index <n> is out of range` | `field_validation` |
| `_finding_source_ids` | `finding must include source_indices or source_ids` | `field_validation` |
| `_finding_source_ids` | `finding must include either source_ids or source_indices, not both` | `field_validation` |
| `ResearchWorker.cancel_run` path | `cancelled by operator` | `cancelled` |
| Any other non-empty string | original text | `unknown` |
| Empty / `None` | no diagnostic | `unknown` |

Non-research `last_error` producers exist in design/build code and should not be pulled into this taxonomy except where UI copy is shared intentionally.

## 0.2 Current Parse, Persistence, And Transaction Boundaries

`_parse_research_output` currently has filesystem side effects. It calls `extract_terminal_json_object`, applies the research quality gate, validates `sources[]` and `findings[]`, and writes any source `raw_text` into `paths.research_sources_staging / <run_id> / <index>.txt`. It also rewrites each payload with `raw_text_path` pointing at the final `paths.research_sources / <run_id> / <index>.txt`. Preview code must not call this function unchanged, because preview is required to be read-only.

`ResearchJobService._apply_run_completed(session, run, log_path)` is the single terminal-success state helper today. It sets `run.status='completed'`, `finished_at`, clears `last_error`, stores `hermes_log_path`, and sets the parent `generation_requests.status='researched'`. It does not commit and assumes its caller already proved ownership/eligibility.

`ResearchJobService.complete_run_with_staged_results(...)` is the existing staged-persistence boundary for normal worker completion. It opens a manual SQLAlchemy session, verifies the running claim with `_get_owned_running_run`, inserts sources/findings, touches the research binding, applies completed state, flushes, promotes staged files to final, then commits. If any exception happens before promotion it rolls back and removes staging. If an exception happens after promotion, it rolls back and removes final files. Backfill apply needs the same manual session/commit shape to compensate commit failures after file promotion.

`ResearchJobService._try_rescue_from_log(session, run, paths)` currently runs inside `claim_next_run`'s expired-row transaction. It reads `run.hermes_log_path` or `paths.research_logs / <run_id>.log` directly with UTF-8, extracts stdout markers, calls `_parse_research_output`, then starts a nested transaction/savepoint to insert sources/findings, applies completed state, flushes, promotes staging, and commits the savepoint. Any failure is swallowed so the caller can mark the expired run failed and possibly enqueue a retry. This path currently lacks the proposed safe-log path/type/size boundary and should be moved to the same helper as manual backfill.

`_extract_stdout_block(log_text)` is pure and only recognizes `--- stdout ---\n` followed by a later `\n--- end stdout ---`. The `recoverable` DTO flag should reuse the same ordered-marker semantics but must not parse JSON or run the quality gate.

The request/detail HTTP run serializer is currently centralized in `src/web/research_endpoints.py::_run_dict`. It has no `ProjectPaths` parameter today, so derived `recoverable` must be wired by explicitly passing app paths from endpoint call sites instead of discovering paths inside the serializer.
