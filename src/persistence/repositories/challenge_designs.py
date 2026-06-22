"""Persistence primitives for structured challenge-design attempts."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.orm import Session

from domain import challenge_designs as dto
from persistence.models import challenge_designs as model
from persistence.models import design_tasks as dt_model


class ChallengeDesignPersistenceError(ValueError):
    """Raised when an attempt cannot be persisted in its current state."""


class ChallengeDesignRepository:
    """Typed CRUD primitives for design attempts and challenge designs."""

    def __init__(self, session: Session) -> None:
        # The caller owns transaction boundaries; this repository never commits.
        self.session = session

    def list_attempts(self, design_task_id: UUID) -> list[dto.DesignAttempt]:
        rows = self.session.scalars(
            sa.select(model.DesignAttempt)
            .where(model.DesignAttempt.design_task_id == design_task_id)
            .order_by(model.DesignAttempt.attempt)
        ).all()
        return [_attempt(row) for row in rows]

    def get_attempt(self, attempt_id: UUID) -> dto.DesignAttempt | None:
        row = self.session.get(model.DesignAttempt, attempt_id)
        return _attempt(row) if row else None

    def latest_attempt(self, design_task_id: UUID) -> dto.DesignAttempt | None:
        row = self.session.scalars(
            sa.select(model.DesignAttempt)
            .where(model.DesignAttempt.design_task_id == design_task_id)
            .order_by(model.DesignAttempt.attempt.desc())
            .limit(1)
        ).one_or_none()
        return _attempt(row) if row else None

    def create_attempt(
        self,
        design_task_id: UUID,
        attempt_no: int,
        caller: str,
        profile_name: str,
    ) -> dto.DesignAttempt:
        if attempt_no <= 0:
            raise ChallengeDesignPersistenceError("attempt_no must be positive")
        task = self._lock_design_task(design_task_id)
        if task.status != "queued":
            raise ChallengeDesignPersistenceError(
                f"design task {design_task_id} is {task.status}, expected queued"
            )

        now = _utcnow()
        row = model.DesignAttempt(
            id=uuid4(),
            design_task_id=design_task_id,
            attempt=attempt_no,
            status="running",
            claimed_by=caller,
            claim_token=uuid4(),
            started_at=now,
            profile_name_used=profile_name,
        )
        task.status = "designing"
        task.updated_at = now
        self.session.add(row)
        self.session.flush()
        self.session.refresh(row)
        return _attempt(row)

    def record_prompt_path(
        self,
        attempt_id: UUID,
        claim_token: UUID,
        prompt_path: str,
    ) -> dto.DesignAttempt:
        row = self._lock_running_attempt(attempt_id, claim_token)
        row.prompt_path = prompt_path
        self.session.flush()
        self.session.refresh(row)
        return _attempt(row)

    def complete_attempt(
        self,
        attempt_id: UUID,
        claim_token: UUID,
        log_path: str,
        payload: Mapping[str, Any],
        summary: str,
        flag_format: str,
        validation_notes: str,
        quality_gate_passed: bool,
    ) -> dto.ChallengeDesign:
        attempt = self._lock_running_attempt(attempt_id, claim_token)
        task = self._lock_design_task(attempt.design_task_id)
        if task.status != "designing":
            raise ChallengeDesignPersistenceError(
                f"design task {task.id} is {task.status}, expected designing"
            )

        now = _utcnow()
        attempt.status = "completed"
        attempt.finished_at = now
        attempt.hermes_log_path = log_path
        attempt.last_error = None
        design = model.ChallengeDesign(
            id=uuid4(),
            design_task_id=attempt.design_task_id,
            design_attempt_id=attempt.id,
            payload=dict(payload),
            summary=summary,
            flag_format=flag_format,
            validation_notes=validation_notes,
            quality_gate_passed=quality_gate_passed,
            status="draft",
            updated_at=now,
        )
        task.status = "designed"
        task.updated_at = now
        self.session.add(design)
        self.session.flush()
        self.session.refresh(design)
        return _design(design)

    def fail_attempt(
        self,
        attempt_id: UUID,
        claim_token: UUID,
        log_path: str,
        last_error: str,
        max_attempts: int,
    ) -> dto.DesignAttempt:
        if max_attempts <= 0:
            raise ChallengeDesignPersistenceError("max_attempts must be positive")
        if not last_error:
            raise ChallengeDesignPersistenceError("last_error is required")

        attempt = self._lock_running_attempt(attempt_id, claim_token)
        task = self._lock_design_task(attempt.design_task_id)
        if task.status != "designing":
            raise ChallengeDesignPersistenceError(
                f"design task {task.id} is {task.status}, expected designing"
            )

        now = _utcnow()
        attempt.status = "failed"
        attempt.finished_at = now
        attempt.hermes_log_path = log_path
        attempt.last_error = last_error
        task.status = "queued" if attempt.attempt < max_attempts else "failed"
        task.updated_at = now
        self.session.flush()
        self.session.refresh(attempt)
        return _attempt(attempt)

    def latest_design(
        self,
        design_task_id: UUID,
        status: str = "draft",
    ) -> dto.ChallengeDesign | None:
        row = self.session.scalars(
            sa.select(model.ChallengeDesign)
            .where(
                model.ChallengeDesign.design_task_id == design_task_id,
                model.ChallengeDesign.status == status,
            )
            .order_by(model.ChallengeDesign.created_at.desc())
            .limit(1)
        ).one_or_none()
        return _design(row) if row else None

    def _lock_design_task(self, design_task_id: UUID) -> dt_model.DesignTask:
        task = self.session.scalars(
            sa.select(dt_model.DesignTask)
            .where(dt_model.DesignTask.id == design_task_id)
            .with_for_update()
        ).one_or_none()
        if task is None:
            raise ChallengeDesignPersistenceError(
                f"design task {design_task_id} does not exist"
            )
        return task

    def _lock_running_attempt(
        self,
        attempt_id: UUID,
        claim_token: UUID,
    ) -> model.DesignAttempt:
        attempt = self.session.scalars(
            sa.select(model.DesignAttempt)
            .where(
                model.DesignAttempt.id == attempt_id,
                model.DesignAttempt.claim_token == claim_token,
                model.DesignAttempt.status == "running",
            )
            .with_for_update()
        ).one_or_none()
        if attempt is None:
            raise ChallengeDesignPersistenceError(
                f"running design attempt {attempt_id} was not found for token"
            )
        return attempt


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _attempt(row: model.DesignAttempt) -> dto.DesignAttempt:
    return dto.DesignAttempt(
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


def _design(row: model.ChallengeDesign) -> dto.ChallengeDesign:
    return dto.ChallengeDesign(
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
        legacy_grandfather=row.legacy_grandfather,
    )
