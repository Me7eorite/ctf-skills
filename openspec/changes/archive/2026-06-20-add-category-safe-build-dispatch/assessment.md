## Ten-round assessment

### Round 1 — OpenSpec structure

**Finding:** Strict validation failed because the first modified requirement's
parsed normative paragraph contained no recognized `SHALL` or `MUST`.

**Remediation:** Rewrote the normative opening and reran strict validation.

### Round 2 — Delta replacement semantics

**Finding:** The change marked `BuildOrchestrationService submits and retries
builds` as modified while restating only dispatch behavior. Applying that delta
would replace and discard the base submission, retry, staging, and recovery
contract.

**Remediation:** Moved dispatch into a separately named added requirement. The
UI requirement, which genuinely changes, now restates its complete base
contract before changing worker actions.

### Round 3 — DB row and shard identity

**Finding:** Checking only top-level `build_attempt_id` could launch a duplicate
or mismatched file and did not bind the pending file to the persisted
`shard_basename` and `design_task_id`.

**Remediation:** DB-backed starts now require the exact persisted basename and
payload agreement on build-attempt id, design-task id, and category.

### Round 4 — Exact-attempt category safety

**Finding:** The single-attempt endpoint proposed only an attempt-id filter, so
a malformed payload with that id but a different challenge category could pass
queue selection.

**Remediation:** Both category and detail starts resolve the authoritative
design-task category and launch with combined attempt-id and category filters.

### Round 5 — Attribution definition

**Finding:** `require_build_attempt=True` treated any non-empty string as
attribution, allowing junk ids to enter build-attempt-only execution.

**Remediation:** Attributed candidates now require valid UUID values for both
`build_attempt_id` and `design_task_id`; exact ids are compared after UUID
normalization.

### Round 6 — Local task race and launch contract

**Finding:** A separate busy precheck and process start would race inside one
dashboard process, and response/loop behavior was unspecified.

**Remediation:** The final busy check and subprocess creation are one atomic
`TaskManager` operation. Exact-attempt starts do not use `--loop`; success is
`202` with the selected id, while eligibility/busy conflicts are `409`.

### Round 7 — CLI option matrix

**Finding:** The proposal did not define `--category` with `--build-attempt`, or
the redundant `--build-attempts-only` with `--build-attempt` combination.

**Remediation:** Category plus exact attempt is explicitly supported with
AND semantics. `--build-attempts-only` requires category and is incompatible
with exact-attempt mode. Invalid combinations exit 2 before queue access.

### Round 8 — Malformed and special filesystem candidates

**Finding:** Constrained parsing behavior did not define valid UUIDs,
non-regular files, or symbolic links, leaving an avoidable path/payload safety
gap.

**Remediation:** Constrained mode skips malformed JSON, invalid attribution,
non-regular files, and symlinks without mutation. The legacy unconstrained
path remains unchanged for compatibility.

### Round 9 — Concurrent change compatibility

**Finding:** `add-agent-worker-pool-management` replaces the launch substrate
and touches the same capabilities/endpoints, but the sequencing obligation was
only implicit.

**Remediation:** The proposal now identifies this change as the prerequisite
dispatch-authorization layer and requires the worker-pool implementation to be
rebased on it. The remaining cross-process duplicate-start limitation is
documented and bounded by exact-attempt atomic file claim until leases arrive.

### Round 10 — Legacy endpoint versus dashboard ownership

**Finding:** The base UI contract says build actions are reachable only from
the Build Attempts view, while an earlier draft allowed a legacy global worker
control on another shard-management surface.

**Remediation:** Keep `POST /api/actions/worker` unchanged for explicit API
compatibility, but do not add another dashboard control for it. The final
cross-file recheck found no remaining contradiction across proposal, design,
delta specs, tasks, current queue/runner interfaces, staging recovery,
reconciler ownership, dashboard task guarding, and the dependent worker-pool
change.

**Result:** The proposal is ready for implementation. Strict OpenSpec
validation passes after the remediations above.

## Follow-up independent assessment

**Finding:** `HTTP API exposes build orchestration` was declared under
`ADDED Requirements` even though the base `build-orchestration` spec already
contains that exact requirement name. OpenSpec strict validation passed but did
not detect the duplicate, so archiving could create two requirements with the
same identity.

**Remediation:** Renamed the added delta to the unique and narrower
`HTTP API exposes constrained build-worker starts`. Also clarified that every
constraint, including attribution-only mode, triggers payload inspection, and
added an explicit duplicate-name verification task.

**Result:** No remaining duplicate ADDED requirement names or internal
contract contradictions were found after the follow-up recheck.
