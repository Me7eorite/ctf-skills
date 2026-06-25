"""Domain objects for pre-build design difficulty review."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID


@dataclass(frozen=True)
class DifficultyReviewResult:
    """Result produced before a design is allowed into the build queue."""

    passed: bool
    claimed_difficulty: str
    actual_difficulty: str
    confidence: float
    reasons: tuple[str, ...]
    detected_risks: tuple[str, ...]
    required_revision: tuple[str, ...]
    reviewer: str = "deterministic-asset-flow"

    def to_payload(self) -> dict[str, Any]:
        return {
            "pass": self.passed,
            "claimed_difficulty": self.claimed_difficulty,
            "actual_difficulty": self.actual_difficulty,
            "confidence": self.confidence,
            "reasons": list(self.reasons),
            "detected_risks": list(self.detected_risks),
            "required_revision": list(self.required_revision),
            "reviewer": self.reviewer,
        }


@dataclass(frozen=True)
class DesignDifficultyReview:
    """Persisted audit row for one pre-build difficulty review."""

    id: UUID
    design_task_id: UUID
    challenge_design_id: UUID
    passed: bool
    claimed_difficulty: str
    actual_difficulty: str
    confidence: float
    reasons: tuple[str, ...]
    detected_risks: tuple[str, ...]
    required_revision: tuple[str, ...]
    reviewer: str
    created_at: datetime
