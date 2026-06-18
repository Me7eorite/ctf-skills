"""build attempts

Revision ID: 0006_build_attempts
Revises: 0005_progress_events
Create Date: 2026-06-18

Adds one persisted row per operator-initiated build submission and
extends design task status with build-phase values.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0006_build_attempts"
down_revision = "0005_progress_events"
branch_labels = None
depends_on = None


OLD_DESIGN_TASK_STATUSES = (
    "'draft', 'queued', 'designing', 'designed', 'failed', 'archived'"
)
NEW_DESIGN_TASK_STATUSES = (
    "'draft', 'queued', 'designing', 'designed', 'failed', 'archived', "
    "'building', 'built', 'build_failed'"
)


def upgrade() -> None:
    op.create_table(
        "build_attempts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "design_task_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("design_tasks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("shard_basename", sa.Text(), nullable=False),
        sa.Column("worker", sa.Text(), nullable=True),
        sa.Column("resulting_challenge_dir", sa.Text(), nullable=True),
        sa.Column(
            "artifact_status",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'unknown'"),
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("started_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("finished_at", sa.TIMESTAMP(timezone=True), nullable=True),
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
    )
    op.create_index(
        "one_active_build_per_task",
        "build_attempts",
        ["design_task_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued','running')"),
    )
    op.create_index(
        "ix_build_attempts_status_created",
        "build_attempts",
        ["status", sa.text("created_at DESC")],
    )
    op.create_index(
        "ix_build_attempts_shard",
        "build_attempts",
        ["shard_basename"],
    )

    op.drop_constraint("ck_design_tasks_status", "design_tasks", type_="check")
    op.create_check_constraint(
        "ck_design_tasks_status",
        "design_tasks",
        f"status in ({NEW_DESIGN_TASK_STATUSES})",
    )


def downgrade() -> None:
    op.drop_constraint("ck_design_tasks_status", "design_tasks", type_="check")
    op.create_check_constraint(
        "ck_design_tasks_status",
        "design_tasks",
        f"status in ({OLD_DESIGN_TASK_STATUSES})",
    )

    op.drop_index("ix_build_attempts_shard", table_name="build_attempts")
    op.drop_index("ix_build_attempts_status_created", table_name="build_attempts")
    op.drop_index("one_active_build_per_task", table_name="build_attempts")
    op.drop_table("build_attempts")
