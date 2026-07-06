"""SQLAlchemy models for design profile reservations and ledgers."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from persistence.models.base import Base
from persistence.models.design_tasks import DesignTask
from persistence.models.research import (
    ChallengeCategory,
    CreatedAt,
    GenerationRequest,
    UpdatedAt,
    UuidPk,
)


class DesignProfileLedger(Base):
    __tablename__ = "design_profile_ledgers"
    __table_args__ = (
        sa.CheckConstraint(
            "ledger_version >= 0",
            name="ck_design_profile_ledgers_version_nonnegative",
        ),
        sa.CheckConstraint(
            "policy_version > 0",
            name="ck_design_profile_ledgers_policy_version_positive",
        ),
        sa.UniqueConstraint("category", name="uq_design_profile_ledgers_category"),
    )

    id: Mapped[UuidPk]
    category: Mapped[str] = mapped_column(
        sa.Text(),
        sa.ForeignKey("challenge_categories.code"),
        nullable=False,
    )
    policy_version: Mapped[int] = mapped_column(sa.Integer(), nullable=False)
    ledger_version: Mapped[int] = mapped_column(
        sa.Integer(),
        nullable=False,
        server_default=sa.text("0"),
    )
    created_at: Mapped[CreatedAt]
    updated_at: Mapped[UpdatedAt]

    category_ref: Mapped[ChallengeCategory] = relationship()


class DesignProfileReservation(Base):
    __tablename__ = "design_profile_reservations"
    __table_args__ = (
        sa.CheckConstraint(
            "state in ('reserved', 'committed', 'released')",
            name="ck_design_profile_reservations_state",
        ),
        sa.CheckConstraint(
            "reservation_version > 0",
            name="ck_design_profile_reservations_version_positive",
        ),
        sa.CheckConstraint(
            "policy_version > 0",
            name="ck_design_profile_reservations_policy_version_positive",
        ),
        sa.CheckConstraint(
            "taxonomy_version > 0",
            name="ck_design_profile_reservations_taxonomy_version_positive",
        ),
        sa.CheckConstraint(
            "ledger_version >= 0",
            name="ck_design_profile_reservations_ledger_version_nonnegative",
        ),
        sa.UniqueConstraint(
            "design_task_id",
            "reservation_version",
            name="uq_design_profile_reservations_task_version",
        ),
        sa.Index(
            "uq_design_profile_reservations_active_task",
            "design_task_id",
            unique=True,
            postgresql_where=sa.text("state IN ('reserved', 'committed')"),
        ),
        sa.Index(
            "uq_design_profile_reservations_active_scoped_key",
            "policy_version",
            "occupancy_scope",
            "exclusive_signature_key",
            unique=True,
            postgresql_where=sa.text(
                "state IN ('reserved', 'committed') "
                "AND occupancy_scope IS NOT NULL "
                "AND exclusive_signature_key IS NOT NULL"
            ),
        ),
    )

    id: Mapped[UuidPk]
    design_task_id: Mapped[UUID | None] = mapped_column(
        sa.Uuid(),
        sa.ForeignKey("design_tasks.id", ondelete="SET NULL"),
        nullable=True,
    )
    generation_request_id: Mapped[UUID] = mapped_column(
        sa.Uuid(),
        sa.ForeignKey("generation_requests.id", ondelete="CASCADE"),
        nullable=False,
    )
    reservation_version: Mapped[int] = mapped_column(sa.Integer(), nullable=False)
    profile: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    profile_signature: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    occupancy_scope: Mapped[str | None] = mapped_column(sa.Text())
    exclusive_signature_key: Mapped[str | None] = mapped_column(sa.Text())
    state: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    taxonomy_version: Mapped[int] = mapped_column(sa.Integer(), nullable=False)
    policy_version: Mapped[int] = mapped_column(sa.Integer(), nullable=False)
    ledger_version: Mapped[int] = mapped_column(sa.Integer(), nullable=False)
    created_at: Mapped[CreatedAt]
    committed_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    released_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))

    design_task: Mapped[DesignTask] = relationship(
        foreign_keys=[design_task_id]
    )
    generation_request: Mapped[GenerationRequest] = relationship(
        foreign_keys=[generation_request_id]
    )
