"""Pre-build difficulty review for structured challenge designs."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from domain import challenge_designs as design_dto
from domain import design_tasks as task_dto
from domain.design.difficulty import difficulty_alignment_violations
from domain.design.difficulty_review import DifficultyReviewResult


class DesignDifficultyValidator:
    """Lightweight review stage that evaluates but never rewrites a design."""

    reviewer = "deterministic-asset-flow"

    def review(
        self,
        *,
        design_task: task_dto.DesignTask,
        challenge_design: design_dto.ChallengeDesign,
    ) -> DifficultyReviewResult:
        challenge, shape_error = self._single_challenge(challenge_design.payload)
        if shape_error is not None:
            reasons = (shape_error,)
            return DifficultyReviewResult(
                passed=False,
                claimed_difficulty=design_task.difficulty,
                actual_difficulty="invalid",
                confidence=0.95,
                reasons=reasons,
                detected_risks=("design payload is not build-reviewable",),
                required_revision=reasons,
                reviewer=self.reviewer,
            )

        violations = difficulty_alignment_violations(challenge, design_task)
        if violations:
            revisions = tuple(_revision_for_violation(item) for item in violations)
            return DifficultyReviewResult(
                passed=False,
                claimed_difficulty=design_task.difficulty,
                actual_difficulty="below_claimed",
                confidence=0.9,
                reasons=tuple(violations),
                detected_risks=tuple(_risk_for_violation(item) for item in violations),
                required_revision=revisions,
                reviewer=self.reviewer,
            )

        return DifficultyReviewResult(
            passed=True,
            claimed_difficulty=design_task.difficulty,
            actual_difficulty=design_task.difficulty,
            confidence=0.84,
            reasons=("deterministic difficulty and asset-flow checks passed",),
            detected_risks=(),
            required_revision=(),
            reviewer=self.reviewer,
        )

    @staticmethod
    def _single_challenge(payload: Mapping[str, Any]) -> tuple[Mapping[str, Any], str | None]:
        challenges = payload.get("challenges")
        if not isinstance(challenges, list) or len(challenges) != 1:
            return {}, "validated design must contain exactly one challenge"
        challenge = challenges[0]
        if not isinstance(challenge, Mapping):
            return {}, "validated design challenge must be an object"
        return challenge, None


def _risk_for_violation(message: str) -> str:
    lowered = message.lower()
    if "asset/capability chain" in lowered or "`asset_flow`" in lowered:
        return "declared difficulty may be step inflation without a required dependency chain"
    if "shortcut_closure" in lowered or "shortcut" in lowered:
        return "implementation may allow direct flag access or generic bypasses"
    if "fingerprint" in lowered:
        return "design may be hard to deduplicate against sibling/generated challenges"
    if "actual_solution_type" in lowered:
        return "nominal technique may be decorative rather than required"
    if "techniques" in lowered:
        return "declared technique count does not match the target difficulty"
    return "claimed difficulty is not supported by the structured design"


def _revision_for_violation(message: str) -> str:
    lowered = message.lower()
    if "asset/capability chain" in lowered or "`asset_flow`" in lowered:
        return "revise asset_flow so each intermediate asset is concrete and required by the next stage"
    if "shortcut_closure" in lowered:
        return (
            "enumerate server-side closure for direct flag, client gate, "
            "guessable token, and public-resource bypasses"
        )
    if "fingerprint" in lowered:
        return "add shape-level fingerprint fields for entrypoint, asset-flow shape, flag access, and scenario"
    if "difficulty_reason" in lowered:
        return "explain why the required dependency chain matches the requested difficulty"
    if "actual_solution_type" in lowered:
        return "state the real solve type and remove generic shortcut solves from the intended path"
    return message
