"""make research source content hash unique per run"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0009_research_hash_unique"
down_revision = "0008_generation_idempotency"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    duplicates = connection.scalar(
        sa.text(
            """
            SELECT COUNT(*) FROM (
              SELECT research_run_id, content_hash, COUNT(*) AS c
              FROM research_sources
              GROUP BY research_run_id, content_hash
              HAVING COUNT(*) > 1
            ) dup
            """
        )
    )
    if duplicates:
        raise RuntimeError("run tools/scripts/dedup_research_sources.py --apply first")
    op.drop_index("ix_research_sources_run_hash", table_name="research_sources")
    op.create_unique_constraint(
        "uq_research_sources_run_hash",
        "research_sources",
        ["research_run_id", "content_hash"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_research_sources_run_hash", "research_sources", type_="unique")
    op.create_index(
        "ix_research_sources_run_hash",
        "research_sources",
        ["research_run_id", "content_hash"],
        unique=False,
    )
