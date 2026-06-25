"""Persistence helpers for pre-build design difficulty reviews."""

from __future__ import annotations

from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.orm import Session

from domain.design.difficulty_review import DesignDifficultyReview, DifficultyReviewResult
from persistence.models import challenge_designs as model


class DesignDifficultyReviewRepository:
    """Append-only persistence for difficulty review results."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def record(
        self,
        *,
        design_task_id: UUID,
        challenge_design_id: UUID,
        result: DifficultyReviewResult,
    ) -> DesignDifficultyReview:
        row = model.DesignDifficultyReview(
            id=uuid4(),
            design_task_id=design_task_id,
            challenge_design_id=challenge_design_id,
            passed=result.passed,
            claimed_difficulty=result.claimed_difficulty,
            actual_difficulty=result.actual_difficulty,
            confidence=result.confidence,
            reasons=list(result.reasons),
            detected_risks=list(result.detected_risks),
            required_revision=list(result.required_revision),
            reviewer=result.reviewer,
        )
        self.session.add(row)
        self.session.flush()
        self.session.refresh(row)
        return _review(row)

    def latest_for_design_task(self, design_task_id: UUID) -> DesignDifficultyReview | None:
        row = self.session.scalars(
            sa.select(model.DesignDifficultyReview)
            .where(model.DesignDifficultyReview.design_task_id == design_task_id)
            .order_by(model.DesignDifficultyReview.created_at.desc())
            .limit(1)
        ).one_or_none()
        return _review(row) if row else None

    def summarize_for_design_task(self, design_task_id: UUID) -> dict[str, object]:
        total = int(
            self.session.scalar(
                sa.select(sa.func.count())
                .select_from(model.DesignDifficultyReview)
                .where(model.DesignDifficultyReview.design_task_id == design_task_id)
            )
            or 0
        )
        failed = int(
            self.session.scalar(
                sa.select(sa.func.count())
                .select_from(model.DesignDifficultyReview)
                .where(
                    model.DesignDifficultyReview.design_task_id == design_task_id,
                    model.DesignDifficultyReview.passed.is_(False),
                )
            )
            or 0
        )
        latest = self.latest_for_design_task(design_task_id)
        return {
            "total": total,
            "failed": failed,
            "latest": latest,
        }


def _review(row: model.DesignDifficultyReview) -> DesignDifficultyReview:
    return DesignDifficultyReview(
        id=row.id,
        design_task_id=row.design_task_id,
        challenge_design_id=row.challenge_design_id,
        passed=row.passed,
        claimed_difficulty=row.claimed_difficulty,
        actual_difficulty=row.actual_difficulty,
        confidence=row.confidence,
        reasons=tuple(row.reasons or ()),
        detected_risks=tuple(row.detected_risks or ()),
        required_revision=tuple(row.required_revision or ()),
        reviewer=row.reviewer,
        created_at=row.created_at,
    )
