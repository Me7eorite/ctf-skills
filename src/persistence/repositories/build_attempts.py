"""Persistence primitives for editorial build attempts."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.orm import Session, aliased

from domain import build_attempts as dto
from persistence.models import build_attempts as model
from persistence.models import design_tasks as task_model
from persistence.models.progress import ProgressSnapshot


class BuildAttemptPersistenceError(ValueError):
    """Raised when a build-attempt mutation violates repository semantics."""


class BuildAttemptsRepository:
    """Typed queries and mutations; transaction ownership stays with the caller."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def create_attempt(
        self,
        design_task_id: UUID,
        shard_basename: str,
        *,
        attempt_id: UUID | None = None,
        idempotency_key: str | None = None,
    ) -> dto.BuildAttempt:
        if not shard_basename:
            raise BuildAttemptPersistenceError("shard_basename is required")

        # Serialize attempt-number allocation per parent task. The database's
        # compound unique constraint remains the final integrity guard.
        task = self.session.scalars(
            sa.select(task_model.DesignTask)
            .where(task_model.DesignTask.id == design_task_id)
            .with_for_update()
        ).one_or_none()
        if task is None:
            raise BuildAttemptPersistenceError(
                f"design task {design_task_id} does not exist"
            )
        attempt_no = int(task.next_build_attempt_no)
        task.next_build_attempt_no = attempt_no + 1
        row = model.BuildAttempt(
            id=attempt_id or uuid4(),
            design_task_id=design_task_id,
            attempt_no=attempt_no,
            status="queued",
            shard_basename=shard_basename,
            idempotency_key=idempotency_key,
        )
        self.session.add(row)
        self.session.flush()
        self.session.refresh(row)
        return _attempt(row)

    def find_by_idempotency_key(self, key: str) -> dto.BuildAttempt | None:
        if not key:
            return None
        row = self.session.scalars(
            sa.select(model.BuildAttempt).where(
                model.BuildAttempt.idempotency_key == key
            )
        ).one_or_none()
        return _attempt(row) if row else None

    def get(self, attempt_id: UUID) -> dto.BuildAttempt | None:
        row = self.session.get(model.BuildAttempt, attempt_id)
        return _attempt(row) if row else None

    def latest_for_design_task(
        self,
        design_task_id: UUID,
    ) -> dto.BuildAttempt | None:
        row = self.session.scalars(
            sa.select(model.BuildAttempt)
            .where(model.BuildAttempt.design_task_id == design_task_id)
            .order_by(model.BuildAttempt.attempt_no.desc())
            .limit(1)
        ).one_or_none()
        return _attempt(row) if row else None

    def active_for_design_task(self, design_task_id: UUID) -> dto.BuildAttempt | None:
        row = self.session.scalars(
            sa.select(model.BuildAttempt)
            .where(
                model.BuildAttempt.design_task_id == design_task_id,
                model.BuildAttempt.status.in_(("queued", "running")),
            )
            .order_by(model.BuildAttempt.attempt_no.desc())
            .limit(1)
        ).one_or_none()
        return _attempt(row) if row else None

    def list_attempts(
        self,
        *,
        design_task_id: UUID | None = None,
        generation_request_id: UUID | None = None,
        status: str | None = None,
        worker: str | None = None,
        category: str | None = None,
        limit: int = 100,
    ) -> list[dto.BuildAttemptListItem]:
        """Return one latest attempt per task, then filter, order, and limit."""
        if limit <= 0:
            raise BuildAttemptPersistenceError("limit must be positive")

        ranked = (
            sa.select(
                model.BuildAttempt.id.label("attempt_id"),
                sa.func.row_number()
                .over(
                    partition_by=model.BuildAttempt.design_task_id,
                    order_by=model.BuildAttempt.attempt_no.desc(),
                )
                .label("rank"),
            )
            .subquery("ranked_build_attempts")
        )
        latest = aliased(model.BuildAttempt)
        selected_query = (
            sa.select(
                latest.id.label("attempt_id"),
                latest.shard_basename.label("shard_basename"),
                latest.created_at.label("created_at"),
            )
            .join(
                ranked,
                sa.and_(ranked.c.attempt_id == latest.id, ranked.c.rank == 1),
            )
            .join(
                task_model.DesignTask,
                task_model.DesignTask.id == latest.design_task_id,
            )
        )
        if design_task_id is not None:
            selected_query = selected_query.where(
                latest.design_task_id == design_task_id
            )
        if generation_request_id is not None:
            selected_query = selected_query.where(
                task_model.DesignTask.generation_request_id == generation_request_id
            )
        if status is not None:
            selected_query = selected_query.where(latest.status == status)
        if worker is not None:
            selected_query = selected_query.where(latest.worker == worker)
        if category is not None:
            selected_query = selected_query.where(
                task_model.DesignTask.category == category
            )
        selected = selected_query.order_by(
            latest.created_at.desc(), latest.id
        ).limit(limit).cte("selected_build_attempts")
        progress = (
            sa.select(
                ProgressSnapshot.shard.label("shard"),
                sa.func.max(ProgressSnapshot.percent).label("percent"),
            )
            .where(
                ProgressSnapshot.shard.in_(sa.select(selected.c.shard_basename))
            )
            .group_by(ProgressSnapshot.shard)
            .subquery("build_attempt_progress")
        )
        query = (
            sa.select(
                latest,
                task_model.DesignTask.generation_request_id,
                task_model.DesignTask.challenge_id,
                task_model.DesignTask.title,
                task_model.DesignTask.category,
                task_model.DesignTask.difficulty,
                sa.func.coalesce(progress.c.percent, 0),
            )
            .join(
                selected,
                selected.c.attempt_id == latest.id,
            )
            .join(
                task_model.DesignTask,
                task_model.DesignTask.id == latest.design_task_id,
            )
            .outerjoin(progress, progress.c.shard == latest.shard_basename)
        )
        rows = self.session.execute(
            query.order_by(selected.c.created_at.desc(), selected.c.attempt_id)
        ).all()
        return [
            _list_item(
                row[0],
                generation_request_id=row[1],
                challenge_id=row[2],
                title=row[3],
                category=row[4],
                difficulty=row[5],
                percent=int(row[6]),
            )
            for row in rows
        ]

    def list_for_design_task(
        self,
        design_task_id: UUID,
    ) -> list[dto.BuildAttempt]:
        rows = self.session.scalars(
            sa.select(model.BuildAttempt)
            .where(model.BuildAttempt.design_task_id == design_task_id)
            .order_by(model.BuildAttempt.attempt_no)
        ).all()
        return [_attempt(row) for row in rows]

    def update_to_running(
        self,
        attempt_id: UUID,
        *,
        worker: str,
        started_at: datetime | None = None,
    ) -> dto.BuildAttempt:
        if not worker:
            raise BuildAttemptPersistenceError("worker is required")
        row = self._lock(attempt_id)
        if row.status != "queued":
            raise BuildAttemptPersistenceError(
                f"build attempt {attempt_id} is {row.status}, expected queued"
            )
        row.status = "running"
        row.worker = worker
        row.started_at = started_at or _utcnow()
        self.session.flush()
        self.session.refresh(row)
        return _attempt(row)

    def update_to_terminal(
        self,
        attempt_id: UUID,
        *,
        status: str,
        worker: str | None = None,
        started_at: datetime | None = None,
        finished_at: datetime | None = None,
        resulting_challenge_dir: str | None = None,
        artifact_status: str | None = None,
        error: str | None = None,
    ) -> dto.BuildAttempt:
        if status not in {"succeeded", "failed", "lost"}:
            raise BuildAttemptPersistenceError(f"invalid terminal status {status!r}")
        if artifact_status is not None:
            _validate_artifact_status(artifact_status)
        row = self._lock(attempt_id)
        if row.status not in {"queued", "running"}:
            raise BuildAttemptPersistenceError(
                f"build attempt {attempt_id} is already terminal"
            )
        now = finished_at or _utcnow()
        if row.started_at is None:
            row.started_at = started_at or now
        if worker is not None:
            row.worker = worker
        row.status = status
        row.finished_at = now
        row.resulting_challenge_dir = resulting_challenge_dir
        if artifact_status is not None:
            row.artifact_status = artifact_status
        row.error = error
        self.session.flush()
        self.session.refresh(row)
        return _attempt(row)

    def update_artifact_status(
        self,
        attempt_id: UUID,
        artifact_status: str,
    ) -> dto.BuildAttempt:
        _validate_artifact_status(artifact_status)
        row = self._lock(attempt_id)
        if row.status not in {"succeeded", "failed", "lost"}:
            raise BuildAttemptPersistenceError(
                "artifact status can only be updated for a terminal attempt"
            )
        row.artifact_status = artifact_status
        self.session.flush()
        self.session.refresh(row)
        return _attempt(row)

    def _lock(self, attempt_id: UUID) -> model.BuildAttempt:
        row = self.session.scalars(
            sa.select(model.BuildAttempt)
            .where(model.BuildAttempt.id == attempt_id)
            .with_for_update()
        ).one_or_none()
        if row is None:
            raise BuildAttemptPersistenceError(
                f"build attempt {attempt_id} does not exist"
            )
        return row


def _validate_artifact_status(status: str) -> None:
    if status not in {"unknown", "present", "missing"}:
        raise BuildAttemptPersistenceError(f"invalid artifact status {status!r}")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _attempt(row: model.BuildAttempt) -> dto.BuildAttempt:
    return dto.BuildAttempt(
        id=row.id,
        design_task_id=row.design_task_id,
        attempt_no=row.attempt_no,
        status=row.status,
        shard_basename=row.shard_basename,
        worker=row.worker,
        resulting_challenge_dir=row.resulting_challenge_dir,
        artifact_status=row.artifact_status,
        error=row.error,
        idempotency_key=row.idempotency_key,
        created_at=row.created_at,
        started_at=row.started_at,
        finished_at=row.finished_at,
    )


def _list_item(
    row: model.BuildAttempt,
    *,
    generation_request_id: UUID,
    challenge_id: str,
    title: str,
    category: str,
    difficulty: str,
    percent: int,
) -> dto.BuildAttemptListItem:
    return dto.BuildAttemptListItem(
        **vars(_attempt(row)),
        generation_request_id=generation_request_id,
        challenge_id=challenge_id,
        title=title,
        category=category,
        difficulty=difficulty,
        percent=percent,
    )
