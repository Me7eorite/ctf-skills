"""SQLAlchemy models for execution rows, feedback snapshots, and revalidation.

Introduced by ``add-execution-lease-and-fencing`` (split-plan proposal #3).
A ``build_attempts`` row is a per-build-session *container*; each individual run
is an ``executions`` row carrying a lease + fencing token and an iteration chain.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from persistence.models.base import Base
from persistence.models.research import CreatedAt, UuidPk

EXECUTION_KINDS = ("initial", "retry", "revision")
EXECUTION_MODES = ("standard", "clean")
EXECUTION_STATUSES = ("queued", "claimed", "running", "succeeded", "failed", "lost")
NON_TERMINAL_STATUSES = ("queued", "claimed", "running")
ACTIVE_STATUSES = ("claimed", "running")
TERMINAL_STATUSES = ("succeeded", "failed", "lost")


class BuildFeedbackSnapshot(Base):
    """Immutable, append-only human feedback bound to a build-attempt container."""

    __tablename__ = "build_feedback_snapshots"
    __table_args__ = (
        # Composite-FK target so an execution's feedback must share its container.
        sa.UniqueConstraint(
            "id", "build_attempt_id", name="uq_feedback_id_attempt"
        ),
    )

    id: Mapped[UuidPk]
    build_attempt_id: Mapped[UUID] = mapped_column(
        sa.Uuid(),
        sa.ForeignKey("build_attempts.id", ondelete="CASCADE"),
        nullable=False,
    )
    summary: Mapped[str | None] = mapped_column(sa.Text())
    requested_changes: Mapped[Any] = mapped_column(
        JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")
    )
    preserve: Mapped[Any] = mapped_column(
        JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")
    )
    forbid: Mapped[Any] = mapped_column(
        JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")
    )
    reviewer: Mapped[str | None] = mapped_column(sa.Text())
    created_at: Mapped[CreatedAt]


class Execution(Base):
    """One build run inside a container, with lease + fencing token + lineage."""

    __tablename__ = "executions"
    __table_args__ = (
        sa.CheckConstraint(
            "execution_kind in ('initial', 'retry', 'revision')",
            name="ck_executions_kind",
        ),
        sa.CheckConstraint(
            "execution_mode in ('standard', 'clean')",
            name="ck_executions_mode",
        ),
        sa.CheckConstraint(
            "status in ('queued', 'claimed', 'running', 'succeeded', 'failed', 'lost')",
            name="ck_executions_status",
        ),
        # clean mode only valid for retry kind
        sa.CheckConstraint(
            "execution_mode <> 'clean' OR execution_kind = 'retry'",
            name="ck_executions_clean_requires_retry",
        ),
        # any non-initial execution needs a parent
        sa.CheckConstraint(
            "execution_kind = 'initial' OR parent_execution_id IS NOT NULL",
            name="ck_executions_parent_required",
        ),
        # revision needs a feedback snapshot
        sa.CheckConstraint(
            "execution_kind <> 'revision' OR feedback_snapshot_id IS NOT NULL",
            name="ck_executions_revision_feedback",
        ),
        # queued => null claim fields; claimed/running => non-null token/lease
        sa.CheckConstraint(
            "(status = 'queued' AND claim_token IS NULL AND lease_expires_at IS NULL)"
            " OR (status IN ('claimed', 'running')"
            "     AND claim_token IS NOT NULL AND lease_expires_at IS NOT NULL)"
            " OR status IN ('succeeded', 'failed', 'lost')",
            name="ck_executions_claim_fields",
        ),
        sa.UniqueConstraint(
            "build_attempt_id", "iteration_no", name="uq_executions_attempt_iter"
        ),
        # composite-FK target so child pointers can require the same container
        sa.UniqueConstraint("id", "build_attempt_id", name="uq_executions_id_attempt"),
        sa.ForeignKeyConstraint(
            ["parent_execution_id", "build_attempt_id"],
            ["executions.id", "executions.build_attempt_id"],
            name="fk_executions_parent_same_attempt",
        ),
        sa.ForeignKeyConstraint(
            ["feedback_snapshot_id", "build_attempt_id"],
            [
                "build_feedback_snapshots.id",
                "build_feedback_snapshots.build_attempt_id",
            ],
            name="fk_executions_feedback_same_attempt",
        ),
        # at most one non-terminal execution per container
        sa.Index(
            "one_nonterminal_execution_per_attempt",
            "build_attempt_id",
            unique=True,
            postgresql_where=sa.text(
                "status IN ('queued', 'claimed', 'running')"
            ),
        ),
        # lease reaper scan
        sa.Index(
            "ix_executions_lease",
            "lease_expires_at",
            postgresql_where=sa.text("status IN ('claimed', 'running')"),
        ),
        sa.Index(
            "ix_executions_attempt_iter",
            "build_attempt_id",
            sa.text("iteration_no DESC"),
        ),
    )

    id: Mapped[UuidPk]
    build_attempt_id: Mapped[UUID] = mapped_column(
        sa.Uuid(),
        sa.ForeignKey("build_attempts.id", ondelete="CASCADE"),
        nullable=False,
    )
    parent_execution_id: Mapped[UUID | None] = mapped_column(sa.Uuid())
    iteration_no: Mapped[int] = mapped_column(sa.Integer(), nullable=False)
    execution_kind: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    execution_mode: Mapped[str] = mapped_column(
        sa.Text(), nullable=False, server_default=sa.text("'standard'")
    )
    feedback_snapshot_id: Mapped[UUID | None] = mapped_column(sa.Uuid())
    worker_id: Mapped[str | None] = mapped_column(sa.Text())
    claim_token: Mapped[UUID | None] = mapped_column(sa.Uuid())
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True)
    )
    heartbeat_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    status: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    exit_class: Mapped[str | None] = mapped_column(sa.Text())
    error: Mapped[str | None] = mapped_column(sa.Text())
    started_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    created_at: Mapped[CreatedAt]


class RevalidationEvent(Base):
    """Append-only revalidation record attached to an existing execution."""

    __tablename__ = "revalidation_events"

    id: Mapped[UuidPk]
    execution_id: Mapped[UUID] = mapped_column(
        sa.Uuid(),
        sa.ForeignKey("executions.id", ondelete="CASCADE"),
        nullable=False,
    )
    check_name: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    result: Mapped[Any] = mapped_column(
        JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
    )
    actor: Mapped[str | None] = mapped_column(sa.Text())
    created_at: Mapped[CreatedAt]
