"""Domain DTOs and value sets for structured challenge designs."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

DesignAttemptStatus: tuple[str, ...] = ("running", "completed", "failed")
ChallengeDesignStatus: tuple[str, ...] = ("draft", "accepted", "superseded")


@dataclass(frozen=True)
class DesignAttempt:
    id: UUID
    design_task_id: UUID
    attempt: int
    status: str
    claimed_by: str | None
    claim_token: UUID
    started_at: datetime | None
    finished_at: datetime | None
    profile_name_used: str
    prompt_path: str | None
    hermes_log_path: str | None
    last_error: str | None
    created_at: datetime


@dataclass(frozen=True)
class ChallengeDesign:
    id: UUID
    design_task_id: UUID
    design_attempt_id: UUID
    payload: Mapping[str, Any]
    summary: str
    flag_format: str
    validation_notes: str
    quality_gate_passed: bool
    status: str
    created_at: datetime
    updated_at: datetime
