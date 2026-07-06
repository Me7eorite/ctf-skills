"""SQLAlchemy model for build-attempt persistence."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from persistence.models.base import Base
from persistence.models.design_tasks import DesignTask
from persistence.models.research import CreatedAt, UuidPk

if TYPE_CHECKING:
    from persistence.models.challenge_designs import DesignEvidence


class BuildAttempt(Base):
    __tablename__ = "build_attempts"
    __table_args__ = (
        sa.CheckConstraint(
            "status in ('queued', 'running', 'succeeded', 'failed', 'lost')",
            name="ck_build_attempts_status",
        ),
        sa.CheckConstraint(
            "artifact_status in ('unknown', 'present', 'missing')",
            name="ck_build_attempts_artifact_status",
        ),
        sa.UniqueConstraint(
            "design_task_id",
            "attempt_no",
            name="uq_build_attempts_task_attempt_no",
        ),
        sa.Index(
            "one_active_build_per_task",
            "design_task_id",
            unique=True,
            postgresql_where=sa.text("status IN ('queued','running')"),
        ),
        sa.Index(
            "ix_build_attempts_status_created",
            "status",
            sa.text("created_at DESC"),
        ),
        sa.Index("ix_build_attempts_shard", "shard_basename"),
        sa.Index(
            "uq_build_attempts_idempotency_key",
            "idempotency_key",
            unique=True,
            postgresql_where=sa.text("idempotency_key IS NOT NULL"),
        ),
        # Execution pointers (add-execution-lease-and-fencing). Composite FKs
        # enforce that a referenced execution belongs to this container; created
        # via use_alter because executions.build_attempt_id references back here.
        sa.ForeignKeyConstraint(
            ["current_execution_id", "id"],
            ["executions.id", "executions.build_attempt_id"],
            name="fk_build_attempts_current_execution",
            use_alter=True,
        ),
        sa.ForeignKeyConstraint(
            ["latest_execution_id", "id"],
            ["executions.id", "executions.build_attempt_id"],
            name="fk_build_attempts_latest_execution",
            use_alter=True,
        ),
        sa.ForeignKeyConstraint(
            ["successful_execution_id", "id"],
            ["executions.id", "executions.build_attempt_id"],
            name="fk_build_attempts_successful_execution",
            use_alter=True,
        ),
    )

    id: Mapped[UuidPk]
    design_task_id: Mapped[UUID] = mapped_column(
        sa.Uuid(),
        sa.ForeignKey("design_tasks.id", ondelete="CASCADE"),
        nullable=False,
    )
    attempt_no: Mapped[int] = mapped_column(sa.Integer(), nullable=False)
    status: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    shard_basename: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    worker: Mapped[str | None] = mapped_column(sa.Text())
    resulting_challenge_dir: Mapped[str | None] = mapped_column(sa.Text())
    artifact_status: Mapped[str] = mapped_column(
        sa.Text(),
        nullable=False,
        server_default=sa.text("'unknown'"),
    )
    error: Mapped[str | None] = mapped_column(sa.Text())
    idempotency_key: Mapped[str | None] = mapped_column(sa.Text())
    design_evidence_id: Mapped[UUID | None] = mapped_column(
        sa.Uuid(),
        sa.ForeignKey("design_evidence.id", ondelete="SET NULL"),
    )
    contract_sha256: Mapped[str | None] = mapped_column(sa.Text())
    current_execution_id: Mapped[UUID | None] = mapped_column(sa.Uuid())
    latest_execution_id: Mapped[UUID | None] = mapped_column(sa.Uuid())
    successful_execution_id: Mapped[UUID | None] = mapped_column(sa.Uuid())
    created_at: Mapped[CreatedAt]
    started_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))

    design_task: Mapped[DesignTask] = relationship()
    design_evidence: Mapped[DesignEvidence | None] = relationship("DesignEvidence")
