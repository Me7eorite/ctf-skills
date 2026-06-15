"""Repository primitives for research-planning persistence."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.orm import Session

from domain import research as dto
from domain.research_validators import (
    ResearchValidationError,
    validate_category,
    validate_distribution,
    validate_finding,
)
from persistence.models import research as model


class ResearchRepository:
    """Typed CRUD/query primitives; callers own transaction boundaries."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def list_categories(self) -> list[dto.ChallengeCategory]:
        rows = self.session.scalars(
            sa.select(model.ChallengeCategory).order_by(model.ChallengeCategory.code)
        ).all()
        return [_category(row) for row in rows]

    def get_generation_request(self, request_id: UUID) -> dto.GenerationRequest | None:
        row = self.session.get(model.GenerationRequest, request_id)
        return _generation_request(row) if row else None

    def list_generation_requests(
        self,
        *,
        category: str | None = None,
        status: str | None = None,
    ) -> list[dto.GenerationRequest]:
        stmt = sa.select(model.GenerationRequest).order_by(model.GenerationRequest.created_at)
        if category is not None:
            stmt = stmt.where(model.GenerationRequest.category == category)
        if status is not None:
            stmt = stmt.where(model.GenerationRequest.status == status)
        return [_generation_request(row) for row in self.session.scalars(stmt)]

    def get_run(self, run_id: UUID) -> dto.ResearchRun | None:
        row = self.session.get(model.ResearchRun, run_id)
        return _run(row) if row else None

    def list_runs(
        self,
        *,
        status: str | None = None,
        claimed_by: str | None = None,
        generation_request_id: UUID | None = None,
        limit: int = 100,
    ) -> list[dto.ResearchRun]:
        stmt = sa.select(model.ResearchRun).order_by(model.ResearchRun.created_at).limit(limit)
        if status is not None:
            stmt = stmt.where(model.ResearchRun.status == status)
        if claimed_by is not None:
            stmt = stmt.where(model.ResearchRun.claimed_by == claimed_by)
        if generation_request_id is not None:
            stmt = stmt.where(model.ResearchRun.generation_request_id == generation_request_id)
        return [_run(row) for row in self.session.scalars(stmt)]

    def list_sources(self, run_id: UUID) -> list[dto.ResearchSource]:
        rows = self.session.scalars(
            sa.select(model.ResearchSource)
            .where(model.ResearchSource.research_run_id == run_id)
            .order_by(model.ResearchSource.fetched_at, model.ResearchSource.id)
        ).all()
        return [_source(row) for row in rows]

    def list_findings(self, run_id: UUID) -> list[dto.ResearchFinding]:
        rows = self.session.scalars(
            sa.select(model.ResearchFinding)
            .where(model.ResearchFinding.research_run_id == run_id)
            .order_by(model.ResearchFinding.label, model.ResearchFinding.id)
        ).all()
        return [_finding(row) for row in rows]

    def queue_stats(self) -> dict[str, Any]:
        counts = dict(
            self.session.execute(
                sa.select(model.ResearchRun.status, sa.func.count()).group_by(model.ResearchRun.status)
            ).all()
        )
        oldest_queued = self.session.scalar(
            sa.select(sa.func.min(model.ResearchRun.created_at)).where(model.ResearchRun.status == "queued")
        )
        now = _utcnow()
        near_expiry = self.session.scalars(
            sa.select(model.ResearchRun.id)
            .where(
                model.ResearchRun.status == "running",
                model.ResearchRun.lease_expires_at <= now + timedelta(seconds=60),
            )
            .order_by(model.ResearchRun.lease_expires_at)
        ).all()
        oldest_age = None
        if oldest_queued is not None:
            oldest_age = max(0.0, (now - oldest_queued).total_seconds())
        return {
            "queued": int(counts.get("queued", 0)),
            "running": int(counts.get("running", 0)),
            "completed": int(counts.get("completed", 0)),
            "failed": int(counts.get("failed", 0)),
            "oldest_queued_age_seconds": oldest_age,
            "runs_near_lease_expiry": list(near_expiry),
        }

    def create_generation_request(
        self,
        *,
        category: str,
        topic: str,
        target_count: int,
        difficulty_distribution: Mapping[str, int],
        seed_urls: Sequence[str] = (),
        max_attempts: int = 3,
        runtime_constraints: Mapping[str, Any] | None = None,
        status: str = "draft",
    ) -> dto.GenerationRequest:
        allowed_codes = [cat.code for cat in self.list_categories()]
        validate_category(category, allowed_codes)
        validate_distribution(target_count, difficulty_distribution)
        if max_attempts <= 0:
            raise ResearchValidationError(f"max_attempts must be positive, got {max_attempts}")
        row = model.GenerationRequest(
            id=uuid4(),
            category=category,
            topic=topic,
            target_count=target_count,
            difficulty_distribution=dict(difficulty_distribution),
            runtime_constraints=dict(runtime_constraints or {}),
            seed_urls=list(seed_urls),
            max_attempts=max_attempts,
            status=status,
        )
        self.session.add(row)
        self.session.flush()
        self.session.refresh(row)
        return _generation_request(row)

    def create_run(
        self,
        *,
        generation_request_id: UUID,
        parent_run_id: UUID | None = None,
        attempt: int = 1,
        status: str = "queued",
    ) -> dto.ResearchRun:
        if attempt <= 0:
            raise ResearchValidationError(f"attempt must be positive, got {attempt}")
        row = model.ResearchRun(
            id=uuid4(),
            generation_request_id=generation_request_id,
            parent_run_id=parent_run_id,
            attempt=attempt,
            status=status,
        )
        self.session.add(row)
        self.session.flush()
        self.session.refresh(row)
        return _run(row)

    def add_source(
        self,
        run_id: UUID,
        *,
        url: str,
        title: str,
        summary: str,
        content_hash: str,
        fetched_at: datetime,
        raw_text_path: str | None = None,
    ) -> dto.ResearchSource:
        row = model.ResearchSource(
            id=uuid4(),
            research_run_id=run_id,
            url=url,
            title=title,
            summary=summary,
            content_hash=content_hash,
            fetched_at=fetched_at,
            raw_text_path=raw_text_path,
        )
        self.session.add(row)
        self.session.flush()
        return _source(row)

    def create_finding(
        self,
        run_id: UUID,
        *,
        kind: str,
        label: str,
        summary: str,
        source_ids: Sequence[UUID],
    ) -> dto.ResearchFinding:
        validate_finding(kind, source_ids)
        rows = self.session.execute(
            sa.select(model.ResearchSource.id, model.ResearchSource.research_run_id).where(
                model.ResearchSource.id.in_(source_ids)
            )
        ).all()
        found = {row.id: row.research_run_id for row in rows}
        missing = [source_id for source_id in source_ids if source_id not in found]
        wrong_run = [source_id for source_id, found_run_id in found.items() if found_run_id != run_id]
        if missing:
            raise ResearchValidationError(f"source_id(s) do not exist: {missing}")
        if wrong_run:
            raise ResearchValidationError(f"source_id(s) do not belong to run {run_id}: {wrong_run}")

        finding = model.ResearchFinding(
            id=uuid4(),
            research_run_id=run_id,
            kind=kind,
            label=label,
            summary=summary,
        )
        self.session.add(finding)
        for source_id in source_ids:
            self.session.add(
                model.ResearchFindingSource(
                    finding_id=finding.id,
                    source_id=source_id,
                )
            )
        self.session.flush()
        return _finding(finding)

    def get_binding(self, role: str) -> dto.HermesProfileBinding | None:
        row = self.session.get(model.HermesProfileBinding, role)
        return _binding(row) if row else None

    def list_bindings(self) -> list[dto.HermesProfileBinding]:
        rows = self.session.scalars(
            sa.select(model.HermesProfileBinding).order_by(model.HermesProfileBinding.role)
        ).all()
        return [_binding(row) for row in rows]

    def upsert_binding(
        self,
        role: str,
        profile_name: str,
        description: str | None = None,
    ) -> dto.HermesProfileBinding:
        self._require_role(role)
        now = _utcnow()
        row = self.session.get(model.HermesProfileBinding, role)
        if row is None:
            row = model.HermesProfileBinding(
                role=role,
                profile_name=profile_name,
                description=description,
                status="enabled",
                updated_at=now,
            )
            self.session.add(row)
        else:
            row.profile_name = profile_name
            row.description = description
            row.updated_at = now
        self.session.flush()
        self.session.refresh(row)
        return _binding(row)

    def set_binding_status(self, role: str, status: str) -> dto.HermesProfileBinding:
        if status not in dto.BindingStatus:
            raise ResearchValidationError(
                f"binding status {status!r} is not allowed; allowed: {list(dto.BindingStatus)}"
            )
        row = self.session.get(model.HermesProfileBinding, role)
        if row is None:
            raise ResearchValidationError(f"binding role {role!r} does not exist")
        row.status = status
        row.updated_at = _utcnow()
        self.session.flush()
        self.session.refresh(row)
        return _binding(row)

    def touch_binding(
        self,
        role: str,
        *,
        last_used_at: datetime,
        last_used_run_id: UUID,
    ) -> None:
        row = self.session.get(model.HermesProfileBinding, role)
        if row is None:
            raise ResearchValidationError(f"binding role {role!r} does not exist")
        row.last_used_at = last_used_at
        row.last_used_run_id = last_used_run_id
        row.updated_at = _utcnow()
        self.session.flush()

    def _require_role(self, role: str) -> None:
        exists = self.session.scalar(sa.select(sa.literal(True)).where(model.AgentRole.code == role))
        if not exists:
            raise ResearchValidationError(f"agent role {role!r} does not exist")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _category(row: model.ChallengeCategory) -> dto.ChallengeCategory:
    return dto.ChallengeCategory(
        code=row.code,
        display_name=row.display_name,
        description=row.description,
    )


def _binding(row: model.HermesProfileBinding) -> dto.HermesProfileBinding:
    return dto.HermesProfileBinding(
        role=row.role,
        profile_name=row.profile_name,
        description=row.description,
        status=row.status,
        last_used_at=row.last_used_at,
        last_used_run_id=row.last_used_run_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _generation_request(row: model.GenerationRequest) -> dto.GenerationRequest:
    return dto.GenerationRequest(
        id=row.id,
        category=row.category,
        topic=row.topic,
        target_count=row.target_count,
        difficulty_distribution=dict(row.difficulty_distribution),
        runtime_constraints=dict(row.runtime_constraints),
        seed_urls=tuple(row.seed_urls),
        max_attempts=row.max_attempts,
        status=row.status,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _run(row: model.ResearchRun) -> dto.ResearchRun:
    return dto.ResearchRun(
        id=row.id,
        generation_request_id=row.generation_request_id,
        parent_run_id=row.parent_run_id,
        attempt=row.attempt,
        status=row.status,
        claimed_by=row.claimed_by,
        claim_token=row.claim_token,
        claimed_at=row.claimed_at,
        heartbeat_at=row.heartbeat_at,
        lease_expires_at=row.lease_expires_at,
        started_at=row.started_at,
        finished_at=row.finished_at,
        last_error=row.last_error,
        hermes_log_path=row.hermes_log_path,
        profile_name_used=row.profile_name_used,
        created_at=row.created_at,
        was_retried=None,
    )


def _source(row: model.ResearchSource) -> dto.ResearchSource:
    return dto.ResearchSource(
        id=row.id,
        research_run_id=row.research_run_id,
        url=row.url,
        title=row.title,
        summary=row.summary,
        content_hash=row.content_hash,
        fetched_at=row.fetched_at,
        raw_text_path=row.raw_text_path,
    )


def _finding(row: model.ResearchFinding) -> dto.ResearchFinding:
    return dto.ResearchFinding(
        id=row.id,
        research_run_id=row.research_run_id,
        kind=row.kind,
        label=row.label,
        summary=row.summary,
    )
