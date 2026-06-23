"""add design task diversity flags

Revision ID: 0014_design_diversity_flags
Revises: 0013_research_tech_family
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0014_design_diversity_flags"
down_revision = "0013_research_tech_family"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "design_tasks",
        sa.Column("diversity_flags", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("design_tasks", "diversity_flags")
