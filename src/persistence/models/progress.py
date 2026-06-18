"""SQLAlchemy models for progress events and dashboard snapshots."""

from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from persistence.models.base import Base


class ProgressEvent(Base):
    __tablename__ = "progress_events"
    __table_args__ = (
        sa.CheckConstraint(
            "stage in ('queued', 'design', 'implement', 'build', "
            "'validate', 'document', 'complete')",
            name="ck_progress_events_stage",
        ),
        sa.CheckConstraint("status in ('pending', 'running', 'passed', 'failed')", name="ck_progress_events_status"),
        sa.Index("ix_progress_events_shard_id", "shard", "id"),
        sa.Index("ix_progress_events_challenge_id", "shard", "challenge_id", "id"),
        sa.Index(
            "ix_progress_events_claims",
            "shard",
            "id",
            postgresql_where=sa.text(
                "challenge_id = '' AND stage = 'queued' AND status = 'running'"
            ),
        ),
    )

    id: Mapped[int] = mapped_column(sa.BigInteger(), primary_key=True, autoincrement=True)
    shard: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    challenge_id: Mapped[str] = mapped_column(sa.Text(), nullable=False, server_default=sa.text("''"))
    worker: Mapped[str] = mapped_column(sa.Text(), nullable=False, server_default=sa.text("''"))
    stage: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    status: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    percent: Mapped[int] = mapped_column(sa.Integer(), nullable=False)
    message: Mapped[str] = mapped_column(sa.Text(), nullable=False, server_default=sa.text("''"))
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    )


class ProgressSnapshot(Base):
    __tablename__ = "progress_snapshots"

    shard: Mapped[str] = mapped_column(sa.Text(), primary_key=True)
    challenge_id: Mapped[str] = mapped_column(sa.Text(), primary_key=True, server_default=sa.text("''"))
    worker: Mapped[str] = mapped_column(sa.Text(), nullable=False, server_default=sa.text("''"))
    stage: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    status: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    percent: Mapped[int] = mapped_column(sa.Integer(), nullable=False)
    message: Mapped[str] = mapped_column(sa.Text(), nullable=False, server_default=sa.text("''"))
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    )
