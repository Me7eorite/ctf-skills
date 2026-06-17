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

from domain import challenge_designs as challenge_dto
from domain import design_tasks as dto
from domain.design_task_validators import (
    DesignTaskValidationError,
    validate_candidate,
    validate_candidate_set,
    validate_status_transition,
)
from persistence.models import challenge_designs as design_model
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

    def list_tasks(
        self,
        *,
        generation_request_id: UUID | None = None,
        status: str | None = None,
        category: str | None = None,
        limit: int = 100,
    ) -> list[dto.DesignTask]:
        """Return globally queryable task rows without history payloads."""
        if limit <= 0:
            raise DesignTaskValidationError("limit must be positive")
        query = sa.select(model.DesignTask)
        if generation_request_id is not None:
            query = query.where(
                model.DesignTask.generation_request_id == generation_request_id
            )
        if status is not None:
            query = query.where(model.DesignTask.status == status)
        if category is not None:
            query = query.where(model.DesignTask.category == category)
        rows = self.session.scalars(
            query.order_by(
                model.DesignTask.generation_request_id,
                model.DesignTask.task_no,
            ).limit(min(limit, 500))
        ).all()
        return [_design_task(row) for row in rows]

    def summarize_for_request(self, generation_request_id: UUID) -> dict[str, Any]:
        """Return total and per-status counts for a request."""
        counts = {status: 0 for status in dto.DesignTaskStatus}
        rows = self.session.execute(
            sa.select(model.DesignTask.status, sa.func.count())
            .where(model.DesignTask.generation_request_id == generation_request_id)
            .group_by(model.DesignTask.status)
        ).all()
        for status, count in rows:
            counts[str(status)] = int(count)
        return {"total": sum(counts.values()), "by_status": counts}

    def get_with_history(
        self,
        task_id: UUID,
    ) -> tuple[
        dto.DesignTask,
        list[challenge_dto.DesignAttempt],
        challenge_dto.ChallengeDesign | None,
    ] | None:
        """Return one task with its attempts and current draft design."""
        task_row = self.session.get(model.DesignTask, task_id)
        if task_row is None:
            return None
        attempts = self.session.scalars(
            sa.select(design_model.DesignAttempt)
            .where(design_model.DesignAttempt.design_task_id == task_id)
            .order_by(design_model.DesignAttempt.attempt)
        ).all()
        latest_design = self.session.scalars(
            sa.select(design_model.ChallengeDesign)
            .where(
                design_model.ChallengeDesign.design_task_id == task_id,
                design_model.ChallengeDesign.status == "draft",
            )
            .order_by(design_model.ChallengeDesign.created_at.desc())
            .limit(1)
        ).one_or_none()
        return (
            _design_task(task_row),
            [_attempt(row) for row in attempts],
            _challenge_design(latest_design) if latest_design else None,
        )

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


def _attempt(row: design_model.DesignAttempt) -> challenge_dto.DesignAttempt:
    return challenge_dto.DesignAttempt(
        id=row.id,
        design_task_id=row.design_task_id,
        attempt=row.attempt,
        status=row.status,
        claimed_by=row.claimed_by,
        claim_token=row.claim_token,
        started_at=row.started_at,
        finished_at=row.finished_at,
        profile_name_used=row.profile_name_used,
        prompt_path=row.prompt_path,
        hermes_log_path=row.hermes_log_path,
        last_error=row.last_error,
        created_at=row.created_at,
    )


def _challenge_design(
    row: design_model.ChallengeDesign,
) -> challenge_dto.ChallengeDesign:
    return challenge_dto.ChallengeDesign(
        id=row.id,
        design_task_id=row.design_task_id,
        design_attempt_id=row.design_attempt_id,
        payload=dict(row.payload),
        summary=row.summary,
        flag_format=row.flag_format,
        validation_notes=row.validation_notes,
        quality_gate_passed=row.quality_gate_passed,
        status=row.status,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )
