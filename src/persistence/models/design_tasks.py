"""SQLAlchemy model for the design-task-planning persistence."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from persistence.models.base import Base
from persistence.models.research import (
    ChallengeCategory,
    CreatedAt,
    GenerationRequest,
    ResearchRun,
    UpdatedAt,
    UuidPk,
)


class DesignTask(Base):
    __tablename__ = "design_tasks"
    __table_args__ = (
        sa.CheckConstraint("points > 0", name="ck_design_tasks_points_positive"),
        sa.CheckConstraint("task_no > 0", name="ck_design_tasks_task_no_positive"),
        sa.CheckConstraint(
            "difficulty in ('easy', 'medium', 'hard', 'expert')",
            name="ck_design_tasks_difficulty",
        ),
        sa.CheckConstraint(
            "status in ('draft', 'queued', 'designing', 'designed', 'failed', "
            "'archived', 'building', 'built', 'build_failed')",
            name="ck_design_tasks_status",
        ),
        sa.UniqueConstraint(
            "generation_request_id",
            "task_no",
            name="uq_design_tasks_request_task_no",
        ),
        sa.UniqueConstraint(
            "generation_request_id",
            "challenge_id",
            name="uq_design_tasks_request_challenge_id",
        ),
        sa.Index(
            "ix_design_tasks_generation_request_status",
            "generation_request_id",
            "status",
        ),
    )

    id: Mapped[UuidPk]
    generation_request_id: Mapped[UUID] = mapped_column(
        sa.Uuid(),
        sa.ForeignKey("generation_requests.id", ondelete="CASCADE"),
        nullable=False,
    )
    research_run_id: Mapped[UUID] = mapped_column(
        sa.Uuid(),
        sa.ForeignKey("research_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    task_no: Mapped[int] = mapped_column(sa.Integer(), nullable=False)
    challenge_id: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    title: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    category: Mapped[str] = mapped_column(
        sa.Text(),
        sa.ForeignKey("challenge_categories.code"),
        nullable=False,
    )
    difficulty: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    primary_technique: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    learning_objective: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    points: Mapped[int] = mapped_column(sa.Integer(), nullable=False)
    port: Mapped[int | None] = mapped_column(sa.Integer())
    scenario: Mapped[str] = mapped_column(
        sa.Text(),
        nullable=False,
        server_default=sa.text("''"),
    )
    constraints: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=sa.text("'{}'::jsonb"),
    )
    evidence_summary: Mapped[str] = mapped_column(
        sa.Text(),
        nullable=False,
        server_default=sa.text("''"),
    )
    finding_ids: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=sa.text("'[]'::jsonb"),
    )
    diversity_flags: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    current_reservation_id: Mapped[UUID | None] = mapped_column(
        sa.Uuid(),
        sa.ForeignKey("design_profile_reservations.id", ondelete="SET NULL"),
    )
    plan_reviewed_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    next_build_attempt_no: Mapped[int] = mapped_column(
        sa.Integer(),
        nullable=False,
        server_default=sa.text("1"),
    )
    status: Mapped[str] = mapped_column(
        sa.Text(),
        nullable=False,
        server_default=sa.text("'draft'"),
    )
    created_at: Mapped[CreatedAt]
    updated_at: Mapped[UpdatedAt]

    generation_request: Mapped[GenerationRequest] = relationship()
    research_run: Mapped[ResearchRun] = relationship()
    category_ref: Mapped[ChallengeCategory] = relationship()
