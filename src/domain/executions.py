"""Domain DTOs and status sets for execution rows."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

EXECUTION_KINDS: tuple[str, ...] = ("initial", "retry", "revision")
EXECUTION_MODES: tuple[str, ...] = ("standard", "clean")
EXECUTION_STATUSES: tuple[str, ...] = (
    "queued",
    "claimed",
    "running",
    "succeeded",
    "failed",
    "lost",
)
NON_TERMINAL_STATUSES: tuple[str, ...] = ("queued", "claimed", "running")
ACTIVE_STATUSES: tuple[str, ...] = ("claimed", "running")
TERMINAL_STATUSES: tuple[str, ...] = ("succeeded", "failed", "lost")

# Container aggregate precedence: latest execution status -> container status.
CONTAINER_STATUS_BY_EXECUTION: dict[str, str] = {
    "queued": "queued",
    "claimed": "running",
    "running": "running",
    "succeeded": "succeeded",
    "failed": "failed",
    "lost": "lost",
}


@dataclass(frozen=True)
class Execution:
    """One build run inside a build-attempt container."""

    id: UUID
    build_attempt_id: UUID
    parent_execution_id: UUID | None
    iteration_no: int
    execution_kind: str
    execution_mode: str
    feedback_snapshot_id: UUID | None
    worker_id: str | None
    claim_token: UUID | None
    lease_expires_at: datetime | None
    heartbeat_at: datetime | None
    status: str
    exit_class: str | None
    error: str | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
