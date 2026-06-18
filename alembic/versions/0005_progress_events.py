"""progress events and snapshots

Revision ID: 0005_progress_events
Revises: 0004_design_attempts_and_designs
Create Date: 2026-06-18

Moves runner progress from the local SQLite state file into PostgreSQL.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0005_progress_events"
down_revision = "0004_design_attempts_and_designs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "progress_events",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("shard", sa.Text(), nullable=False),
        sa.Column(
            "challenge_id",
            sa.Text(),
            nullable=False,
            server_default=sa.text("''"),
        ),
        sa.Column("worker", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("stage", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("percent", sa.Integer(), nullable=False),
        sa.Column("message", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "stage in ('queued', 'design', 'implement', 'build', "
            "'validate', 'document', 'complete')",
            name="ck_progress_events_stage",
        ),
        sa.CheckConstraint(
            "status in ('pending', 'running', 'passed', 'failed')",
            name="ck_progress_events_status",
        ),
    )
    op.create_index(
        "ix_progress_events_shard_id",
        "progress_events",
        ["shard", "id"],
    )
    op.create_index(
        "ix_progress_events_challenge_id",
        "progress_events",
        ["shard", "challenge_id", "id"],
    )
    op.create_index(
        "ix_progress_events_claims",
        "progress_events",
        ["shard", "id"],
        postgresql_where=sa.text(
            "challenge_id = '' AND stage = 'queued' AND status = 'running'"
        ),
    )

    op.create_table(
        "progress_snapshots",
        sa.Column("shard", sa.Text(), primary_key=True),
        sa.Column(
            "challenge_id",
            sa.Text(),
            primary_key=True,
            server_default=sa.text("''"),
        ),
        sa.Column("worker", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("stage", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("percent", sa.Integer(), nullable=False),
        sa.Column("message", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )


def downgrade() -> None:
    op.drop_table("progress_snapshots")
    op.drop_index("ix_progress_events_claims", table_name="progress_events")
    op.drop_index("ix_progress_events_challenge_id", table_name="progress_events")
    op.drop_index("ix_progress_events_shard_id", table_name="progress_events")
    op.drop_table("progress_events")
