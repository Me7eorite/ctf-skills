"""SQLAlchemy models for research-planning persistence."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from persistence.models.base import Base


def _strip_nul(value: str | None) -> str | None:
    """剔除 NUL (0x00) 字节。

    PostgreSQL 的 text/jsonb 不允许存储 NUL，而 agent 自由文本里偶尔会带上
    （例如描述 PE 签名 ``PE\\x00\\x00`` 或抓取到的二进制网页内容）。在写入前清洗
    可以避免 ``psycopg.DataError`` 直接拖垮整个 worker。
    """
    if isinstance(value, str) and "\x00" in value:
        return value.replace("\x00", "")
    return value


UuidPk = Annotated[UUID, mapped_column(sa.Uuid(), primary_key=True)]
TextPk = Annotated[str, mapped_column(sa.Text(), primary_key=True)]
CreatedAt = Annotated[datetime, mapped_column(sa.DateTime(timezone=True), server_default=sa.func.now())]
UpdatedAt = Annotated[datetime, mapped_column(sa.DateTime(timezone=True), server_default=sa.func.now())]


generation_request_status = sa.Enum(
    "draft",
    "researching",
    "researched",
    "failed",
    name="generation_request_status",
    create_type=False,
)
research_run_status = sa.Enum(
    "queued",
    "running",
    "completed",
    "failed",
    name="research_run_status",
    create_type=False,
)
research_finding_kind = sa.Enum(
    "technique",
    "variant",
    "scenario",
    "prerequisite",
    name="research_finding_kind",
    create_type=False,
)


class ChallengeCategory(Base):
    __tablename__ = "challenge_categories"

    code: Mapped[TextPk]
    display_name: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    description: Mapped[str | None] = mapped_column(sa.Text())


class AgentRole(Base):
    __tablename__ = "agent_roles"

    code: Mapped[TextPk]
    display_name: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    description: Mapped[str | None] = mapped_column(sa.Text())


class HermesProfileBinding(Base):
    __tablename__ = "hermes_profile_bindings"
    __table_args__ = (
        sa.CheckConstraint(
            "status in ('enabled', 'disabled')",
            name="ck_hermes_profile_bindings_status",
        ),
    )

    role: Mapped[str] = mapped_column(sa.Text(), sa.ForeignKey("agent_roles.code"), primary_key=True)
    profile_name: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    description: Mapped[str | None] = mapped_column(sa.Text())
    status: Mapped[str] = mapped_column(sa.Text(), nullable=False, server_default=sa.text("'enabled'"))
    last_used_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    last_used_run_id: Mapped[UUID | None] = mapped_column(
        sa.Uuid(),
        sa.ForeignKey("research_runs.id", ondelete="SET NULL"),
    )
    created_at: Mapped[CreatedAt]
    updated_at: Mapped[UpdatedAt]

    role_ref: Mapped[AgentRole] = relationship()
    last_used_run: Mapped[ResearchRun | None] = relationship(foreign_keys=[last_used_run_id])


class GenerationRequest(Base):
    __tablename__ = "generation_requests"
    __table_args__ = (
        sa.CheckConstraint("target_count > 0", name="ck_generation_requests_target_count_positive"),
        sa.CheckConstraint("max_attempts > 0", name="ck_generation_requests_max_attempts_positive"),
        sa.Index("ix_generation_requests_category_status", "category", "status"),
        sa.Index("ix_generation_requests_idempotency", "idempotency_key", "created_at"),
    )

    id: Mapped[UuidPk]
    category: Mapped[str] = mapped_column(sa.Text(), sa.ForeignKey("challenge_categories.code"), nullable=False)
    topic: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    target_count: Mapped[int] = mapped_column(sa.Integer(), nullable=False)
    difficulty_distribution: Mapped[dict[str, int]] = mapped_column(JSONB, nullable=False)
    runtime_constraints: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=sa.text("'{}'::jsonb"),
    )
    seed_urls: Mapped[list[str]] = mapped_column(JSONB, nullable=False, server_default=sa.text("'[]'::jsonb"))
    max_attempts: Mapped[int] = mapped_column(sa.Integer(), nullable=False, server_default=sa.text("3"))
    status: Mapped[str] = mapped_column(
        generation_request_status,
        nullable=False,
        server_default=sa.text("'draft'"),
    )
    idempotency_key: Mapped[str | None] = mapped_column(sa.Text())
    request_fingerprint: Mapped[str | None] = mapped_column(sa.Text())
    created_at: Mapped[CreatedAt]
    updated_at: Mapped[UpdatedAt]

    category_ref: Mapped[ChallengeCategory] = relationship()


class ResearchRun(Base):
    __tablename__ = "research_runs"
    __table_args__ = (
        sa.CheckConstraint("attempt > 0", name="ck_research_runs_attempt_positive"),
        sa.UniqueConstraint(
            "generation_request_id",
            "attempt",
            name="uq_research_runs_generation_request_attempt",
        ),
        sa.Index("ix_research_runs_generation_request_id", "generation_request_id"),
        sa.Index(
            "ix_research_runs_status_lease_expires_at_active",
            "status",
            "lease_expires_at",
            postgresql_where=sa.text("status IN ('queued', 'running')"),
        ),
        sa.Index("ix_research_runs_claimed_by", "claimed_by"),
        sa.Index("ix_research_runs_claim_token", "claim_token"),
    )

    id: Mapped[UuidPk]
    generation_request_id: Mapped[UUID] = mapped_column(
        sa.Uuid(),
        sa.ForeignKey("generation_requests.id", ondelete="CASCADE"),
        nullable=False,
    )
    parent_run_id: Mapped[UUID | None] = mapped_column(
        sa.Uuid(),
        sa.ForeignKey("research_runs.id", ondelete="SET NULL"),
    )
    attempt: Mapped[int] = mapped_column(sa.Integer(), nullable=False, server_default=sa.text("1"))
    status: Mapped[str] = mapped_column(research_run_status, nullable=False, server_default=sa.text("'queued'"))
    claimed_by: Mapped[str | None] = mapped_column(sa.Text())
    claim_token: Mapped[UUID | None] = mapped_column(sa.Uuid())
    claimed_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    lease_expires_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    trial_only: Mapped[bool] = mapped_column(
        sa.Boolean(),
        nullable=False,
        default=False,
        server_default=sa.text("false"),
    )
    last_error: Mapped[str | None] = mapped_column(sa.Text())
    hermes_log_path: Mapped[str | None] = mapped_column(sa.Text())
    profile_name_used: Mapped[str | None] = mapped_column(sa.Text())
    created_at: Mapped[CreatedAt]

    generation_request: Mapped[GenerationRequest] = relationship()
    parent_run: Mapped[ResearchRun | None] = relationship(remote_side="ResearchRun.id")


class ResearchSource(Base):
    __tablename__ = "research_sources"
    __table_args__ = (
        sa.UniqueConstraint(
            "research_run_id",
            "content_hash",
            name="uq_research_sources_run_hash",
        ),
    )

    id: Mapped[UuidPk]
    research_run_id: Mapped[UUID] = mapped_column(
        sa.Uuid(),
        sa.ForeignKey("research_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    url: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    title: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    summary: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    content_hash: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    raw_text_path: Mapped[str | None] = mapped_column(sa.Text())

    research_run: Mapped[ResearchRun] = relationship()

    @validates("url", "title", "summary", "content_hash", "raw_text_path")
    def _sanitize_text(self, _key: str, value: str | None) -> str | None:
        return _strip_nul(value)


class ResearchFinding(Base):
    __tablename__ = "research_findings"
    __table_args__ = (
        sa.Index("ix_research_findings_research_run_id", "research_run_id"),
    )

    id: Mapped[UuidPk]
    research_run_id: Mapped[UUID] = mapped_column(
        sa.Uuid(),
        sa.ForeignKey("research_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    kind: Mapped[str] = mapped_column(research_finding_kind, nullable=False)
    label: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    summary: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    technique_family: Mapped[str | None] = mapped_column(sa.Text())

    research_run: Mapped[ResearchRun] = relationship()

    @validates("label", "summary", "technique_family")
    def _sanitize_text(self, _key: str, value: str | None) -> str | None:
        return _strip_nul(value)


class ResearchFindingSource(Base):
    __tablename__ = "research_finding_sources"

    finding_id: Mapped[UUID] = mapped_column(
        sa.Uuid(),
        sa.ForeignKey("research_findings.id", ondelete="CASCADE"),
        primary_key=True,
    )
    source_id: Mapped[UUID] = mapped_column(
        sa.Uuid(),
        sa.ForeignKey("research_sources.id", ondelete="CASCADE"),
        primary_key=True,
    )

    finding: Mapped[ResearchFinding] = relationship()
    source: Mapped[ResearchSource] = relationship()
