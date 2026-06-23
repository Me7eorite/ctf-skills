"""add weakly-enforced research finding technique family

Revision ID: 0013_research_tech_family
Revises: 0012_executions_lease_fencing
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0013_research_tech_family"
down_revision = "0012_executions_lease_fencing"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "research_findings",
        sa.Column("technique_family", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("research_findings", "technique_family")
