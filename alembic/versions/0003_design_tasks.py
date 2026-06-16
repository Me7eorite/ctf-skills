"""design tasks

Revision ID: 0003_design_tasks
Revises: 0002_research_tables
Create Date: 2026-06-16

Adds ``design_tasks`` — one row per future challenge derived from a
researched generation request. Schema mirrors the shard ``challenges[]``
seed shape (id/title/category/difficulty/primary_technique/learning_
objective/points/port) plus planning metadata used by a later design
worker. Status is intentionally not a PostgreSQL enum: a CHECK
constraint enforces the same allowed set without locking the schema
into a migration just to add a new state later.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0003_design_tasks"
down_revision = "0002_research_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "design_tasks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "generation_request_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("generation_requests.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "research_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("research_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("task_no", sa.Integer(), nullable=False),
        sa.Column("challenge_id", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column(
            "category",
            sa.Text(),
            sa.ForeignKey("challenge_categories.code"),
            nullable=False,
        ),
        sa.Column("difficulty", sa.Text(), nullable=False),
        sa.Column("primary_technique", sa.Text(), nullable=False),
        sa.Column("learning_objective", sa.Text(), nullable=False),
        sa.Column("points", sa.Integer(), nullable=False),
        sa.Column("port", sa.Integer(), nullable=True),
        sa.Column("scenario", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column(
            "constraints",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "evidence_summary",
            sa.Text(),
            nullable=False,
            server_default=sa.text("''"),
        ),
        sa.Column(
            "finding_ids",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'draft'"),
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint("points > 0", name="ck_design_tasks_points_positive"),
        sa.CheckConstraint("task_no > 0", name="ck_design_tasks_task_no_positive"),
        sa.CheckConstraint(
            "difficulty in ('easy', 'medium', 'hard', 'expert')",
            name="ck_design_tasks_difficulty",
        ),
        sa.CheckConstraint(
            "status in ('draft', 'queued', 'designing', 'designed', 'failed', 'archived')",
            name="ck_design_tasks_status",
        ),
        sa.UniqueConstraint(
            "generation_request_id",
            "task_no",
            name="uq_design_tasks_request_task_no",
        ),
        sa.UniqueConstraint(
            "generation_request_id",
            "challenge_id",
            name="uq_design_tasks_request_challenge_id",
        ),
    )
    op.create_index(
        "ix_design_tasks_generation_request_status",
        "design_tasks",
        ["generation_request_id", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_design_tasks_generation_request_status", table_name="design_tasks")
    op.drop_table("design_tasks")
