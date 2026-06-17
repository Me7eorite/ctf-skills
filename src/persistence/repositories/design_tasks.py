"""Persistence primitives for design tasks.

Mirrors :class:`persistence.repositories.research.ResearchRepository`'s
shape: typed CRUD/query helpers that never commit themselves — the
caller owns the transaction. Cross-row validation that doesn't need
a SELECT lives in :mod:`domain.design_task_validators`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.orm import Session

from domain import design_tasks as dto
from domain.design_task_validators import (
    DesignTaskValidationError,
    validate_candidate,
    validate_candidate_set,
    validate_status_transition,
)
from persistence.models import design_tasks as model


class DesignTaskRepository:
    """Typed CRUD primitives for ``design_tasks`` rows."""

    def __init__(self, session: Session) -> None:
        # Repository only borrows the caller-supplied session; the caller
        # decides when to commit, just like ResearchRepository.
        self.session = session

    def get_design_task(self, task_id: UUID) -> dto.DesignTask | None:
        row = self.session.get(model.DesignTask, task_id)
        return _design_task(row) if row else None

    def list_design_tasks(self, generation_request_id: UUID) -> list[dto.DesignTask]:
        """Return tasks for a request ordered by ``task_no`` ascending."""
        rows = self.session.scalars(
            sa.select(model.DesignTask)
            .where(model.DesignTask.generation_request_id == generation_request_id)
            .order_by(model.DesignTask.task_no)
        ).all()
        return [_design_task(row) for row in rows]

    def replace_draft_or_archived_tasks(
        self,
        *,
        generation_request_id: UUID,
        research_run_id: UUID,
        parent_category: str,
        target_count: int,
        difficulty_distribution: Mapping[str, int],
        candidates: Sequence[Mapping[str, Any]],
    ) -> list[dto.DesignTask]:
        """Replace existing ``draft``/``archived`` tasks with a fresh set.

        Workflow (single transaction owned by the caller):

        1. Reject when any existing task is ``queued``/``designing``/
           ``designed``/``failed``.
        2. Run candidate-set + per-candidate shape validation.
        3. Delete the existing ``draft``/``archived`` rows in this same
           transaction so the
           ``unique(generation_request_id, challenge_id)`` constraint
           cannot fire transiently.
        4. Insert the new draft rows.
        """
        existing = self.session.scalars(
            sa.select(model.DesignTask)
            .where(model.DesignTask.generation_request_id == generation_request_id)
            .with_for_update()
        ).all()
        blocking_statuses = {"queued", "designing", "designed", "failed"}
        blockers = [row for row in existing if row.status in blocking_statuses]
        if blockers:
            raise DesignTaskValidationError(
                "cannot regenerate design tasks: "
                f"{len(blockers)} task(s) already in non-draft/archived status"
            )

        # Shape validation: candidate set + per-candidate. Doing it
        # before the DELETE means a bad planner output cannot leave the
        # request without any tasks at all.
        validate_candidate_set(
            candidates,
            target_count=target_count,
            difficulty_distribution=difficulty_distribution,
        )
        # 按 task_no 排序后再做逐条校验/插入：validate_candidate_set 已经
        # 保证 task_no 是 1..N 的全集，排序使得未来非确定性 planner 即便
        # 乱序输出，也能稳定通过位置敏感的 per-candidate 校验。
        ordered_candidates = sorted(candidates, key=lambda c: int(c["task_no"]))
        for index, candidate in enumerate(ordered_candidates):
            validate_candidate(
                candidate,
                parent_category=parent_category,
                task_no=index + 1,
            )

        if existing:
            for row in existing:
                self.session.delete(row)
            self.session.flush()

        created_rows: list[model.DesignTask] = []
        now = _utcnow()
        for candidate in ordered_candidates:
            row = model.DesignTask(
                id=uuid4(),
                generation_request_id=generation_request_id,
                research_run_id=research_run_id,
                task_no=int(candidate["task_no"]),
                challenge_id=candidate["challenge_id"],
                title=candidate["title"],
                category=candidate["category"],
                difficulty=candidate["difficulty"],
                primary_technique=candidate["primary_technique"],
                learning_objective=candidate["learning_objective"],
                points=int(candidate["points"]),
                port=candidate.get("port"),
                scenario=str(candidate.get("scenario", "")),
                constraints=dict(candidate.get("constraints") or {}),
                evidence_summary=str(candidate.get("evidence_summary", "")),
                finding_ids=[str(fid) for fid in candidate.get("finding_ids") or ()],
                status="draft",
                updated_at=now,
            )
            self.session.add(row)
            created_rows.append(row)

        self.session.flush()
        for row in created_rows:
            self.session.refresh(row)
        return [_design_task(row) for row in created_rows]

    def set_design_task_status(self, task_id: UUID, status: str) -> dto.DesignTask:
        """Transition a single design task's status.

        Only operator transitions allowed by
        :func:`domain.design_task_validators.validate_status_transition`
        are accepted — worker states are reserved for a future change.
        """
        row = self.session.get(model.DesignTask, task_id)
        if row is None:
            raise DesignTaskValidationError(f"design task {task_id} does not exist")
        validate_status_transition(row.status, status)
        row.status = status
        row.updated_at = _utcnow()
        self.session.flush()
        self.session.refresh(row)
        return _design_task(row)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _design_task(row: model.DesignTask) -> dto.DesignTask:
    return dto.DesignTask(
        id=row.id,
        generation_request_id=row.generation_request_id,
        research_run_id=row.research_run_id,
        task_no=row.task_no,
        challenge_id=row.challenge_id,
        title=row.title,
        category=row.category,
        difficulty=row.difficulty,
        primary_technique=row.primary_technique,
        learning_objective=row.learning_objective,
        points=row.points,
        port=row.port,
        scenario=row.scenario,
        constraints=dict(row.constraints),
        evidence_summary=row.evidence_summary,
        finding_ids=tuple(UUID(str(fid)) for fid in row.finding_ids),
        status=row.status,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )
