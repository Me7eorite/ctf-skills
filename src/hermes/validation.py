"""Validation orchestration for generated challenges."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from core.paths import ProjectPaths
from core.state import StateStore
from domain.resume import (
    ChallengeResumePlan,
    build_evidence,
    design_evidence,
    document_evidence,
    implement_evidence,
    validator_message,
)
from domain.validation import ChallengeValidator


def run_validation(
    *,
    state: StateStore,
    validator: ChallengeValidator,
    paths: ProjectPaths,
    image_exists: Callable[[str], bool],
    original_shard_name: str,
    worker: str,
    challenge_ids: list[str],
    plan_by_id: dict[str, ChallengeResumePlan],
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for challenge_id in challenge_ids:
        plan = plan_by_id.get(challenge_id)
        if plan is not None and "validate" in plan.skipped_stages:
            results.append(
                {
                    "challenge_id": challenge_id,
                    "solve_status": "passed",
                    "validation_status": "skipped_resume",
                }
            )
            continue

        gate_error = validate_gate(challenge_id, plan, paths, image_exists)
        if gate_error is not None:
            state.record(
                shard=original_shard_name,
                challenge_id=challenge_id,
                worker=worker,
                stage="validate",
                status="failed",
                message=validator_message(status="contract_failed", error=gate_error),
            )
            results.append(
                {
                    "challenge_id": challenge_id,
                    "solve_status": "failed",
                    "validation_status": "contract_failed",
                    "validation_error": gate_error,
                }
            )
            continue

        state.record(
            shard=original_shard_name,
            challenge_id=challenge_id,
            worker=worker,
            stage="validate",
            status="running",
            message=validator_message(status="running"),
        )
        outcome = validator.validate_challenge(challenge_id)
        elapsed = outcome.get("elapsed")
        if outcome.get("status") == "passed":
            state.record(
                shard=original_shard_name,
                challenge_id=challenge_id,
                worker=worker,
                stage="validate",
                status="passed",
                message=validator_message(
                    status="passed", elapsed=elapsed, flag_matched=True
                ),
            )
            results.append(
                {
                    "challenge_id": challenge_id,
                    "solve_status": "passed",
                    "validation_status": "passed",
                    "validation_elapsed": elapsed,
                }
            )
        else:
            status = str(outcome.get("status", "failed"))
            error = outcome.get("error")
            state.record(
                shard=original_shard_name,
                challenge_id=challenge_id,
                worker=worker,
                stage="validate",
                status="failed",
                message=validator_message(status=status, elapsed=elapsed, error=error),
            )
            results.append(
                {
                    "challenge_id": challenge_id,
                    "solve_status": "failed",
                    "validation_status": status,
                    "validation_elapsed": elapsed,
                    "validation_error": error,
                }
            )
    return results


def validate_gate(
    challenge_id: str,
    plan: ChallengeResumePlan | None,
    paths: ProjectPaths,
    image_exists: Callable[[str], bool],
) -> str | None:
    if plan is None:
        return "no resume plan entry"
    if plan.directory is None:
        return plan.lookup_status
    category = _category_of(plan.directory, paths)
    if not design_evidence(plan.directory, challenge_id):
        return "design evidence incomplete"
    if not implement_evidence(plan.directory, category):
        return "implement evidence incomplete"
    if not build_evidence(plan.directory, category, image_exists):
        return "build evidence incomplete"
    if not document_evidence(plan.directory):
        return "document evidence incomplete"
    if not (plan.directory / "validate.sh").is_file():
        return "validate.sh missing"
    if not (plan.directory / "writenup" / "exp.py").is_file():
        return "writenup/exp.py missing"
    return None


def record_per_challenge_complete(
    state: StateStore,
    original_shard_name: str,
    worker: str,
    per_results: list[dict[str, Any]],
) -> None:
    for result in per_results:
        status = "passed" if result.get("solve_status") == "passed" else "failed"
        state.record(
            shard=original_shard_name,
            challenge_id=result["challenge_id"],
            worker=worker,
            stage="complete",
            status=status,
            message=str(result.get("validation_status", "")),
        )


def _category_of(challenge_dir, paths: ProjectPaths) -> str:
    try:
        relative = challenge_dir.resolve().relative_to(paths.challenges.resolve())
    except ValueError:
        return ""
    return relative.parts[0] if relative.parts else ""
