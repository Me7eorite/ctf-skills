"""Design evidence DTOs and value sets."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID


@dataclass(frozen=True)
class DesignEvidence:
    id: UUID
    design_task_id: UUID
    evidence_version: int
    challenge_design_id: UUID
    research_finding_ids: Sequence[UUID]
    profile: Mapping[str, Any]
    profile_signature: str
    distinctness_claim: str
    compared_challenge_ids: Sequence[str]
    evidence: Mapping[str, Any]
    build_contract: Mapping[str, Any]
    ledger_version: int
    created_at: datetime
    superseded_at: datetime | None = None
    superseded_by_evidence_id: UUID | None = None
    supersession_reason: str | None = None
