"""corpus governance

Revision ID: 0022_corpus_governance
Revises: 0021_artifact_observations
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0022_corpus_governance"
down_revision = "0021_artifact_observations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "corpus_batches",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("mode", sa.Text(), nullable=False),
        sa.Column(
            "category",
            sa.Text(),
            sa.ForeignKey("challenge_categories.code"),
            nullable=False,
        ),
        sa.Column("policy_version", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.Text(),
            server_default=sa.text("'draft'"),
            nullable=False,
        ),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("evaluation_started_at", sa.DateTime(timezone=True)),
        sa.Column("evaluated_at", sa.DateTime(timezone=True)),
        sa.Column("released_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "mode in ('shadow', 'trial', 'production')",
            name="ck_corpus_batches_mode",
        ),
        sa.CheckConstraint(
            "status in ('draft', 'evaluating', 'evaluated', 'released', 'retired')",
            name="ck_corpus_batches_status",
        ),
        sa.CheckConstraint(
            "policy_version > 0",
            name="ck_corpus_batches_policy_version_positive",
        ),
    )
    op.create_index(
        "ix_corpus_batches_category_status",
        "corpus_batches",
        ["category", "status"],
    )
    op.create_index(
        "ix_corpus_batches_mode_status",
        "corpus_batches",
        ["mode", "status"],
    )

    op.create_table(
        "corpus_batch_members",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "batch_id",
            sa.Uuid(),
            sa.ForeignKey("corpus_batches.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "build_attempt_id",
            sa.Uuid(),
            sa.ForeignKey("build_attempts.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "design_evidence_id",
            sa.Uuid(),
            sa.ForeignKey("design_evidence.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "artifact_observation_id",
            sa.Uuid(),
            sa.ForeignKey("artifact_observations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("fingerprint_version", sa.Integer(), nullable=False),
        sa.Column("fingerprints", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "fingerprint_version > 0",
            name="ck_corpus_batch_members_fingerprint_version_positive",
        ),
        sa.UniqueConstraint(
            "batch_id",
            "build_attempt_id",
            name="uq_corpus_batch_members_batch_attempt",
        ),
        sa.UniqueConstraint(
            "batch_id",
            "design_evidence_id",
            name="uq_corpus_batch_members_batch_evidence",
        ),
    )
    op.create_index(
        "ix_corpus_batch_members_batch",
        "corpus_batch_members",
        ["batch_id"],
    )
    op.create_index(
        "ix_corpus_batch_members_design_evidence",
        "corpus_batch_members",
        ["design_evidence_id"],
    )
    op.create_index(
        "ix_corpus_batch_members_artifact_observation",
        "corpus_batch_members",
        ["artifact_observation_id"],
    )

    op.create_table(
        "corpus_decisions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "batch_id",
            sa.Uuid(),
            sa.ForeignKey("corpus_batches.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "member_id",
            sa.Uuid(),
            sa.ForeignKey("corpus_batch_members.id", ondelete="CASCADE"),
        ),
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column("decision", sa.Text(), nullable=False),
        sa.Column("reasons", postgresql.JSONB(), nullable=False),
        sa.Column("policy_version", sa.Integer(), nullable=False),
        sa.Column(
            "is_current",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("superseded_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "scope in ('member', 'aggregate')",
            name="ck_corpus_decisions_scope",
        ),
        sa.CheckConstraint(
            "decision in ('passed', 'review_required', 'blocked')",
            name="ck_corpus_decisions_decision",
        ),
        sa.CheckConstraint(
            "policy_version > 0",
            name="ck_corpus_decisions_policy_version_positive",
        ),
        sa.CheckConstraint(
            "(scope = 'member' AND member_id IS NOT NULL) OR "
            "(scope = 'aggregate' AND member_id IS NULL)",
            name="ck_corpus_decisions_scope_member",
        ),
    )
    op.create_index(
        "uq_corpus_decisions_current_member",
        "corpus_decisions",
        ["member_id"],
        unique=True,
        postgresql_where=sa.text("is_current AND member_id IS NOT NULL"),
    )
    op.create_index(
        "uq_corpus_decisions_current_aggregate",
        "corpus_decisions",
        ["batch_id"],
        unique=True,
        postgresql_where=sa.text("is_current AND scope = 'aggregate'"),
    )
    op.create_index(
        "ix_corpus_decisions_batch_scope",
        "corpus_decisions",
        ["batch_id", "scope"],
    )

    op.create_table(
        "observation_review_decisions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "artifact_observation_id",
            sa.Uuid(),
            sa.ForeignKey("artifact_observations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("decision", sa.Text(), nullable=False),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "decision in ('accepted', 'rejected')",
            name="ck_observation_review_decisions_decision",
        ),
    )
    op.create_index(
        "ix_observation_review_decisions_observation",
        "observation_review_decisions",
        ["artifact_observation_id", "created_at"],
    )

    op.create_table(
        "corpus_review_decisions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "corpus_decision_id",
            sa.Uuid(),
            sa.ForeignKey("corpus_decisions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("decision", sa.Text(), nullable=False),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "decision in ('approved', 'rejected')",
            name="ck_corpus_review_decisions_decision",
        ),
    )
    op.create_index(
        "ix_corpus_review_decisions_decision",
        "corpus_review_decisions",
        ["corpus_decision_id", "created_at"],
    )

    op.create_table(
        "corpus_history_entries",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("challenge_id", sa.Text(), nullable=False),
        sa.Column(
            "category",
            sa.Text(),
            sa.ForeignKey("challenge_categories.code"),
            nullable=False,
        ),
        sa.Column(
            "design_evidence_id",
            sa.Uuid(),
            sa.ForeignKey("design_evidence.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "build_attempt_id",
            sa.Uuid(),
            sa.ForeignKey("build_attempts.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "artifact_observation_id",
            sa.Uuid(),
            sa.ForeignKey("artifact_observations.id", ondelete="SET NULL"),
        ),
        sa.Column("fingerprint_version", sa.Integer(), nullable=False),
        sa.Column("fingerprints", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("audit_reason", sa.Text()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "fingerprint_version > 0",
            name="ck_corpus_history_entries_fingerprint_version_positive",
        ),
        sa.CheckConstraint(
            "status in ('published', 'retired')",
            name="ck_corpus_history_entries_status",
        ),
    )
    op.create_index(
        "ix_corpus_history_entries_category_status",
        "corpus_history_entries",
        ["category", "status"],
    )
    op.create_index(
        "ix_corpus_history_entries_challenge_id",
        "corpus_history_entries",
        ["challenge_id"],
    )

    op.create_table(
        "corpus_matches",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "batch_id",
            sa.Uuid(),
            sa.ForeignKey("corpus_batches.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "member_id",
            sa.Uuid(),
            sa.ForeignKey("corpus_batch_members.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "compared_member_id",
            sa.Uuid(),
            sa.ForeignKey("corpus_batch_members.id", ondelete="CASCADE"),
        ),
        sa.Column(
            "compared_history_entry_id",
            sa.Uuid(),
            sa.ForeignKey("corpus_history_entries.id", ondelete="CASCADE"),
        ),
        sa.Column("fingerprint_type", sa.Text(), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("threshold", sa.Float(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint("score >= 0 AND score <= 1", name="ck_corpus_matches_score"),
        sa.CheckConstraint(
            "threshold >= 0 AND threshold <= 1",
            name="ck_corpus_matches_threshold",
        ),
        sa.CheckConstraint(
            "fingerprint_type in ('semantic', 'solve', 'implementation', 'combined', "
            "'source', 'solver', 'intended_path')",
            name="ck_corpus_matches_fingerprint_type",
        ),
        sa.CheckConstraint(
            "compared_member_id IS NOT NULL OR compared_history_entry_id IS NOT NULL",
            name="ck_corpus_matches_compared_target",
        ),
    )
    op.create_index(
        "ix_corpus_matches_member_type",
        "corpus_matches",
        ["member_id", "fingerprint_type"],
    )
    op.create_index(
        "ix_corpus_matches_batch_score",
        "corpus_matches",
        ["batch_id", sa.text("score DESC")],
    )


def downgrade() -> None:
    op.drop_index("ix_corpus_matches_batch_score", table_name="corpus_matches")
    op.drop_index("ix_corpus_matches_member_type", table_name="corpus_matches")
    op.drop_table("corpus_matches")
    op.drop_index("ix_corpus_history_entries_challenge_id", table_name="corpus_history_entries")
    op.drop_index("ix_corpus_history_entries_category_status", table_name="corpus_history_entries")
    op.drop_table("corpus_history_entries")
    op.drop_index("ix_corpus_review_decisions_decision", table_name="corpus_review_decisions")
    op.drop_table("corpus_review_decisions")
    op.drop_index(
        "ix_observation_review_decisions_observation",
        table_name="observation_review_decisions",
    )
    op.drop_table("observation_review_decisions")
    op.drop_index("ix_corpus_decisions_batch_scope", table_name="corpus_decisions")
    op.drop_index("uq_corpus_decisions_current_aggregate", table_name="corpus_decisions")
    op.drop_index("uq_corpus_decisions_current_member", table_name="corpus_decisions")
    op.drop_table("corpus_decisions")
    op.drop_index(
        "ix_corpus_batch_members_artifact_observation",
        table_name="corpus_batch_members",
    )
    op.drop_index(
        "ix_corpus_batch_members_design_evidence",
        table_name="corpus_batch_members",
    )
    op.drop_index("ix_corpus_batch_members_batch", table_name="corpus_batch_members")
    op.drop_table("corpus_batch_members")
    op.drop_index("ix_corpus_batches_mode_status", table_name="corpus_batches")
    op.drop_index("ix_corpus_batches_category_status", table_name="corpus_batches")
    op.drop_table("corpus_batches")
