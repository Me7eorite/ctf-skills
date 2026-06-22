"""add build_attempts idempotency_key for clean rebuild"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0010_build_attempts_idempotency"
down_revision = "0009_research_hash_unique"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "build_attempts",
        sa.Column("idempotency_key", sa.Text(), nullable=True),
    )
    op.create_index(
        "uq_build_attempts_idempotency_key",
        "build_attempts",
        ["idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_build_attempts_idempotency_key", table_name="build_attempts")
    op.drop_column("build_attempts", "idempotency_key")
