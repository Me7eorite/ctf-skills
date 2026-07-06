"""Persistence primitives for governed artifact observations."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.orm import Session

from core.clock import utcnow as _utcnow
from domain.artifact_observations import ArtifactObservation as ArtifactObservationDTO
from persistence.models import artifact_observations as model
from persistence.models import build_attempts as build_model


class ArtifactObservationPersistenceError(ValueError):
    """Raised when artifact observations cannot be persisted as requested."""


class ArtifactObservationRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def latest_current_for_attempt(self, build_attempt_id: UUID) -> ArtifactObservationDTO | None:
        row = self.session.scalars(
            sa.select(model.ArtifactObservation)
            .where(
                model.ArtifactObservation.build_attempt_id == build_attempt_id,
                model.ArtifactObservation.is_current.is_(True),
            )
            .order_by(model.ArtifactObservation.observation_version.desc())
            .limit(1)
        ).one_or_none()
        return _observation(row) if row else None

    def create_current(
        self,
        *,
        build_attempt_id: UUID,
        design_evidence_id: UUID | None,
        contract_sha256: str,
        artifact_manifest_sha256: str,
        observed_profile: Mapping[str, Any],
        contract_checks: Mapping[str, Any],
        negative_test_results: Mapping[str, Any],
        fingerprints: Mapping[str, Any],
        status: str,
        is_current: bool = True,
    ) -> ArtifactObservationDTO:
        row = model.ArtifactObservation(
            id=uuid4(),
            build_attempt_id=build_attempt_id,
            observation_version=self.next_version(build_attempt_id),
            design_evidence_id=design_evidence_id,
            contract_sha256=contract_sha256,
            artifact_manifest_sha256=artifact_manifest_sha256,
            observed_profile=dict(observed_profile),
            contract_checks=dict(contract_checks),
            negative_test_results=dict(negative_test_results),
            fingerprints=dict(fingerprints),
            status=status,
            is_current=is_current,
        )
        self.session.add(row)
        self.session.flush()
        self._sync_current_pointer(build_attempt_id, row)
        self.session.flush()
        self.session.refresh(row)
        return _observation(row)

    def next_version(self, build_attempt_id: UUID) -> int:
        value = self.session.scalar(
            sa.select(sa.func.max(model.ArtifactObservation.observation_version)).where(
                model.ArtifactObservation.build_attempt_id == build_attempt_id
            )
        )
        return int(value or 0) + 1

    def supersede_current(
        self,
        build_attempt_id: UUID,
        *,
        superseded_at: datetime | None = None,
    ) -> None:
        row = self.session.scalars(
            sa.select(model.ArtifactObservation)
            .where(
                model.ArtifactObservation.build_attempt_id == build_attempt_id,
                model.ArtifactObservation.is_current.is_(True),
            )
            .with_for_update()
            .limit(1)
        ).one_or_none()
        if row is None:
            return
        row.is_current = False
        row.superseded_at = superseded_at or _utcnow()
        self.session.flush()

    def _sync_current_pointer(self, build_attempt_id: UUID, row: model.ArtifactObservation) -> None:
        attempt = self.session.get(build_model.BuildAttempt, build_attempt_id)
        if attempt is None:
            raise ArtifactObservationPersistenceError(
                f"build attempt {build_attempt_id} does not exist"
            )
        attempt.artifact_observation_id = row.id
        self.session.flush()


def _observation(row: model.ArtifactObservation) -> ArtifactObservationDTO:
    return ArtifactObservationDTO(
        id=row.id,
        build_attempt_id=row.build_attempt_id,
        observation_version=row.observation_version,
        design_evidence_id=row.design_evidence_id,
        contract_sha256=row.contract_sha256,
        artifact_manifest_sha256=row.artifact_manifest_sha256,
        observed_profile=dict(row.observed_profile),
        contract_checks=dict(row.contract_checks),
        negative_test_results=dict(row.negative_test_results),
        fingerprints=dict(row.fingerprints),
        status=row.status,
        is_current=row.is_current,
        created_at=row.created_at,
        superseded_at=row.superseded_at,
    )

