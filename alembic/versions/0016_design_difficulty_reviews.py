"""add design difficulty reviews

Revision ID: 0016_design_difficulty_reviews
Revises: 0015_design_plan_review
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0016_design_difficulty_reviews"
down_revision = "0015_design_plan_review"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "design_difficulty_reviews",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("design_task_id", sa.Uuid(), nullable=False),
        sa.Column("challenge_design_id", sa.Uuid(), nullable=False),
        sa.Column("passed", sa.Boolean(), nullable=False),
        sa.Column("claimed_difficulty", sa.Text(), nullable=False),
        sa.Column("actual_difficulty", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column(
            "reasons",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "detected_risks",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "required_revision",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("reviewer", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_design_difficulty_reviews_confidence"),
        sa.ForeignKeyConstraint(["challenge_design_id"], ["challenge_designs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["design_task_id"], ["design_tasks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_design_difficulty_reviews_design_created",
        "design_difficulty_reviews",
        ["challenge_design_id", "created_at"],
    )
    op.create_index(
        "ix_design_difficulty_reviews_task_created",
        "design_difficulty_reviews",
        ["design_task_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_design_difficulty_reviews_task_created", table_name="design_difficulty_reviews")
    op.drop_index("ix_design_difficulty_reviews_design_created", table_name="design_difficulty_reviews")
    op.drop_table("design_difficulty_reviews")
