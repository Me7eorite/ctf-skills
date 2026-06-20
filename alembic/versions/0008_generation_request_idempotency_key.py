"""add generation request idempotency fields"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0008_generation_idempotency"
down_revision = "0007_resource_deletion_counter"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("generation_requests", sa.Column("idempotency_key", sa.Text(), nullable=True))
    op.add_column("generation_requests", sa.Column("request_fingerprint", sa.Text(), nullable=True))
    op.create_index(
        "ix_generation_requests_idempotency",
        "generation_requests",
        ["idempotency_key", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_generation_requests_idempotency", table_name="generation_requests")
    op.drop_column("generation_requests", "request_fingerprint")
    op.drop_column("generation_requests", "idempotency_key")
