"""add challenge_designs.legacy_grandfather for pre-rubric rows

Phase 2 (D2 = b+c): the new difficulty rubric (see
``skills/design-challenges/references/difficulty-rubric.md``) rejects
designs that do not declare 2+ techniques, a business scenario, or a
novelty field for expert. Existing rows persisted before the rubric
existed pass the old structural validator but would fail the new
alignment check. Operators flag those rows ``legacy_grandfather = TRUE``
so a future backfill / re-design script can pick them up explicitly.

New designs default to FALSE.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0011_designs_grandfather"
down_revision = "0010_build_attempts_idempotency"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "challenge_designs",
        sa.Column(
            "legacy_grandfather",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("challenge_designs", "legacy_grandfather")
