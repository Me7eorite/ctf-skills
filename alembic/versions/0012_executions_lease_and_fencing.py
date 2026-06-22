"""executions table with lease/fencing, feedback snapshots, revalidation events

add-execution-lease-and-fencing (split-plan proposal #3): build_attempts becomes
a per-build-session container; each run is an executions row with a lease +
fencing token + iteration chain.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0012_executions_lease_fencing"
down_revision = "0011_designs_grandfather"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "build_feedback_snapshots",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "build_attempt_id",
            sa.Uuid(),
            sa.ForeignKey("build_attempts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("summary", sa.Text()),
        sa.Column(
            "requested_changes",
            sa.dialects.postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "preserve",
            sa.dialects.postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "forbid",
            sa.dialects.postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("reviewer", sa.Text()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("id", "build_attempt_id", name="uq_feedback_id_attempt"),
    )

    op.create_table(
        "executions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "build_attempt_id",
            sa.Uuid(),
            sa.ForeignKey("build_attempts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("parent_execution_id", sa.Uuid()),
        sa.Column("iteration_no", sa.Integer(), nullable=False),
        sa.Column("execution_kind", sa.Text(), nullable=False),
        sa.Column(
            "execution_mode",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'standard'"),
        ),
        sa.Column("feedback_snapshot_id", sa.Uuid()),
        sa.Column("worker_id", sa.Text()),
        sa.Column("claim_token", sa.Uuid()),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True)),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("exit_class", sa.Text()),
        sa.Column("error", sa.Text()),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "execution_kind in ('initial', 'retry', 'revision')",
            name="ck_executions_kind",
        ),
        sa.CheckConstraint(
            "execution_mode in ('standard', 'clean')",
            name="ck_executions_mode",
        ),
        sa.CheckConstraint(
            "status in ('queued', 'claimed', 'running', 'succeeded', 'failed', 'lost')",
            name="ck_executions_status",
        ),
        sa.CheckConstraint(
            "execution_mode <> 'clean' OR execution_kind = 'retry'",
            name="ck_executions_clean_requires_retry",
        ),
        sa.CheckConstraint(
            "execution_kind = 'initial' OR parent_execution_id IS NOT NULL",
            name="ck_executions_parent_required",
        ),
        sa.CheckConstraint(
            "execution_kind <> 'revision' OR feedback_snapshot_id IS NOT NULL",
            name="ck_executions_revision_feedback",
        ),
        sa.CheckConstraint(
            "(status = 'queued' AND claim_token IS NULL AND lease_expires_at IS NULL)"
            " OR (status IN ('claimed', 'running')"
            "     AND claim_token IS NOT NULL AND lease_expires_at IS NOT NULL)"
            " OR status IN ('succeeded', 'failed', 'lost')",
            name="ck_executions_claim_fields",
        ),
        sa.UniqueConstraint(
            "build_attempt_id", "iteration_no", name="uq_executions_attempt_iter"
        ),
        sa.UniqueConstraint("id", "build_attempt_id", name="uq_executions_id_attempt"),
        sa.ForeignKeyConstraint(
            ["parent_execution_id", "build_attempt_id"],
            ["executions.id", "executions.build_attempt_id"],
            name="fk_executions_parent_same_attempt",
        ),
        sa.ForeignKeyConstraint(
            ["feedback_snapshot_id", "build_attempt_id"],
            [
                "build_feedback_snapshots.id",
                "build_feedback_snapshots.build_attempt_id",
            ],
            name="fk_executions_feedback_same_attempt",
        ),
    )
    op.create_index(
        "one_nonterminal_execution_per_attempt",
        "executions",
        ["build_attempt_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'claimed', 'running')"),
    )
    op.create_index(
        "ix_executions_lease",
        "executions",
        ["lease_expires_at"],
        postgresql_where=sa.text("status IN ('claimed', 'running')"),
    )
    op.create_index(
        "ix_executions_attempt_iter",
        "executions",
        ["build_attempt_id", sa.text("iteration_no DESC")],
    )

    op.create_table(
        "revalidation_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "execution_id",
            sa.Uuid(),
            sa.ForeignKey("executions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("check_name", sa.Text(), nullable=False),
        sa.Column(
            "result",
            sa.dialects.postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("actor", sa.Text()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    for col in (
        "current_execution_id",
        "latest_execution_id",
        "successful_execution_id",
    ):
        op.add_column("build_attempts", sa.Column(col, sa.Uuid(), nullable=True))
    for col, name in (
        ("current_execution_id", "fk_build_attempts_current_execution"),
        ("latest_execution_id", "fk_build_attempts_latest_execution"),
        ("successful_execution_id", "fk_build_attempts_successful_execution"),
    ):
        op.create_foreign_key(
            name,
            "build_attempts",
            "executions",
            [col, "id"],
            ["id", "build_attempt_id"],
        )


def downgrade() -> None:
    for name in (
        "fk_build_attempts_current_execution",
        "fk_build_attempts_latest_execution",
        "fk_build_attempts_successful_execution",
    ):
        op.drop_constraint(name, "build_attempts", type_="foreignkey")
    for col in (
        "successful_execution_id",
        "latest_execution_id",
        "current_execution_id",
    ):
        op.drop_column("build_attempts", col)
    op.drop_table("revalidation_events")
    op.drop_index("ix_executions_attempt_iter", table_name="executions")
    op.drop_index("ix_executions_lease", table_name="executions")
    op.drop_index("one_nonterminal_execution_per_attempt", table_name="executions")
    op.drop_table("executions")
    op.drop_table("build_feedback_snapshots")
