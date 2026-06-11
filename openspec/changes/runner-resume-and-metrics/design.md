## Context

The runner currently treats a claimed shard as a fresh execution unit. If Hermes
is interrupted, the next worker claims the shard with a worker-specific filename
and reruns every challenge stage, even when prior stage events and artifacts are
already complete. Progress is append-only, but the code lacks public windowed
queries for resume decisions and duration metrics.

Validation is also split ambiguously: the prompt asks Hermes to execute
`validate.sh` and report validate progress, while the Python runner can invoke
`ChallengeValidator` behind a flag. That makes resume unsafe because
`validate/passed` can come from different producers with different evidence.

This design keeps the existing five conceptual stages and queue directories, but
makes the Python host responsible for resume planning, validate execution, final
status, and metrics.

## Goals / Non-Goals

**Goals:**

- Reuse completed work only when historical events and deterministic on-disk
  evidence agree.
- Normalize shard identity so retries by different workers share the same
  SQLite event stream.
- Make `challenge-factory run` validation mandatory and runner-owned.
- Preserve dry-run as a state-isolated prompt rendering path.
- Add explicit Hermes timeout selection and per-stage duration metrics without
  changing the SQLite schema.
- Relax Web prompt rules to support normal Apache/nginx port and root-master
  patterns without permitting root business processes.

**Non-Goals:**

- Do not introduce ConfigProposal YAML, interactive approval gates, stack
  template systems, Dockerfile lint/autofix, base-image pre-pull, or a Docker
  image migration system.
- Do not add stages or split validate into startup/exploit sub-stages.
- Do not migrate historical events that were recorded with worker-suffixed
  shard names; those runs will be invisible to normalized resume lookups.
- Do not add a dashboard duration column in this change; backend helper and CLI
  output are sufficient.

## Decisions

### Normalize shard names at claim time

`HermesRunner`'s per-shard processing entry (currently inline in the run loop;
the apply phase will locate the method by code rather than rely on a fixed
name) will compute `original_shard_name` exactly once after claim by calling
`ShardQueue.original_name(running_path)`. All state writes, resume queries,
report updates, metrics, and rendered `progress --shard` command strings will
use this original basename. `{shard_path}` in the prompt remains the actual
running path because Hermes must read the claimed file.

Alternative considered: derive the original name by stripping `.worker-*` from
the filename. That is more brittle than using the existing `.claim.json`
sidecar and fails if naming rules change.

### Use event windows plus evidence, not snapshots

`domain/resume.py` will depend on public StateStore read APIs:
`events_for_shard`, `events_for_challenge`, and `latest_claim_event`. A resume
plan is computed before the new run writes its own queued event. The relevant
historical window starts at the previous shard-level `queued/running` event and
ends before the current run boundary.

Snapshots are intentionally excluded from resume decisions because they are a
dashboard read model, can be reset, and only retain the latest state.

### Skip only a continuous verified prefix

Resume will evaluate stages in conceptual order: design, implement, build,
validate, document. For each stage, the latest event in the historical window
must be `passed`, and the stage-specific artifact evidence must be complete.
The first missing or non-passed stage stops the skip set; later historical
passes are ignored.

This favors recomputation over false success. It also explains why a failed
post-Hermes validate rerun starts from validate even if document had passed in
the earlier attempt: document is after validate in the conceptual prefix.

### Make validate runner-owned

The prompt will instruct Hermes to generate `validate.sh` and `solve/solve.py`
but not execute validation or write validate progress. After Hermes returns,
the runner checks prerequisite events and evidence for design, implement, build,
and document plus the validate files. If the gate passes and validate is not in
the skip prefix, the runner writes `validate/running`, calls
`ChallengeValidator.validate_challenge(challenge_id)`, then maps only
`status == "passed"` to `validate/passed`.

The event timestamp order for a fresh successful run becomes design, implement,
build, document, validate. This is intentionally different from the conceptual
stage order and is handled by resume/metrics rules rather than changing
`STAGES`.

### Keep dry-run read-only

`--dry-run` still claims a shard to get atomic read ownership and the correct
claim sidecar. It computes the resume plan and renders the prompt, but writes no
events, resets no snapshots, performs no all-skipped short-circuit, invokes no
Hermes or validator, and restores the shard to pending in a `finally` block.
`--dry-run --loop` is rejected by argparse to avoid repeatedly claiming the same
shard.

### Preserve append-only events and reset only snapshots

Before a non-dry-run writes the current queued event, the runner clears
snapshots for the original shard name. Events remain append-only. Snapshot
upserts will keep `percent = max(existing.percent, new.percent)` so the
document-before-validate timestamp order cannot make the dashboard progress bar
move backward within a run.

### Add duration metrics as query-time aggregation

`domain/metrics.duration_breakdown(challenge_id, shard)` will compute durations
from the latest normalized claim window. A stage duration is present only when
that window contains a first `running` event and the latest event for that stage
is `passed`; the value is `last_passed.created_at - first_running.created_at`.
Carry-forward skip events have no `running` event, so their duration is `None`.

### Isolate Docker image inspection

Build resume evidence for Web/Pwn needs to know whether `metadata.docker_image`
exists locally. A new `core.docker.image_exists(image)` helper wraps
`docker image inspect` with argv lists, `shell=False`, and a timeout, returning
`False` for empty input, missing Docker, timeouts, or non-zero inspect results.
`domain/resume.py` calls the helper but does not import `subprocess`.

## Risks / Trade-offs

- Resume can miss old work after shard-name normalization because legacy events
  used worker-suffixed shard names -> accept as a one-time migration cost and
  avoid adding a risky event rewrite.
- Resume can still be wrong if evidence checks are too weak -> require both the
  latest passed event and deterministic file/image/hash evidence for every
  skipped stage.
- The prompt becomes longer -> place "Resume Check" before execution stages and
  cover key literals in dry-run prompt tests.
- Existing Docker images can be stale when validate.sh skips build -> document
  `docker rmi` as the manual force-rebuild path and avoid adding hash-based
  image invalidation in this change.
- Duration values include retry and wait time inside the claim window -> this
  is the intended wall-clock stage duration and will be documented by tests.

## Migration Plan

1. Add StateStore query/reset APIs, snapshot monotonicity, and Docker helper
   tests first.
2. Add resume and metrics domain modules with unit tests using synthetic events
   and temporary challenge directories.
3. Update prompt rendering to accept `original_shard_name` and `resume_plan`,
   then update the prompt contract and dry-run tests.
4. Update runner flow for normalized shard names, mandatory validation,
   carry-forward events, all-skipped short-circuit, report merging, and timeout
   handling.
5. Remove `run --validate`, add `run --timeout`, and add `durations` CLI.
6. Run strict OpenSpec validation plus the full test suite.

Rollback is straightforward before archive: revert this change's code and
prompt edits. No SQLite schema migration is introduced.

## Open Questions

- None for v1. Dashboard display of duration metrics and finer validate
  startup/exploit timing are intentionally deferred.
