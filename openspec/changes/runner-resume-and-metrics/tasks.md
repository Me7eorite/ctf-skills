## 1. State and infrastructure foundations

- [ ] 1.1 Add StateStore public query APIs: `events_for_shard`, `events_for_challenge`, and `latest_claim_event` with id boundary tests.
- [ ] 1.2 Change `StateStore.record()` to return the inserted event id while preserving existing return fields and callers.
- [ ] 1.3 Add `StateStore.reset_snapshots(shard)` and snapshot upsert monotonic percent behavior.
- [ ] 1.4 Add `core.docker.image_exists(image)` with argv-based `docker image inspect`, timeout handling, and tests for missing/invalid Docker cases.
- [ ] 1.5 Extend dependency-direction tests so `domain.resume` and `domain.metrics` do not import `subprocess`, and `core.docker` imports no upper-layer package.

## 2. Resume and metrics domain logic

- [ ] 2.1 Add `domain.resume` to compute historical claim windows from original shard names before current queued events are written.
- [ ] 2.2 Implement per-stage evidence checks for design, implement, build, validate, and document, including Docker image and Reverse artifact SHA-256 checks.
- [ ] 2.3 Implement continuous-prefix resume planning with historical source event ids and per-challenge first-pending-stage output.
- [ ] 2.4 Add tests for missing evidence, latest-event-wins behavior, continuous-prefix stopping, build evidence, document evidence, validate read-only evidence, and duplicate challenge directories.
- [ ] 2.5 Add `domain.metrics.duration_breakdown` for latest claim-window durations and tests for missing stages, old windows, carry-forward-only stages, and validate duration.

## 3. Prompt protocol updates

- [ ] 3.1 Update `hermes.prompt.render_prompt` and runner call sites to pass `original_shard_name` and structured `resume_plan`.
- [ ] 3.2 Render `progress_command` with the original shard basename while leaving `shard_path` as the running claimed path.
- [ ] 3.3 Update `prompts/shard_prompt.md` with the Resume Check section, relaxed Web port/root-master rules, and validate.sh image inspect fallback.
- [ ] 3.4 Remove prompt instructions that require Hermes to execute `validate.sh` or write validate progress, while preserving progress writes for design, implement, build, and document.
- [ ] 3.5 Add dry-run prompt tests for resume plan injection, original shard progress command, Apache/nginx rules, validate.sh image inspect literal, and absence of Hermes-owned validate progress requirements.

## 4. Runner resume flow

- [ ] 4.1 Normalize shard identity once after claim using `ShardQueue.original_name(running_path)` and use it for all state, report, resume, metrics, and prompt operations.
- [ ] 4.2 Implement dry-run state isolation: claim, compute plan, render prompt, restore to pending in `finally`, write no events, and reject `--dry-run --loop`.
- [ ] 4.3 For non-dry-run, compute resume plan, reset snapshots, write current queued/running, write carry-forward passed events, and write only the first pending stage per challenge.
- [ ] 4.4 Implement all-skipped short-circuit with carry-forward events, per-challenge complete/passed, shard-level complete/passed, done queue transition, and report creation/update.
- [ ] 4.5 Update timeout recovery to re-run evidence checks, avoid synthetic stage passes, and continue into mandatory validation only when prerequisites are complete.
- [ ] 4.6 Add runner tests for pending writes (including the validate-first un-skipped-stage case writing `validate/pending`), carry-forward ordering, carry-forward message `carry-forward:` prefix discipline with source historical event id, validator-written `validate/*` events using the `validator:` prefix, machine-distinguishable carry-forward versus validator messages, all-skipped short-circuit, original shard keys, dry-run isolation, timeout recovery, and event timestamp ordering.

## 5. Validation and reporting

- [ ] 5.1 Add `ChallengeValidator.validate_challenge(challenge_id)` with exact directory matching and safe failure statuses for missing or ambiguous challenge ids.
- [ ] 5.2 Refactor batch validation to reuse the single-challenge implementation or the same underlying helper.
- [ ] 5.3 Make non-dry-run `HermesRunner` perform prerequisite validation gates, write validate/running, call `validate_challenge`, and map only `status == "passed"` to validate/passed.
- [ ] 5.4 Ensure skipped validate stages do not invoke `ChallengeValidator`.
- [ ] 5.5 Merge per-challenge validation results into shard reports, repairing missing or malformed report structures.
- [ ] 5.6 Add tests for validate gate failures, validate passed/failed mapping, missing/ambiguous challenge ids, skipped validate, malformed reports, mixed-result shards, and failed validate final status.

## 6. CLI behavior

- [ ] 6.1 Remove `challenge-factory run --validate` and related runner parameters while keeping the standalone `challenge-factory validate` command.
- [ ] 6.2 Add `challenge-factory run --timeout INT`, default `DEFAULT_HERMES_TIMEOUT = 1500`, environment fallback, positive integer validation, and first-line effective-timeout output.
- [ ] 6.3 Add `challenge-factory durations --challenge <id> --shard <original>.json` with shard basename validation and JSON output.
- [ ] 6.4 Add CLI tests for help output, timeout precedence and invalid values, dry-run timeout output before rendering, and durations shard input validation.

## 7. Verification

- [ ] 7.1 Run `uv run ruff check`.
- [ ] 7.2 Run `uv run mypy`.
- [ ] 7.3 Run `uv run pytest tests/`.
- [ ] 7.4 Run `openspec validate runner-resume-and-metrics --strict`.
