## ADDED Requirements

### Requirement: Backfill preview is a pure read over a safe Hermes log

The system SHALL provide `ResearchBackfillService.preview(run_id)`. It SHALL read the run's log through one safe-log helper: the resolved path MUST be a regular file beneath `paths.research_logs`, MUST NOT escape through a symbolic link, MUST be at most 10 MiB, and MUST decode as UTF-8. It SHALL extract the stdout block and run the same pure parsing, normalization, and quality-gate logic as normal completion. Preview MUST NOT write to the database or filesystem.

#### Scenario: Preview reports projected state without mutation

- **WHEN** preview is called for an eligible failed run with a valid payload
- **THEN** it returns `would_insert_sources`, `would_insert_findings`, `current_run_status`,
  `would_run_status`, `current_request_status`, `would_request_status`, and lowercase hexadecimal
  `log_sha256`
- **AND** the database and filesystem trees are unchanged

#### Scenario: Preview does not materialize raw text

- **WHEN** the parsed payload contains source `raw_text`
- **THEN** preview validates it and reports counts without creating a staging or final source file

### Requirement: Backfill has stable eligibility and error semantics

The service and endpoint SHALL use these stable codes/statuses: `run_not_found` (404); `already_completed`, `run_not_terminal`, `superseded_run`, `active_sibling_run`, `already_has_results`, and `preview_stale` (409); `no_log_file`, `unsafe_log_path`, `log_too_large`, `log_unreadable`, `parse_failed`, and `quality_gate_failed` (422). Every HTTP error body MUST be the top-level JSON object `{"code": <code>, "detail": <string>}`.

#### Scenario: Missing or completed run is rejected

- **WHEN** the run does not exist or is already completed
- **THEN** the endpoint returns `run_not_found` 404 or `already_completed` 409 respectively

#### Scenario: Running or queued run is protected by claim ownership

- **WHEN** backfill is attempted against a running or queued run
- **THEN** the endpoint returns `run_not_terminal` 409
- **AND** no claim or result state changes

#### Scenario: Superseded failure is rejected

- **WHEN** a failed run has a higher-attempt sibling or any completed sibling
- **THEN** the endpoint returns `superseded_run` 409 without mutation

#### Scenario: Active retry blocks backfill

- **WHEN** a failed run has another queued or running sibling
- **THEN** the endpoint returns `active_sibling_run` 409 without mutation

#### Scenario: Existing partial results block backfill

- **WHEN** the run already has at least one source or finding
- **THEN** the endpoint returns `already_has_results` 409 without mutation

#### Scenario: Unsafe or excessive log is rejected

- **WHEN** the path is missing, escapes the allowed root, names a non-regular file, exceeds
  10 MiB, cannot be read, or is not valid UTF-8
- **THEN** the endpoint returns the matching 422 code from `no_log_file`, `unsafe_log_path`,
  `log_too_large`, or `log_unreadable`
- **AND** no content outside the allowed log root is returned

#### Scenario: Parse and quality failures remain distinguishable

- **WHEN** stdout markers/JSON are invalid or the parsed payload fails the existing quality gate
- **THEN** the endpoint returns `parse_failed` 422 or `quality_gate_failed` 422 respectively
- **AND** its `detail` contains the underlying validation reason

### Requirement: Apply is bound to the previewed log bytes

The system SHALL provide `ResearchBackfillService.apply(run_id, expected_log_sha256)`. Preview SHALL compute SHA-256 over the exact validated log bytes. Apply SHALL require that lowercase 64-hex digest, re-read the safe log while holding the apply lock, and compare before any materialization or write. A missing/malformed digest SHALL fail request validation with HTTP 422; a mismatch SHALL return `preview_stale` 409.

#### Scenario: Log changes after preview

- **WHEN** preview succeeds and the log bytes change before apply
- **THEN** apply returns `preview_stale` 409
- **AND** no database rows or source artifacts are written

### Requirement: Apply serializes eligibility and persistence

Apply SHALL open one transaction, acquire a transaction-scoped PostgreSQL advisory lock whose signed 64-bit key is deterministically derived from the UUID bytes (Python `hash()` MUST NOT be used), then re-read the run with `SELECT ... FOR UPDATE` before eligibility checks. The lock MUST cover digest verification, persistence, promotion, and commit.

