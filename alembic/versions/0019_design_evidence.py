"""design evidence

Revision ID: 0019_design_evidence
Revises: 0018_design_profile_reservations
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0019_design_evidence"
down_revision = "0018_design_profile_reservations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "design_evidence",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "design_task_id",
            sa.Uuid(),
            sa.ForeignKey("design_tasks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("evidence_version", sa.Integer(), nullable=False),
        sa.Column(
            "challenge_design_id",
            sa.Uuid(),
            sa.ForeignKey("challenge_designs.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("research_finding_ids", postgresql.JSONB(), nullable=False),
        sa.Column("profile", postgresql.JSONB(), nullable=False),
        sa.Column("profile_signature", sa.Text(), nullable=False),
        sa.Column("distinctness_claim", sa.Text(), nullable=False),
        sa.Column("compared_challenge_ids", postgresql.JSONB(), nullable=False),
        sa.Column("evidence", postgresql.JSONB(), nullable=False),
        sa.Column("build_contract", postgresql.JSONB(), nullable=False),
        sa.Column("ledger_version", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("superseded_at", sa.DateTime(timezone=True)),
        sa.Column(
            "superseded_by_evidence_id",
            sa.Uuid(),
            sa.ForeignKey("design_evidence.id", ondelete="SET NULL"),
        ),
        sa.Column("supersession_reason", sa.Text()),
        sa.CheckConstraint(
            "evidence_version > 0",
            name="ck_design_evidence_version_positive",
        ),
        sa.UniqueConstraint(
            "design_task_id",
            "evidence_version",
            name="uq_design_evidence_task_version",
        ),
    )
    op.create_index(
        "uq_design_evidence_live_task",
        "design_evidence",
        ["design_task_id"],
        unique=True,
        postgresql_where=sa.text("superseded_at IS NULL"),
    )
    op.create_index(
        "ix_design_evidence_profile_signature",
        "design_evidence",
        ["profile_signature"],
    )
    op.create_index(
        "ix_design_evidence_challenge_design",
        "design_evidence",
        ["challenge_design_id"],
    )
    op.add_column(
        "design_tasks",
        sa.Column("current_design_evidence_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        "fk_design_tasks_current_design_evidence",
        "design_tasks",
        "design_evidence",
        ["current_design_evidence_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_design_tasks_current_design_evidence",
        "design_tasks",
        type_="foreignkey",
    )
    op.drop_column("design_tasks", "current_design_evidence_id")
    op.drop_index("ix_design_evidence_challenge_design", table_name="design_evidence")
    op.drop_index("ix_design_evidence_profile_signature", table_name="design_evidence")
    op.drop_index("uq_design_evidence_live_task", table_name="design_evidence")
    op.drop_table("design_evidence")
