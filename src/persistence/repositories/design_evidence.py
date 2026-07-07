"""Persistence primitives for committed DesignEvidence rows."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.orm import Session

from core.clock import utcnow as _utcnow
from domain import design_evidence as dto
from persistence.models import challenge_designs as model
from persistence.models import design_tasks as task_model


class DesignEvidencePersistenceError(ValueError):
    """Raised when DesignEvidence cannot be persisted in the requested state."""


class DesignEvidenceRepository:
    """Typed CRUD/query primitives for DesignEvidence."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def latest_live_for_task(self, design_task_id: UUID) -> dto.DesignEvidence | None:
        row = self.session.scalars(
            sa.select(model.DesignEvidence)
            .where(
                model.DesignEvidence.design_task_id == design_task_id,
                model.DesignEvidence.superseded_at.is_(None),
            )
            .order_by(model.DesignEvidence.evidence_version.desc())
            .limit(1)
        ).one_or_none()
        return _evidence(row) if row else None

    def get(self, evidence_id: UUID) -> dto.DesignEvidence | None:
        row = self.session.get(model.DesignEvidence, evidence_id)
        return _evidence(row) if row else None

    def list_for_task(self, design_task_id: UUID) -> list[dto.DesignEvidence]:
        rows = self.session.scalars(
            sa.select(model.DesignEvidence)
            .where(model.DesignEvidence.design_task_id == design_task_id)
            .order_by(
                model.DesignEvidence.evidence_version.desc(),
                model.DesignEvidence.created_at.desc(),
                model.DesignEvidence.id,
            )
        ).all()
        return [_evidence(row) for row in rows]

    def list_live_for_request(
        self,
        generation_request_id: UUID,
        *,
        exclude_task_id: UUID | None = None,
        limit: int | None = None,
    ) -> list[dto.DesignEvidence]:
        stmt = (
            sa.select(model.DesignEvidence)
            .join(
                task_model.DesignTask,
                model.DesignEvidence.design_task_id == task_model.DesignTask.id,
            )
            .where(
                task_model.DesignTask.generation_request_id == generation_request_id,
                model.DesignEvidence.superseded_at.is_(None),
            )
            .order_by(model.DesignEvidence.created_at, model.DesignEvidence.id)
        )
        if exclude_task_id is not None:
            stmt = stmt.where(model.DesignEvidence.design_task_id != exclude_task_id)
        if limit is not None:
            stmt = stmt.limit(limit)
        return [_evidence(row) for row in self.session.scalars(stmt)]

    def list_historical_for_category(
        self,
        category: str,
        *,
        exclude_generation_request_id: UUID | None = None,
        limit: int = 10,
    ) -> list[dto.DesignEvidence]:
        stmt = (
            sa.select(model.DesignEvidence)
            .join(
                task_model.DesignTask,
                model.DesignEvidence.design_task_id == task_model.DesignTask.id,
            )
            .where(
                task_model.DesignTask.category == category,
                model.DesignEvidence.superseded_at.is_(None),
            )
            .order_by(model.DesignEvidence.created_at.desc(), model.DesignEvidence.id)
            .limit(limit)
        )
        if exclude_generation_request_id is not None:
            stmt = stmt.where(
                task_model.DesignTask.generation_request_id
                != exclude_generation_request_id
            )
        return [_evidence(row) for row in self.session.scalars(stmt)]

    def next_version(self, design_task_id: UUID) -> int:
        value = self.session.scalar(
            sa.select(sa.func.max(model.DesignEvidence.evidence_version)).where(
                model.DesignEvidence.design_task_id == design_task_id
            )
        )
        return int(value or 0) + 1

    def create_live(
        self,
        *,
        design_task_id: UUID,
        challenge_design_id: UUID,
        research_finding_ids: Sequence[UUID],
        profile: Mapping[str, object],
        profile_signature: str,
        distinctness_claim: str,
        compared_challenge_ids: Sequence[str],
        evidence: Mapping[str, object],
        build_contract: Mapping[str, object],
        ledger_version: int,
    ) -> dto.DesignEvidence:
        if not distinctness_claim.strip():
            raise DesignEvidencePersistenceError("distinctness_claim is required")
        row = model.DesignEvidence(
            id=uuid4(),
            design_task_id=design_task_id,
            evidence_version=self.next_version(design_task_id),
            challenge_design_id=challenge_design_id,
            research_finding_ids=[str(item) for item in research_finding_ids],
            profile=dict(profile),
            profile_signature=profile_signature,
            distinctness_claim=distinctness_claim.strip(),
            compared_challenge_ids=list(compared_challenge_ids),
            evidence=dict(evidence),
            build_contract=dict(build_contract),
            ledger_version=ledger_version,
        )
        self.session.add(row)
        self.session.flush()
        task = self.session.get(task_model.DesignTask, design_task_id)
        if task is None:
            raise DesignEvidencePersistenceError(
                f"design task {design_task_id} does not exist"
            )
        task.current_design_evidence_id = row.id
        task.updated_at = _utcnow()
        self.session.flush()
        self.session.refresh(row)
        return _evidence(row)

    def supersede_live_for_task(
        self,
        design_task_id: UUID,
        *,
        reason: str,
        superseded_by_evidence_id: UUID | None = None,
    ) -> dto.DesignEvidence | None:
        if not reason.strip():
            raise DesignEvidencePersistenceError("supersession reason is required")
        row = self.session.scalars(
            sa.select(model.DesignEvidence)
            .where(
                model.DesignEvidence.design_task_id == design_task_id,
                model.DesignEvidence.superseded_at.is_(None),
            )
            .with_for_update()
            .limit(1)
        ).one_or_none()
        if row is None:
            return None
        now = _utcnow()
        row.superseded_at = now
        row.superseded_by_evidence_id = superseded_by_evidence_id
        row.supersession_reason = reason.strip()
        task = self.session.get(task_model.DesignTask, design_task_id)
        if task is not None and task.current_design_evidence_id == row.id:
            task.current_design_evidence_id = None
            task.updated_at = now
        self.session.flush()
        self.session.refresh(row)
        return _evidence(row)


def _evidence(row: model.DesignEvidence) -> dto.DesignEvidence:
    return dto.DesignEvidence(
        id=row.id,
        design_task_id=row.design_task_id,
        evidence_version=row.evidence_version,
        challenge_design_id=row.challenge_design_id,
        research_finding_ids=tuple(UUID(str(item)) for item in row.research_finding_ids),
        profile=dict(row.profile),
        profile_signature=row.profile_signature,
        distinctness_claim=row.distinctness_claim,
        compared_challenge_ids=tuple(str(item) for item in row.compared_challenge_ids),
        evidence=dict(row.evidence),
        build_contract=dict(row.build_contract),
        ledger_version=row.ledger_version,
        created_at=row.created_at,
        superseded_at=row.superseded_at,
        superseded_by_evidence_id=row.superseded_by_evidence_id,
        supersession_reason=row.supersession_reason,
    )
