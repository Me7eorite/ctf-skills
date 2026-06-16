"""design attempts and structured challenge designs

Revision ID: 0004_design_attempts_and_designs
Revises: 0003_design_tasks
Create Date: 2026-06-17

Adds one audit row per design-challenges Hermes invocation and one
structured draft design row per successful attempt.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0004_design_attempts_and_designs"
down_revision = "0003_design_tasks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "design_attempts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "design_task_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("design_tasks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("claimed_by", sa.Text(), nullable=True),
        sa.Column("claim_token", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("started_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("finished_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("profile_name_used", sa.Text(), nullable=False),
        sa.Column("prompt_path", sa.Text(), nullable=True),
        sa.Column("hermes_log_path", sa.Text(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint("attempt > 0", name="ck_design_attempts_attempt_positive"),
        sa.CheckConstraint(
            "status in ('running', 'completed', 'failed')",
            name="ck_design_attempts_status",
        ),
        sa.UniqueConstraint(
            "design_task_id",
            "attempt",
            name="uq_design_attempts_task_attempt",
        ),
    )
    op.create_index(
        "ix_design_attempts_design_task_status",
        "design_attempts",
        ["design_task_id", "status"],
    )

    op.create_table(
        "challenge_designs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "design_task_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("design_tasks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "design_attempt_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("design_attempts.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("flag_format", sa.Text(), nullable=False),
        sa.Column("validation_notes", sa.Text(), nullable=False),
        sa.Column("quality_gate_passed", sa.Boolean(), nullable=False),
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
        sa.CheckConstraint(
            "status in ('draft', 'accepted', 'superseded')",
            name="ck_challenge_designs_status",
        ),
    )
    op.create_index(
        "uq_challenge_designs_task_draft",
        "challenge_designs",
        ["design_task_id"],
        unique=True,
        postgresql_where=sa.text("status = 'draft'"),
    )

    agent_roles = sa.table(
        "agent_roles",
        sa.column("code", sa.Text()),
        sa.column("display_name", sa.Text()),
        sa.column("description", sa.Text()),
    )
    op.bulk_insert(
        agent_roles,
        [
            {
                "code": "design",
                "display_name": "Design Agent",
                "description": "Produces structured challenge design drafts from queued design tasks.",
            },
        ],
    )

    hermes_profile_bindings = sa.table(
        "hermes_profile_bindings",
        sa.column("role", sa.Text()),
        sa.column("profile_name", sa.Text()),
        sa.column("description", sa.Text()),
        sa.column("status", sa.Text()),
    )
    op.bulk_insert(
        hermes_profile_bindings,
        [
            {
                "role": "design",
                "profile_name": "default",
                "description": "Default design-agent Hermes profile binding; operators may change it.",
                "status": "enabled",
            },
        ],
    )


def downgrade() -> None:
    op.execute("DELETE FROM hermes_profile_bindings WHERE role = 'design'")
    op.execute("DELETE FROM agent_roles WHERE code = 'design'")
    op.drop_index("uq_challenge_designs_task_draft", table_name="challenge_designs")
    op.drop_table("challenge_designs")
    op.drop_index("ix_design_attempts_design_task_status", table_name="design_attempts")
    op.drop_table("design_attempts")