#### Scenario: Concurrent apply produces one result set

- **WHEN** two apply calls use the same valid preview digest for one eligible run
- **THEN** exactly one persists and completes the run
- **AND** the other returns `already_completed` or `already_has_results`
- **AND** no duplicate source or finding exists

### Requirement: Apply preserves DB and filesystem compensation semantics

After pure parsing, apply SHALL materialize raw-text files under the existing per-run staging directory, persist rows and completed state through shared `_persist_rescue_payload`, flush, promote staging to final, and commit. The shared helper MUST NOT open a transaction, acquire a lock, promote files, or commit. Parse/persist/flush/promotion failure SHALL roll back and remove staging. Commit failure after promotion SHALL roll back and remove final files. Existing startup reconciliation remains the crash-recovery mechanism for process death between promotion and commit.

#### Scenario: Apply succeeds atomically at the service boundary

- **WHEN** apply receives a matching digest and valid payload
- **THEN** sources/findings are inserted, the run becomes completed, and source files are promoted
- **AND** the parent request becomes researched in the same DB transaction

#### Scenario: Commit failure compensates promoted files

- **WHEN** source files are promoted and the subsequent DB commit raises
- **THEN** the DB transaction is rolled back, final files are removed, and the run remains failed
  with no results

### Requirement: Successful backfill promotes the only consumable result set

Apply SHALL only accept a failed run that is the request's highest attempt and has no other queued, running, or completed sibling. On success it SHALL set the run to completed, set `finished_at`, clear `last_error`, retain the validated `hermes_log_path`, and set the parent request to researched. Queue API methods SHALL remain unable to mutate the completed row.

#### Scenario: Latest failed run is restored

- **GIVEN** the latest run is failed, has no results or conflicting sibling, and its log passes all checks
- **WHEN** apply is called with the preview digest
- **THEN** run and request reach completed/researched and `last_error` is null

### Requirement: HTTP request shape distinguishes preview and confirmed apply

`POST /api/research/runs/{run_id}/backfill` SHALL accept a JSON object that requires boolean `apply` and rejects unknown fields. For `apply=false`, `expected_log_sha256` MUST be absent. For `apply=true`, it MUST be present and valid. The response SHALL use the preview/result shape or the stable top-level error shape defined above.

#### Scenario: Malformed apply request is rejected before service execution

- **WHEN** apply is missing, an unknown field is present, or confirmed apply omits its digest
- **THEN** FastAPI returns request validation status 422
- **AND** the service is not called

### Requirement: UI previews and explicitly confirms candidate recovery

The dashboard SHALL show "尝试从日志恢复结果" exactly when a failed run's `recoverable` candidate flag is true. It SHALL explain that candidacy is not a success guarantee, preview first, display projected counts/statuses, and send the preview digest only after explicit confirmation. Preview error disables confirmation. Successful apply shows a toast and refreshes detail.

#### Scenario: Operator confirms the exact preview

- **WHEN** preview succeeds and the operator confirms
- **THEN** the UI sends `{"apply":true,"expected_log_sha256":"<preview digest>"}`

#### Scenario: Stale preview requires another review

- **WHEN** apply returns `preview_stale`
- **THEN** the dialog does not retry automatically and asks the operator to preview again

### Requirement: CLI supports single audit and independent batch recovery

The system SHALL provide `challenge-factory research backfill --run-id <UUID> --dry-run` and `challenge-factory research backfill --all-recoverable --apply`. `--run-id` SHALL accept exactly one UUID. Dry-run MUST print preview without DB/filesystem mutation. Batch apply MUST enumerate failed safe-log candidates, preview each, pass its digest to apply, process each run in its own transaction, continue after per-run failures, and print a final recovered/skipped summary. Single-run apply MUST be rejected at CLI parse time.

#### Scenario: Batch is independently committable and resumable

- **WHEN** three candidates contain two valid logs and one quality failure
- **THEN** two independent transactions commit, the failure remains untouched, and output reports
  each outcome
- **AND** the overall exit code is non-zero because at least one candidate failed

#### Scenario: Single-run apply is unavailable

- **WHEN** the operator combines `--run-id` with `--apply`
- **THEN** argparse rejects the invocation with a non-zero exit code
