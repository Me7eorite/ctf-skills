"""Persistence primitives for governed design profile reservations."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from core.clock import utcnow as _utcnow
from domain import design_profile_reservations as dto
from domain.design.profile_taxonomy import ProfileOccupancy, profile_occupancy_from_mapping
from persistence.models import design_profile_reservations as model
from persistence.models import research as research_model
from persistence.models import research as research_model


class DesignProfileReservationPersistenceError(ValueError):
    """Raised when reservation state transitions are invalid."""


class DesignProfileReservationRepository:
    """Typed CRUD/query primitives for profile reservations and ledgers."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def ensure_ledger(self, category: str, *, policy_version: int) -> model.DesignProfileLedger:
        self.session.execute(
            insert(model.DesignProfileLedger)
            .values(category=category, policy_version=policy_version, ledger_version=0)
            .on_conflict_do_nothing(index_elements=["category"])
        )
        row = self.session.scalars(
            sa.select(model.DesignProfileLedger).where(model.DesignProfileLedger.category == category)
        ).one()
        if row.policy_version != policy_version:
            row.policy_version = policy_version
            row.updated_at = _utcnow()
            self.session.flush()
        return row

    def lock_ledger(self, category: str, *, policy_version: int) -> model.DesignProfileLedger:
        self.ensure_ledger(category, policy_version=policy_version)
        row = self.session.scalars(
            sa.select(model.DesignProfileLedger)
            .where(model.DesignProfileLedger.category == category)
            .with_for_update()
        ).one()
        return row

    def list_active_occupancies(self, category: str) -> list[ProfileOccupancy]:
        rows = self.session.scalars(
            sa.select(model.DesignProfileReservation)
            .join(
                research_model.GenerationRequest,
                model.DesignProfileReservation.generation_request_id
                == research_model.GenerationRequest.id,
            )
            .where(
                research_model.GenerationRequest.category == category,
                model.DesignProfileReservation.state.in_(("reserved", "committed")),
            )
            .order_by(model.DesignProfileReservation.created_at, model.DesignProfileReservation.id)
        ).all()
        return [
            profile_occupancy_from_mapping(
                row.profile,
                category=category,
                state=row.state,
                source_id=str(row.id),
            )
            for row in rows
        ]

    def get_latest_reservation_version(self, design_task_id: UUID) -> int:
        value = self.session.scalar(
            sa.select(sa.func.max(model.DesignProfileReservation.reservation_version)).where(
                model.DesignProfileReservation.design_task_id == design_task_id
            )
        )
        return int(value or 0)

    def reserve_task(
        self,
        *,
        design_task_id: UUID,
        generation_request_id: UUID,
        profile: dict[str, object],
        profile_signature: str,
        occupancy_scope: str | None,
        exclusive_signature_key: str | None,
        taxonomy_version: int,
        policy_version: int,
        ledger_version: int,
    ) -> dto.DesignProfileReservation:
        version = self.get_latest_reservation_version(design_task_id) + 1
        row = model.DesignProfileReservation(
            id=uuid4(),
            design_task_id=design_task_id,
            generation_request_id=generation_request_id,
            reservation_version=version,
            profile=dict(profile),
            profile_signature=profile_signature,
            occupancy_scope=occupancy_scope,
            exclusive_signature_key=exclusive_signature_key,
            state="reserved",
            taxonomy_version=taxonomy_version,
            policy_version=policy_version,
            ledger_version=ledger_version,
        )
        self.session.add(row)
        self.session.flush()
        self.session.refresh(row)
        return _reservation(row)

    def release_reservation(
        self,
        reservation_id: UUID,
        *,
        bump_ledger: bool = False,
    ) -> dto.DesignProfileReservation:
        row = self.session.get(model.DesignProfileReservation, reservation_id)
        if row is None:
            raise DesignProfileReservationPersistenceError(
                f"reservation {reservation_id} does not exist"
            )
        if row.state != "released":
            row.state = "released"
            row.released_at = _utcnow()
            if bump_ledger:
                row.ledger_version = self._bump_ledger(row.generation_request_id)
            self.session.flush()
        self.session.refresh(row)
        return _reservation(row)

    def commit_reservation(self, reservation_id: UUID) -> dto.DesignProfileReservation:
        row = self.session.get(model.DesignProfileReservation, reservation_id)
        if row is None:
            raise DesignProfileReservationPersistenceError(
                f"reservation {reservation_id} does not exist"
            )
        if row.state != "reserved":
            raise DesignProfileReservationPersistenceError(
                f"reservation {reservation_id} is {row.state}, expected reserved"
            )
        row.state = "committed"
        row.committed_at = _utcnow()
        row.ledger_version = self._bump_ledger(row.generation_request_id)
        self.session.flush()
        self.session.refresh(row)
        return _reservation(row)

    def set_current_reservation(self, task_id: UUID, reservation_id: UUID | None) -> None:
        row = self.session.get(task_model.DesignTask, task_id)
        if row is None:
            raise DesignProfileReservationPersistenceError(f"design task {task_id} does not exist")
        row.current_reservation_id = reservation_id
        row.updated_at = _utcnow()
        self.session.flush()

    def _bump_ledger(self, generation_request_id: UUID) -> int:
        category = self.session.scalar(
            sa.select(research_model.GenerationRequest.category).where(
                research_model.GenerationRequest.id == generation_request_id
            )
        )
        if category is None:
            raise DesignProfileReservationPersistenceError(
                f"generation request {generation_request_id} does not exist"
            )
        row = self.session.scalars(
            sa.select(model.DesignProfileLedger)
            .where(model.DesignProfileLedger.category == str(category))
            .with_for_update()
        ).one()
        row.ledger_version += 1
        row.updated_at = _utcnow()
        self.session.flush()
        return row.ledger_version


def _reservation(row: model.DesignProfileReservation) -> dto.DesignProfileReservation:
    return dto.DesignProfileReservation(
        id=row.id,
        design_task_id=row.design_task_id,
        generation_request_id=row.generation_request_id,
        reservation_version=row.reservation_version,
        profile=dict(row.profile),
        profile_signature=row.profile_signature,
        occupancy_scope=row.occupancy_scope,
        exclusive_signature_key=row.exclusive_signature_key,
        state=row.state,
        taxonomy_version=row.taxonomy_version,
        policy_version=row.policy_version,
        ledger_version=row.ledger_version,
        created_at=row.created_at,
        committed_at=row.committed_at,
        released_at=row.released_at,
    )


def _ledger(row: model.DesignProfileLedger) -> dto.DesignProfileLedger:
    return dto.DesignProfileLedger(
        id=row.id,
        category=row.category,
        policy_version=row.policy_version,
        ledger_version=row.ledger_version,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )
