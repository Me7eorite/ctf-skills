"""SQLAlchemy models for corpus-governance persistence."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from persistence.models.artifact_observations import ArtifactObservation
from persistence.models.base import Base
from persistence.models.build_attempts import BuildAttempt
from persistence.models.challenge_designs import DesignEvidence
from persistence.models.research import ChallengeCategory, CreatedAt, UuidPk


class CorpusBatch(Base):
    __tablename__ = "corpus_batches"
    __table_args__ = (
        sa.CheckConstraint(
            "mode in ('shadow', 'trial', 'production')",
            name="ck_corpus_batches_mode",
        ),
        sa.CheckConstraint(
            "status in ('draft', 'evaluating', 'evaluated', 'released', 'retired')",
            name="ck_corpus_batches_status",
        ),
        sa.CheckConstraint("policy_version > 0", name="ck_corpus_batches_policy_version_positive"),
        sa.Index("ix_corpus_batches_category_status", "category", "status"),
        sa.Index("ix_corpus_batches_mode_status", "mode", "status"),
    )

    id: Mapped[UuidPk]
    mode: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    category: Mapped[str] = mapped_column(
        sa.Text(),
        sa.ForeignKey("challenge_categories.code"),
        nullable=False,
    )
    policy_version: Mapped[int] = mapped_column(sa.Integer(), nullable=False)
    status: Mapped[str] = mapped_column(
        sa.Text(),
        nullable=False,
        server_default=sa.text("'draft'"),
    )
    created_by: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    created_at: Mapped[CreatedAt]
    evaluation_started_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    evaluated_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    released_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))

    category_ref: Mapped[ChallengeCategory] = relationship()


class CorpusBatchMember(Base):
    __tablename__ = "corpus_batch_members"
    __table_args__ = (
        sa.CheckConstraint(
            "fingerprint_version > 0",
            name="ck_corpus_batch_members_fingerprint_version_positive",
        ),
        sa.UniqueConstraint(
            "batch_id",
            "build_attempt_id",
            name="uq_corpus_batch_members_batch_attempt",
        ),
        sa.UniqueConstraint(
            "batch_id",
            "design_evidence_id",
            name="uq_corpus_batch_members_batch_evidence",
        ),
        sa.Index("ix_corpus_batch_members_batch", "batch_id"),
        sa.Index("ix_corpus_batch_members_design_evidence", "design_evidence_id"),
        sa.Index("ix_corpus_batch_members_artifact_observation", "artifact_observation_id"),
    )

    id: Mapped[UuidPk]
    batch_id: Mapped[UUID] = mapped_column(
        sa.Uuid(),
        sa.ForeignKey("corpus_batches.id", ondelete="CASCADE"),
        nullable=False,
    )
    build_attempt_id: Mapped[UUID] = mapped_column(
        sa.Uuid(),
        sa.ForeignKey("build_attempts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    design_evidence_id: Mapped[UUID] = mapped_column(
        sa.Uuid(),
        sa.ForeignKey("design_evidence.id", ondelete="RESTRICT"),
        nullable=False,
    )
    artifact_observation_id: Mapped[UUID] = mapped_column(
        sa.Uuid(),
        sa.ForeignKey("artifact_observations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    fingerprint_version: Mapped[int] = mapped_column(sa.Integer(), nullable=False)
    fingerprints: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[CreatedAt]

    batch: Mapped[CorpusBatch] = relationship()
    build_attempt: Mapped[BuildAttempt] = relationship()
    design_evidence: Mapped[DesignEvidence] = relationship()
    artifact_observation: Mapped[ArtifactObservation] = relationship()


class CorpusDecision(Base):
    __tablename__ = "corpus_decisions"
    __table_args__ = (
        sa.CheckConstraint(
            "scope in ('member', 'aggregate')",
            name="ck_corpus_decisions_scope",
        ),
        sa.CheckConstraint(
            "decision in ('passed', 'review_required', 'blocked')",
            name="ck_corpus_decisions_decision",
        ),
        sa.CheckConstraint("policy_version > 0", name="ck_corpus_decisions_policy_version_positive"),
        sa.CheckConstraint(
            "(scope = 'member' AND member_id IS NOT NULL) OR "
            "(scope = 'aggregate' AND member_id IS NULL)",
            name="ck_corpus_decisions_scope_member",
        ),
        sa.Index(
            "uq_corpus_decisions_current_member",
            "member_id",
            unique=True,
            postgresql_where=sa.text("is_current AND member_id IS NOT NULL"),
        ),
        sa.Index(
            "uq_corpus_decisions_current_aggregate",
            "batch_id",
            unique=True,
            postgresql_where=sa.text("is_current AND scope = 'aggregate'"),
        ),
        sa.Index("ix_corpus_decisions_batch_scope", "batch_id", "scope"),
    )

    id: Mapped[UuidPk]
    batch_id: Mapped[UUID] = mapped_column(
        sa.Uuid(),
        sa.ForeignKey("corpus_batches.id", ondelete="CASCADE"),
        nullable=False,
    )
    member_id: Mapped[UUID | None] = mapped_column(
        sa.Uuid(),
        sa.ForeignKey("corpus_batch_members.id", ondelete="CASCADE"),
    )
    scope: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    decision: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    reasons: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    policy_version: Mapped[int] = mapped_column(sa.Integer(), nullable=False)
    is_current: Mapped[bool] = mapped_column(
        sa.Boolean(),
        nullable=False,
        server_default=sa.text("true"),
    )
    created_at: Mapped[CreatedAt]
    superseded_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))

    batch: Mapped[CorpusBatch] = relationship()
    member: Mapped[CorpusBatchMember | None] = relationship()


class CorpusMatch(Base):
    __tablename__ = "corpus_matches"
    __table_args__ = (
        sa.CheckConstraint(
            "score >= 0 AND score <= 1",
            name="ck_corpus_matches_score",
        ),
        sa.CheckConstraint(
            "threshold >= 0 AND threshold <= 1",
            name="ck_corpus_matches_threshold",
        ),
        sa.CheckConstraint(
            "fingerprint_type in ('semantic', 'solve', 'implementation', 'combined', "
            "'source', 'solver', 'intended_path')",
            name="ck_corpus_matches_fingerprint_type",
        ),
        sa.CheckConstraint(
            "compared_member_id IS NOT NULL OR compared_history_entry_id IS NOT NULL",
            name="ck_corpus_matches_compared_target",
        ),
        sa.Index("ix_corpus_matches_member_type", "member_id", "fingerprint_type"),
        sa.Index("ix_corpus_matches_batch_score", "batch_id", sa.text("score DESC")),
    )

    id: Mapped[UuidPk]
    batch_id: Mapped[UUID] = mapped_column(
        sa.Uuid(),
        sa.ForeignKey("corpus_batches.id", ondelete="CASCADE"),
        nullable=False,
    )
    member_id: Mapped[UUID] = mapped_column(
        sa.Uuid(),
        sa.ForeignKey("corpus_batch_members.id", ondelete="CASCADE"),
        nullable=False,
    )
    compared_member_id: Mapped[UUID | None] = mapped_column(
        sa.Uuid(),
        sa.ForeignKey("corpus_batch_members.id", ondelete="CASCADE"),
    )
    compared_history_entry_id: Mapped[UUID | None] = mapped_column(
        sa.Uuid(),
        sa.ForeignKey("corpus_history_entries.id", ondelete="CASCADE"),
    )
    fingerprint_type: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    score: Mapped[float] = mapped_column(sa.Float(), nullable=False)
    threshold: Mapped[float] = mapped_column(sa.Float(), nullable=False)
    reason: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    created_at: Mapped[CreatedAt]

    batch: Mapped[CorpusBatch] = relationship()
    member: Mapped[CorpusBatchMember] = relationship(foreign_keys=[member_id])
    compared_member: Mapped[CorpusBatchMember | None] = relationship(
        foreign_keys=[compared_member_id]
    )


class ObservationReviewDecision(Base):
    __tablename__ = "observation_review_decisions"
    __table_args__ = (
        sa.CheckConstraint(
            "decision in ('accepted', 'rejected')",
            name="ck_observation_review_decisions_decision",
        ),
        sa.Index(
            "ix_observation_review_decisions_observation",
            "artifact_observation_id",
            "created_at",
        ),
    )

    id: Mapped[UuidPk]
    artifact_observation_id: Mapped[UUID] = mapped_column(
        sa.Uuid(),
        sa.ForeignKey("artifact_observations.id", ondelete="CASCADE"),
        nullable=False,
    )
    decision: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    actor: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    reason: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    scope: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    created_at: Mapped[CreatedAt]

    artifact_observation: Mapped[ArtifactObservation] = relationship()


class CorpusReviewDecision(Base):
    __tablename__ = "corpus_review_decisions"
    __table_args__ = (
        sa.CheckConstraint(
            "decision in ('approved', 'rejected')",
            name="ck_corpus_review_decisions_decision",
        ),
        sa.Index("ix_corpus_review_decisions_decision", "corpus_decision_id", "created_at"),
    )

    id: Mapped[UuidPk]
    corpus_decision_id: Mapped[UUID] = mapped_column(
        sa.Uuid(),
        sa.ForeignKey("corpus_decisions.id", ondelete="CASCADE"),
        nullable=False,
    )
    decision: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    actor: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    reason: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    scope: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    created_at: Mapped[CreatedAt]

    corpus_decision: Mapped[CorpusDecision] = relationship()


class CorpusHistoryEntry(Base):
    __tablename__ = "corpus_history_entries"
    __table_args__ = (
        sa.CheckConstraint(
            "fingerprint_version > 0",
            name="ck_corpus_history_entries_fingerprint_version_positive",
        ),
        sa.CheckConstraint(
            "status in ('published', 'retired')",
            name="ck_corpus_history_entries_status",
        ),
        sa.Index("ix_corpus_history_entries_category_status", "category", "status"),
        sa.Index("ix_corpus_history_entries_challenge_id", "challenge_id"),
    )

    id: Mapped[UuidPk]
    challenge_id: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    category: Mapped[str] = mapped_column(
        sa.Text(),
        sa.ForeignKey("challenge_categories.code"),
        nullable=False,
    )
    design_evidence_id: Mapped[UUID | None] = mapped_column(
        sa.Uuid(),
        sa.ForeignKey("design_evidence.id", ondelete="SET NULL"),
    )
    build_attempt_id: Mapped[UUID | None] = mapped_column(
        sa.Uuid(),
        sa.ForeignKey("build_attempts.id", ondelete="SET NULL"),
    )
    artifact_observation_id: Mapped[UUID | None] = mapped_column(
        sa.Uuid(),
        sa.ForeignKey("artifact_observations.id", ondelete="SET NULL"),
    )
    fingerprint_version: Mapped[int] = mapped_column(sa.Integer(), nullable=False)
    fingerprints: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    audit_reason: Mapped[str | None] = mapped_column(sa.Text())
    created_at: Mapped[CreatedAt]

    category_ref: Mapped[ChallengeCategory] = relationship()
