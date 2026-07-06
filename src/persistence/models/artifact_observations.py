"""SQLAlchemy model for governed artifact observations."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from persistence.models.base import Base
from persistence.models.build_attempts import BuildAttempt
from persistence.models.research import CreatedAt, UuidPk


class ArtifactObservation(Base):
    __tablename__ = "artifact_observations"
    __table_args__ = (
        sa.CheckConstraint(
            "status in ('passed', 'failed', 'inconclusive')",
            name="ck_artifact_observations_status",
        ),
        sa.CheckConstraint(
            "observation_version > 0",
            name="ck_artifact_observations_version_positive",
        ),
        sa.UniqueConstraint(
            "build_attempt_id",
            "observation_version",
            name="uq_artifact_observations_attempt_version",
        ),
        sa.Index(
            "uq_artifact_observations_current_attempt",
            "build_attempt_id",
            unique=True,
            postgresql_where=sa.text("is_current"),
        ),
        sa.Index("ix_artifact_observations_contract_sha256", "contract_sha256"),
        sa.Index(
            "ix_artifact_observations_artifact_manifest_sha256",
            "artifact_manifest_sha256",
        ),
        sa.Index(
            "ix_artifact_observations_design_evidence",
            "design_evidence_id",
        ),
    )

    id: Mapped[UuidPk]
    build_attempt_id: Mapped[UUID] = mapped_column(
        sa.Uuid(),
        sa.ForeignKey("build_attempts.id", ondelete="CASCADE"),
        nullable=False,
    )
    observation_version: Mapped[int] = mapped_column(sa.Integer(), nullable=False)
    design_evidence_id: Mapped[UUID | None] = mapped_column(
        sa.Uuid(),
        sa.ForeignKey("design_evidence.id", ondelete="SET NULL"),
    )
    contract_sha256: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    artifact_manifest_sha256: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    observed_profile: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    contract_checks: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    negative_test_results: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    fingerprints: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    is_current: Mapped[bool] = mapped_column(
        sa.Boolean(),
        nullable=False,
        server_default=sa.text("true"),
    )
    created_at: Mapped[CreatedAt]
    superseded_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))

    build_attempt: Mapped[BuildAttempt] = relationship("BuildAttempt")

