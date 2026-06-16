"""Domain DTOs and value sets for the design-task-planning workflow.

Mirrors the ``design_tasks`` table introduced by Alembic revision
``0003_design_tasks``. The DTO is a frozen dataclass; the allowed status
values are exposed as a tuple constant. The shard-compatible seed
fields (``challenge_id``, ``title``, ``category``, ``difficulty``,
``primary_technique``, ``learning_objective``, ``points``, ``port``)
intentionally use the same names as ``core.queue`` / ``domain.seeds``
so a future shard-export change can serialize without renaming.

Validation logic lives in :mod:`domain.design_task_validators`; this
file is purely data shape.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

DesignTaskStatus: tuple[str, ...] = (
    "draft",
    "queued",
    "designing",
    "designed",
    "failed",
    "archived",
)


@dataclass(frozen=True)
class DesignTask:
    id: UUID
    generation_request_id: UUID
    research_run_id: UUID
    task_no: int
    challenge_id: str
    title: str
    category: str
    difficulty: str
    primary_technique: str
    learning_objective: str
    points: int
    port: int | None
    scenario: str
    constraints: Mapping[str, Any]
    evidence_summary: str
    finding_ids: Sequence[UUID]
    status: str
    created_at: datetime
    updated_at: datetime
