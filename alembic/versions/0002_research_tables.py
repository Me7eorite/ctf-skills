"""research tables

Revision ID: 0002_research_tables
Revises: 0001_baseline
Create Date: 2026-06-15

The challenge_categories lookup table is initially seeded to match
core.queue.SUPPORTED_CATEGORIES. Category is intentionally not a PostgreSQL
enum so operators can add research-only categories without a schema migration.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0002_research_tables"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


generation_request_status = postgresql.ENUM(
    "draft",
    "researching",
    "researched",
    "failed",
    name="generation_request_status",
)
research_run_status = postgresql.ENUM(
    "queued",
    "running",
    "completed",
    "failed",
    name="research_run_status",
)
research_finding_kind = postgresql.ENUM(
    "technique",
    "variant",
    "scenario",
    "prerequisite",
    name="research_finding_kind",
)


def upgrade() -> None:
    bind = op.get_bind()
    generation_request_status.create(bind)
    research_run_status.create(bind)
    research_finding_kind.create(bind)

    op.create_table(
        "challenge_categories",
        sa.Column("code", sa.Text(), primary_key=True),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
    )
    challenge_categories = sa.table(
        "challenge_categories",
        sa.column("code", sa.Text()),
        sa.column("display_name", sa.Text()),
        sa.column("description", sa.Text()),
    )
    op.bulk_insert(
        challenge_categories,
        [
            {
                "code": "web",
                "display_name": "Web 安全",
                "description": "基于 HTTP/Web 服务的题目",
            },
            {
                "code": "pwn",
                "display_name": "Pwn",
                "description": "二进制利用题目",
            },
            {
                "code": "re",
                "display_name": "Reverse",
                "description": "逆向工程题目",
            },
        ],
    )

    op.create_table(
        "agent_roles",
        sa.Column("code", sa.Text(), primary_key=True),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
    )
    agent_roles = sa.table(
        "agent_roles",
        sa.column("code", sa.Text()),
        sa.column("display_name", sa.Text()),
        sa.column("description", sa.Text()),
    )
    op.bulk_insert(
        agent_roles,
        [
            {
                "code": "research",
                "display_name": "研究 Agent",
                "description": "从话题、种子 URL 中调研并提取技术发现",
            },
        ],
    )

    op.create_table(
        "generation_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "category",
            sa.Text(),
            sa.ForeignKey("challenge_categories.code"),
            nullable=False,
        ),
        sa.Column("topic", sa.Text(), nullable=False),
        sa.Column("target_count", sa.Integer(), nullable=False),
        sa.Column("difficulty_distribution", postgresql.JSONB(), nullable=False),
        sa.Column(
            "runtime_constraints",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "seed_urls",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "max_attempts",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("3"),
        ),
        sa.Column(
            "status",
            postgresql.ENUM(name="generation_request_status", create_type=False),
            nullable=False,
            server_default=sa.text("'draft'"),
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint("target_count > 0", name="ck_generation_requests_target_count_positive"),
        sa.CheckConstraint("max_attempts > 0", name="ck_generation_requests_max_attempts_positive"),
    )
    op.create_index(
        "ix_generation_requests_category_status",
        "generation_requests",
        ["category", "status"],
    )

    op.create_table(
        "research_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "generation_request_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("generation_requests.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "parent_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("research_runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column(
            "status",
            postgresql.ENUM(name="research_run_status", create_type=False),
            nullable=False,
            server_default=sa.text("'queued'"),
        ),
        sa.Column("claimed_by", sa.Text(), nullable=True),
        sa.Column("claim_token", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("claimed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("lease_expires_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("started_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("finished_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("hermes_log_path", sa.Text(), nullable=True),
        sa.Column("profile_name_used", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint("attempt > 0", name="ck_research_runs_attempt_positive"),
        sa.UniqueConstraint(
            "generation_request_id",
            "attempt",
            name="uq_research_runs_generation_request_attempt",
        ),
    )
    op.create_index(
        "ix_research_runs_generation_request_id",
        "research_runs",
        ["generation_request_id"],
    )
    op.create_index(
        "ix_research_runs_status_lease_expires_at_active",
        "research_runs",
        ["status", "lease_expires_at"],
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )
    op.create_index("ix_research_runs_claimed_by", "research_runs", ["claimed_by"])
    op.create_index("ix_research_runs_claim_token", "research_runs", ["claim_token"])

    op.create_table(
        "hermes_profile_bindings",
        sa.Column(
            "role",
            sa.Text(),
            sa.ForeignKey("agent_roles.code"),
            primary_key=True,
        ),
        sa.Column("profile_name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'enabled'"),
        ),
        sa.Column("last_used_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "last_used_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("research_runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "status in ('enabled', 'disabled')",
            name="ck_hermes_profile_bindings_status",
        ),
    )
    hermes_profile_bindings = sa.table(
        "hermes_profile_bindings",
        sa.column("role", sa.Text()),
        sa.column("profile_name", sa.Text()),
        sa.column("description", sa.Text()),
        sa.column("status", sa.Text()),
    )
    op.bulk_insert(
        hermes_profile_bindings,
        [
            {
                "role": "research",
                "profile_name": "default",
                "description": "默认绑定，operator 可改",
                "status": "enabled",
            },
        ],
    )

    op.create_table(
        "research_sources",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "research_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("research_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column("fetched_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("raw_text_path", sa.Text(), nullable=True),
    )
    op.create_index(
        "ix_research_sources_run_hash",
        "research_sources",
        ["research_run_id", "content_hash"],
    )

    op.create_table(
        "research_findings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "research_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("research_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "kind",
            postgresql.ENUM(name="research_finding_kind", create_type=False),
            nullable=False,
        ),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
    )
    op.create_index(
        "ix_research_findings_research_run_id",
        "research_findings",
        ["research_run_id"],
    )

    op.create_table(
        "research_finding_sources",
        sa.Column(
            "finding_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("research_findings.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "source_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("research_sources.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("finding_id", "source_id"),
    )


def downgrade() -> None:
    bind = op.get_bind()

    op.drop_table("research_finding_sources")
    op.drop_table("research_findings")
    op.drop_table("research_sources")
    op.drop_table("hermes_profile_bindings")
    op.drop_table("research_runs")
    op.drop_table("generation_requests")
    op.drop_table("agent_roles")
    op.drop_table("challenge_categories")

    research_finding_kind.drop(bind)
    research_run_status.drop(bind)
    generation_request_status.drop(bind)
