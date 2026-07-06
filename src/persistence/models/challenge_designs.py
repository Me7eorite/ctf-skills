"""SQLAlchemy models for structured challenge design persistence."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from persistence.models.base import Base
from persistence.models.design_tasks import DesignTask
from persistence.models.research import CreatedAt, UpdatedAt, UuidPk


class DesignAttempt(Base):
    __tablename__ = "design_attempts"
    __table_args__ = (
        sa.CheckConstraint("attempt > 0", name="ck_design_attempts_attempt_positive"),
        sa.CheckConstraint(
            "status in ('running', 'completed', 'failed')",
            name="ck_design_attempts_status",
        ),
        sa.UniqueConstraint(
            "design_task_id",
            "attempt",
            name="uq_design_attempts_task_attempt",
        ),
        sa.Index("ix_design_attempts_design_task_status", "design_task_id", "status"),
    )

    id: Mapped[UuidPk]
    design_task_id: Mapped[UUID] = mapped_column(
        sa.Uuid(),
        sa.ForeignKey("design_tasks.id", ondelete="CASCADE"),
        nullable=False,
    )
    attempt: Mapped[int] = mapped_column(sa.Integer(), nullable=False)
    status: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    claimed_by: Mapped[str | None] = mapped_column(sa.Text())
    claim_token: Mapped[UUID] = mapped_column(sa.Uuid(), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    profile_name_used: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    prompt_path: Mapped[str | None] = mapped_column(sa.Text())
    hermes_log_path: Mapped[str | None] = mapped_column(sa.Text())
    last_error: Mapped[str | None] = mapped_column(sa.Text())
    created_at: Mapped[CreatedAt]

    design_task: Mapped[DesignTask] = relationship()


class ChallengeDesign(Base):
    __tablename__ = "challenge_designs"
    __table_args__ = (
        sa.CheckConstraint(
            "status in ('draft', 'accepted', 'superseded')",
            name="ck_challenge_designs_status",
        ),
        sa.Index(
            "uq_challenge_designs_task_draft",
            "design_task_id",
            unique=True,
            postgresql_where=sa.text("status = 'draft'"),
        ),
    )

    id: Mapped[UuidPk]
    design_task_id: Mapped[UUID] = mapped_column(
        sa.Uuid(),
        sa.ForeignKey("design_tasks.id", ondelete="CASCADE"),
        nullable=False,
    )
    design_attempt_id: Mapped[UUID] = mapped_column(
        sa.Uuid(),
        sa.ForeignKey("design_attempts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    summary: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    flag_format: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    validation_notes: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    quality_gate_passed: Mapped[bool] = mapped_column(sa.Boolean(), nullable=False)
    # Phase 2 (D2): rows persisted before the difficulty rubric existed
    # are flagged so the future backfill / re-design script can pick them
    # up without rejecting the rest of the table. New rows default FALSE.
    legacy_grandfather: Mapped[bool] = mapped_column(
        sa.Boolean(), nullable=False, server_default=sa.text("false"), default=False
    )
    status: Mapped[str] = mapped_column(
        sa.Text(),
        nullable=False,
        server_default=sa.text("'draft'"),
    )
    created_at: Mapped[CreatedAt]
    updated_at: Mapped[UpdatedAt]

    design_task: Mapped[DesignTask] = relationship()
    design_attempt: Mapped[DesignAttempt] = relationship()


class DesignDifficultyReview(Base):
    __tablename__ = "design_difficulty_reviews"
    __table_args__ = (
        sa.CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_design_difficulty_reviews_confidence"),
        sa.Index("ix_design_difficulty_reviews_task_created", "design_task_id", "created_at"),
        sa.Index("ix_design_difficulty_reviews_design_created", "challenge_design_id", "created_at"),
    )

    id: Mapped[UuidPk]
    design_task_id: Mapped[UUID] = mapped_column(
        sa.Uuid(),
        sa.ForeignKey("design_tasks.id", ondelete="CASCADE"),
        nullable=False,
    )
    challenge_design_id: Mapped[UUID] = mapped_column(
        sa.Uuid(),
        sa.ForeignKey("challenge_designs.id", ondelete="CASCADE"),
        nullable=False,
    )
    passed: Mapped[bool] = mapped_column(sa.Boolean(), nullable=False)
    claimed_difficulty: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    actual_difficulty: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    confidence: Mapped[float] = mapped_column(sa.Float(), nullable=False)
    reasons: Mapped[list[str]] = mapped_column(JSONB, nullable=False, server_default=sa.text("'[]'::jsonb"))
    detected_risks: Mapped[list[str]] = mapped_column(JSONB, nullable=False, server_default=sa.text("'[]'::jsonb"))
    required_revision: Mapped[list[str]] = mapped_column(JSONB, nullable=False, server_default=sa.text("'[]'::jsonb"))
    reviewer: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    created_at: Mapped[CreatedAt]

    design_task: Mapped[DesignTask] = relationship()
    challenge_design: Mapped[ChallengeDesign] = relationship()


class DesignEvidence(Base):
    __tablename__ = "design_evidence"
    __table_args__ = (
        sa.CheckConstraint("evidence_version > 0", name="ck_design_evidence_version_positive"),
        sa.UniqueConstraint(
            "design_task_id",
            "evidence_version",
            name="uq_design_evidence_task_version",
        ),
        sa.Index(
            "uq_design_evidence_live_task",
            "design_task_id",
            unique=True,
            postgresql_where=sa.text("superseded_at IS NULL"),
        ),
        sa.Index("ix_design_evidence_profile_signature", "profile_signature"),
        sa.Index("ix_design_evidence_challenge_design", "challenge_design_id"),
    )

    id: Mapped[UuidPk]
    design_task_id: Mapped[UUID] = mapped_column(
        sa.Uuid(),
        sa.ForeignKey("design_tasks.id", ondelete="CASCADE"),
        nullable=False,
    )
    evidence_version: Mapped[int] = mapped_column(sa.Integer(), nullable=False)
    challenge_design_id: Mapped[UUID] = mapped_column(
        sa.Uuid(),
        sa.ForeignKey("challenge_designs.id", ondelete="RESTRICT"),
        nullable=False,
    )
    research_finding_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    profile: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    profile_signature: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    distinctness_claim: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    compared_challenge_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    build_contract: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    ledger_version: Mapped[int] = mapped_column(sa.Integer(), nullable=False)
    created_at: Mapped[CreatedAt]
    superseded_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    superseded_by_evidence_id: Mapped[UUID | None] = mapped_column(
        sa.Uuid(),
        sa.ForeignKey("design_evidence.id", ondelete="SET NULL"),
    )
    supersession_reason: Mapped[str | None] = mapped_column(sa.Text())

    design_task: Mapped[DesignTask] = relationship(foreign_keys=[design_task_id])
    challenge_design: Mapped[ChallengeDesign] = relationship()
