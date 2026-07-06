"""design profile reservations and ledgers

Revision ID: 0018_design_profile_reservations
Revises: 0017_research_run_trial_only
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0018_design_profile_reservations"
down_revision = "0017_research_run_trial_only"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "design_profile_ledgers",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "category",
            sa.Text(),
            sa.ForeignKey("challenge_categories.code"),
            nullable=False,
        ),
        sa.Column("policy_version", sa.Integer(), nullable=False),
        sa.Column(
            "ledger_version",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint("ledger_version >= 0", name="ck_design_profile_ledgers_version_nonnegative"),
        sa.CheckConstraint("policy_version > 0", name="ck_design_profile_ledgers_policy_version_positive"),
        sa.UniqueConstraint("category", name="uq_design_profile_ledgers_category"),
    )

    op.create_table(
        "design_profile_reservations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("design_task_id", sa.Uuid(), sa.ForeignKey("design_tasks.id", ondelete="SET NULL"), nullable=True),
        sa.Column("generation_request_id", sa.Uuid(), sa.ForeignKey("generation_requests.id", ondelete="CASCADE"), nullable=False),
        sa.Column("reservation_version", sa.Integer(), nullable=False),
        sa.Column("profile", sa.dialects.postgresql.JSONB(), nullable=False),
        sa.Column("profile_signature", sa.Text(), nullable=False),
        sa.Column("occupancy_scope", sa.Text()),
        sa.Column("exclusive_signature_key", sa.Text()),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("taxonomy_version", sa.Integer(), nullable=False),
        sa.Column("policy_version", sa.Integer(), nullable=False),
        sa.Column("ledger_version", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("committed_at", sa.DateTime(timezone=True)),
        sa.Column("released_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "state in ('reserved', 'committed', 'released')",
            name="ck_design_profile_reservations_state",
        ),
        sa.CheckConstraint(
            "reservation_version > 0",
            name="ck_design_profile_reservations_version_positive",
        ),
        sa.CheckConstraint(
            "policy_version > 0",
            name="ck_design_profile_reservations_policy_version_positive",
        ),
        sa.CheckConstraint(
            "taxonomy_version > 0",
            name="ck_design_profile_reservations_taxonomy_version_positive",
        ),
        sa.CheckConstraint(
            "ledger_version >= 0",
            name="ck_design_profile_reservations_ledger_version_nonnegative",
        ),
        sa.UniqueConstraint(
            "design_task_id",
            "reservation_version",
            name="uq_design_profile_reservations_task_version",
        ),
        sa.Index(
            "uq_design_profile_reservations_active_task",
            "design_task_id",
            unique=True,
            postgresql_where=sa.text("state IN ('reserved', 'committed')"),
        ),
        sa.Index(
            "uq_design_profile_reservations_active_scoped_key",
            "policy_version",
            "occupancy_scope",
            "exclusive_signature_key",
            unique=True,
            postgresql_where=sa.text(
                "state IN ('reserved', 'committed') "
                "AND occupancy_scope IS NOT NULL "
                "AND exclusive_signature_key IS NOT NULL"
            ),
        ),
    )

    op.add_column(
        "design_tasks",
        sa.Column("current_reservation_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        "fk_design_tasks_current_reservation",
        "design_tasks",
        "design_profile_reservations",
        ["current_reservation_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_design_tasks_current_reservation",
        "design_tasks",
        type_="foreignkey",
    )
    op.drop_column("design_tasks", "current_reservation_id")
    op.drop_table("design_profile_reservations")
    op.drop_table("design_profile_ledgers")
