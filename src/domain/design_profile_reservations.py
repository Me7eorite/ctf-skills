"""Design profile reservation and ledger DTOs."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

DesignProfileReservationState: tuple[str, ...] = ("reserved", "committed", "released")


@dataclass(frozen=True)
class DesignProfileReservation:
    id: UUID
    design_task_id: UUID | None
    generation_request_id: UUID
    reservation_version: int
    profile: Mapping[str, Any]
    profile_signature: str
    occupancy_scope: str | None
    exclusive_signature_key: str | None
    state: str
    taxonomy_version: int
    policy_version: int
    ledger_version: int
    created_at: datetime
    committed_at: datetime | None = None
    released_at: datetime | None = None


@dataclass(frozen=True)
class DesignProfileLedger:
    id: UUID
    category: str
    policy_version: int
    ledger_version: int
    created_at: datetime
    updated_at: datetime
