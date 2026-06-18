"""resource deletion build attempt counter

Revision ID: 0007_resource_deletion_counter
Revises: 0006_build_attempts
Create Date: 2026-06-19

Adds a durable per-design-task build attempt allocator so deleting build
attempt rows cannot cause attempt_no reuse.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0007_resource_deletion_counter"
down_revision = "0006_build_attempts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "design_tasks",
        sa.Column(
            "next_build_attempt_no",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        ),
    )
    op.execute(
        """
        UPDATE design_tasks AS dt
        SET next_build_attempt_no = COALESCE((
            SELECT MAX(ba.attempt_no)
            FROM build_attempts AS ba
            WHERE ba.design_task_id = dt.id
        ), 0) + 1
        """
    )
    op.create_check_constraint(
        "ck_design_tasks_next_build_attempt_no_positive",
        "design_tasks",
        "next_build_attempt_no > 0",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_design_tasks_next_build_attempt_no_positive",
        "design_tasks",
        type_="check",
    )
    op.drop_column("design_tasks", "next_build_attempt_no")
