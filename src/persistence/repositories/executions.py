"""Repository for execution rows: scheduling, claim/lease, fencing, reaping.

The build_attempt is a per-build-session container; each run is an execution
with a lease + fencing token. Scheduling and claim are separate transitions
(see capability ``worker-pool-execution``):

- ``schedule_execution`` locks the container, allocates ``iteration_no``,
  inserts a *queued* execution (null claim fields), and points the container's
  ``latest_execution_id`` at it.
- ``claim_queued`` locks that queued row, mints the token + lease, flips it to
  ``claimed``, sets ``current_execution_id`` and moves the container to running.

Every post-claim write (running, terminal, heartbeat) is fenced by the token
*and* by being the container's current execution; the reaper is the sole writer
that does not present a token (it is fenced by current-id + expired lease).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.orm import Session

from domain import executions as dto
from persistence.models import build_attempts as build_model
from persistence.models import executions as model


class ExecutionPersistenceError(ValueError):
    """Raised on invalid execution transitions or fencing violations."""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ExecutionsRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    # ----- reads -------------------------------------------------------------

    def get(self, execution_id: UUID) -> dto.Execution | None:
        row = self.session.get(model.Execution, execution_id)
        return _execution(row) if row else None

    def latest_for_attempt(self, build_attempt_id: UUID) -> dto.Execution | None:
        row = self.session.scalars(
            sa.select(model.Execution)
            .where(model.Execution.build_attempt_id == build_attempt_id)
            .order_by(model.Execution.iteration_no.desc())
            .limit(1)
        ).one_or_none()
        return _execution(row) if row else None

    def list_for_attempt(self, build_attempt_id: UUID) -> list[dto.Execution]:
        rows = self.session.scalars(
            sa.select(model.Execution)
            .where(model.Execution.build_attempt_id == build_attempt_id)
            .order_by(model.Execution.iteration_no.asc())
        ).all()
        return [_execution(row) for row in rows]

    # ----- scheduling --------------------------------------------------------

    def schedule_execution(
        self,
        build_attempt_id: UUID,
        *,
        execution_kind: str,
        parent_execution_id: UUID | None = None,
        feedback_snapshot_id: UUID | None = None,
        execution_mode: str = "standard",
        now: datetime | None = None,
    ) -> dto.Execution:
        if execution_kind not in dto.EXECUTION_KINDS:
            raise ExecutionPersistenceError(f"invalid execution_kind {execution_kind!r}")
        if execution_mode not in dto.EXECUTION_MODES:
            raise ExecutionPersistenceError(f"invalid execution_mode {execution_mode!r}")
        moment = _aware(now)
        container = self._lock_container(build_attempt_id)
        next_iter = self._next_iteration_no(build_attempt_id)
        row = model.Execution(
            id=uuid4(),
            build_attempt_id=build_attempt_id,
            parent_execution_id=parent_execution_id,
            iteration_no=next_iter,
            execution_kind=execution_kind,
            execution_mode=execution_mode,
            feedback_snapshot_id=feedback_snapshot_id,
            status="queued",
            created_at=moment,
        )
        self.session.add(row)
        self.session.flush()
        # Scheduling only advances `latest`; current stays null until claim.
        container.latest_execution_id = row.id
        container.status = "queued"
        container.finished_at = None
        container.error = None
        self.session.flush()
        self.session.refresh(row)
        return _execution(row)

    # ----- claim / lease -----------------------------------------------------

    def claim_queued(
        self,
        build_attempt_id: UUID,
        *,
        worker_id: str,
        lease_ttl_seconds: int,
        now: datetime | None = None,
    ) -> tuple[dto.Execution, UUID]:
        moment = _aware(now)
        # Lock order is execution-first, then container — matching `_fenced` and
        # the reaper so concurrent transitions on the same pair cannot deadlock.
        row = self.session.scalars(
            sa.select(model.Execution)
            .where(
                model.Execution.build_attempt_id == build_attempt_id,
                model.Execution.status == "queued",
            )
            .order_by(model.Execution.iteration_no.desc())
            .limit(1)
            .with_for_update()
        ).one_or_none()
        if row is None:
            raise ExecutionPersistenceError(
                f"no queued execution to claim for build attempt {build_attempt_id}"
            )
        container = self._lock_container(build_attempt_id)
        token = uuid4()
        row.claim_token = token
        row.worker_id = worker_id
        row.lease_expires_at = moment + timedelta(seconds=lease_ttl_seconds)
        row.heartbeat_at = moment
        row.status = "claimed"
        if container.started_at is None:
            container.started_at = moment
        container.current_execution_id = row.id
        container.latest_execution_id = row.id
        container.status = "running"
        self.session.flush()
        self.session.refresh(row)
        return _execution(row), token

    def update_to_running(
        self,
        execution_id: UUID,
        *,
        claim_token: UUID,
        now: datetime | None = None,
    ) -> dto.Execution:
        row, container = self._fenced(execution_id, claim_token)
        if row.status not in {"claimed", "running"}:
            raise ExecutionPersistenceError(
                f"execution {execution_id} is {row.status}, cannot mark running"
            )
        row.status = "running"
        if row.started_at is None:
            row.started_at = _aware(now)
        self.session.flush()
        self.session.refresh(row)
        return _execution(row)

    def update_to_terminal(
        self,
        execution_id: UUID,
        *,
        claim_token: UUID,
        status: str,
        error: str | None = None,
        exit_class: str | None = None,
        now: datetime | None = None,
    ) -> dto.Execution:
        if status not in dto.TERMINAL_STATUSES:
            raise ExecutionPersistenceError(f"invalid terminal status {status!r}")
        row, container = self._fenced(execution_id, claim_token)
        moment = _aware(now)
        row.status = status
        row.error = error
        row.exit_class = exit_class
        row.finished_at = moment
        # Current execution reached terminal: clear current, keep latest.
        container.current_execution_id = None
        if container.latest_execution_id == row.id:
            container.status = dto.CONTAINER_STATUS_BY_EXECUTION[status]
            container.finished_at = moment
            container.error = error  # compatibility aggregate mirrors latest
        self.session.flush()
        self.session.refresh(row)
        return _execution(row)

    def heartbeat(
        self,
        execution_id: UUID,
        *,
        claim_token: UUID,
        lease_ttl_seconds: int,
        now: datetime | None = None,
    ) -> dto.Execution:
        # Three-gate fence: token equality AND active status AND is-current.
        row, container = self._fenced(execution_id, claim_token, require_active=True)
        moment = _aware(now)
        row.lease_expires_at = moment + timedelta(seconds=lease_ttl_seconds)
        row.heartbeat_at = moment
        self.session.flush()
        self.session.refresh(row)
        return _execution(row)

    # ----- reaper ------------------------------------------------------------

    def reap_expired(self, *, now: datetime | None = None) -> list[UUID]:
        """Terminally mark expired current executions lost. Returns reaped ids."""
        moment = _aware(now)
        rows = self.session.scalars(
            sa.select(model.Execution)
            .where(
                model.Execution.status.in_(dto.ACTIVE_STATUSES),
                model.Execution.lease_expires_at < moment,
            )
            .with_for_update()
        ).all()
        reaped: list[UUID] = []
        for row in rows:
            # Lock the container (execution already locked above → execution-first
            # ordering preserved) to avoid a TOCTOU with a concurrent claim.
            container = self.session.scalars(
                sa.select(build_model.BuildAttempt)
                .where(build_model.BuildAttempt.id == row.build_attempt_id)
                .with_for_update()
            ).one_or_none()
            # Reaper fence: only the container's current execution is reaped.
            if container is None or container.current_execution_id != row.id:
                continue
            message = row.error or "lease expired"
            row.status = "lost"
            row.error = message
            row.exit_class = row.exit_class or "lease_expired"
            row.finished_at = moment
            container.current_execution_id = None
            if container.latest_execution_id == row.id:
                container.status = "lost"
                container.finished_at = moment
                container.error = message
            reaped.append(row.id)
        self.session.flush()
        return reaped

    def set_successful_execution(
        self, build_attempt_id: UUID, execution_id: UUID
    ) -> None:
        container = self.session.get(build_model.BuildAttempt, build_attempt_id)
        if container is None:
            raise ExecutionPersistenceError(
                f"build attempt {build_attempt_id} does not exist"
            )
        container.successful_execution_id = execution_id
        self.session.flush()

    # ----- internals ---------------------------------------------------------

    def _lock_container(self, build_attempt_id: UUID) -> build_model.BuildAttempt:
        container = self.session.scalars(
            sa.select(build_model.BuildAttempt)
            .where(build_model.BuildAttempt.id == build_attempt_id)
            .with_for_update()
        ).one_or_none()
        if container is None:
            raise ExecutionPersistenceError(
                f"build attempt {build_attempt_id} does not exist"
            )
        return container

    def _next_iteration_no(self, build_attempt_id: UUID) -> int:
        current_max = self.session.scalar(
            sa.select(sa.func.max(model.Execution.iteration_no)).where(
                model.Execution.build_attempt_id == build_attempt_id
            )
        )
        return int(current_max or 0) + 1

    def _fenced(
        self,
        execution_id: UUID,
        claim_token: UUID,
        *,
        require_active: bool = False,
    ) -> tuple[model.Execution, build_model.BuildAttempt]:
        row = self.session.scalars(
            sa.select(model.Execution)
            .where(model.Execution.id == execution_id)
            .with_for_update()
        ).one_or_none()
        if row is None:
            raise ExecutionPersistenceError(f"execution {execution_id} does not exist")
        if row.claim_token is None or row.claim_token != claim_token:
            raise ExecutionPersistenceError("stale or missing claim token")
        container = self._lock_container(row.build_attempt_id)
        if container.current_execution_id != row.id:
            raise ExecutionPersistenceError(
                "execution is not the container's current execution"
            )
        if require_active and row.status not in dto.ACTIVE_STATUSES:
            raise ExecutionPersistenceError(
                f"execution {execution_id} is {row.status}, not active"
            )
        return row, container


def _aware(value: datetime | None) -> datetime:
    moment = value or _utcnow()
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment


def _execution(row: model.Execution) -> dto.Execution:
    return dto.Execution(
        id=row.id,
        build_attempt_id=row.build_attempt_id,
        parent_execution_id=row.parent_execution_id,
        iteration_no=row.iteration_no,
        execution_kind=row.execution_kind,
        execution_mode=row.execution_mode,
        feedback_snapshot_id=row.feedback_snapshot_id,
        worker_id=row.worker_id,
        claim_token=row.claim_token,
        lease_expires_at=row.lease_expires_at,
        heartbeat_at=row.heartbeat_at,
        status=row.status,
        exit_class=row.exit_class,
        error=row.error,
        started_at=row.started_at,
        finished_at=row.finished_at,
        created_at=row.created_at,
    )
