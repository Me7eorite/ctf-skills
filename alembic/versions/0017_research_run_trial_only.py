"""add research_runs.trial_only

Revision ID: 0017_research_run_trial_only
Revises: 0016_design_difficulty_reviews
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0017_research_run_trial_only"
down_revision = "0016_design_difficulty_reviews"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "research_runs",
        sa.Column("trial_only", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )


def downgrade() -> None:
    op.drop_column("research_runs", "trial_only")
