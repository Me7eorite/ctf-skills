"""add design task plan review marker

Revision ID: 0015_design_plan_review
Revises: 0014_design_diversity_flags
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0015_design_plan_review"
down_revision = "0014_design_diversity_flags"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "design_tasks",
        sa.Column("plan_reviewed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("design_tasks", "plan_reviewed_at")
