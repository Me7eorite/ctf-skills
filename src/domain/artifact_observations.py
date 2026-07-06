"""Domain DTOs for governed artifact observations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

ArtifactObservationStatus: tuple[str, ...] = ("passed", "failed", "inconclusive")


@dataclass(frozen=True)
class ArtifactObservation:
    id: UUID
    build_attempt_id: UUID
    observation_version: int
    design_evidence_id: UUID | None
    contract_sha256: str
    artifact_manifest_sha256: str
    observed_profile: dict[str, Any]
    contract_checks: dict[str, Any]
    negative_test_results: dict[str, Any]
    fingerprints: dict[str, Any]
    status: str
    is_current: bool
    created_at: datetime
    superseded_at: datetime | None

