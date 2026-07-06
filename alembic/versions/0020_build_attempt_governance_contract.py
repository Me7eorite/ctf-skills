"""build attempt governance contract

Revision ID: 0020_build_attempt_contract
Revises: 0019_design_evidence
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0020_build_attempt_contract"
down_revision = "0019_design_evidence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "build_attempts",
        sa.Column("design_evidence_id", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "build_attempts",
        sa.Column("contract_sha256", sa.Text(), nullable=True),
    )
    op.create_foreign_key(
        "fk_build_attempts_design_evidence",
        "build_attempts",
        "design_evidence",
        ["design_evidence_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_build_attempts_design_evidence",
        "build_attempts",
        ["design_evidence_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_build_attempts_design_evidence", table_name="build_attempts")
    op.drop_constraint(
        "fk_build_attempts_design_evidence",
        "build_attempts",
        type_="foreignkey",
    )
    op.drop_column("build_attempts", "contract_sha256")
    op.drop_column("build_attempts", "design_evidence_id")
