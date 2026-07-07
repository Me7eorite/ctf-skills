"""Pre-build difficulty review for structured challenge designs."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from domain import challenge_designs as design_dto
from domain import design_tasks as task_dto
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

        return DifficultyReviewResult(
            passed=True,
            claimed_difficulty=design_task.difficulty,
            actual_difficulty=design_task.difficulty,
            confidence=0.84,
            reasons=("design contains a build-reviewable challenge payload",),
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
