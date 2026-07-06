"""artifact observations

Revision ID: 0021_artifact_observations
Revises: 0020_build_attempt_contract
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0021_artifact_observations"
down_revision = "0020_build_attempt_contract"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "build_attempts",
        sa.Column("artifact_observation_id", sa.Uuid(), nullable=True),
    )
    op.create_table(
        "artifact_observations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "build_attempt_id",
            sa.Uuid(),
            sa.ForeignKey("build_attempts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("observation_version", sa.Integer(), nullable=False),
        sa.Column(
            "design_evidence_id",
            sa.Uuid(),
            sa.ForeignKey("design_evidence.id", ondelete="SET NULL"),
        ),
        sa.Column("contract_sha256", sa.Text(), nullable=False),
        sa.Column("artifact_manifest_sha256", sa.Text(), nullable=False),
        sa.Column("observed_profile", postgresql.JSONB(), nullable=False),
        sa.Column("contract_checks", postgresql.JSONB(), nullable=False),
        sa.Column("negative_test_results", postgresql.JSONB(), nullable=False),
        sa.Column("fingerprints", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column(
            "is_current",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("superseded_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "status in ('passed', 'failed', 'inconclusive')",
            name="ck_artifact_observations_status",
        ),
        sa.CheckConstraint(
            "observation_version > 0",
            name="ck_artifact_observations_version_positive",
        ),
        sa.UniqueConstraint(
            "build_attempt_id",
            "observation_version",
            name="uq_artifact_observations_attempt_version",
        ),
    )
    op.create_index(
        "uq_artifact_observations_current_attempt",
        "artifact_observations",
        ["build_attempt_id"],
        unique=True,
        postgresql_where=sa.text("is_current"),
    )
    op.create_index(
        "ix_artifact_observations_contract_sha256",
        "artifact_observations",
        ["contract_sha256"],
    )
    op.create_index(
        "ix_artifact_observations_artifact_manifest_sha256",
        "artifact_observations",
        ["artifact_manifest_sha256"],
    )
    op.create_index(
        "ix_artifact_observations_design_evidence",
        "artifact_observations",
        ["design_evidence_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_artifact_observations_design_evidence", table_name="artifact_observations")
    op.drop_index("ix_artifact_observations_artifact_manifest_sha256", table_name="artifact_observations")
    op.drop_index("ix_artifact_observations_contract_sha256", table_name="artifact_observations")
    op.drop_index("uq_artifact_observations_current_attempt", table_name="artifact_observations")
    op.drop_table("artifact_observations")
    op.drop_column("build_attempts", "artifact_observation_id")
