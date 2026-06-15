"""Short-transaction queue operations for research runs."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import sqlalchemy as sa

from domain import research as dto
from domain.research_validators import ResearchValidationError
from persistence.models import research as model
from persistence.repositories import ResearchRepository
from persistence.session import SessionFactory, transaction


class StaleClaimError(RuntimeError):
    """Raised when a token-fenced transition no longer owns the run."""


class ResearchAttemptError(RuntimeError):
    """Raised when persisted attempt state violates the retry contract."""


class ResearchJobService:
    """Owns research queue state changes and their transaction boundaries."""

    def __init__(self, repository_factory: SessionFactory | None = None) -> None:
        self.repository_factory = repository_factory

    def submit_request(
        self,
        category: str,
        topic: str,
        target_count: int,
        difficulty_distribution: Mapping[str, int],
        seed_urls: Sequence[str] = (),
        max_attempts: int = 3,
        runtime_constraints: Mapping[str, Any] | None = None,
    ) -> tuple[dto.GenerationRequest, dto.ResearchRun]:
        with transaction(factory=self.repository_factory) as session:
            repo = ResearchRepository(session)
            request = repo.create_generation_request(
                category=category,
                topic=topic,
                target_count=target_count,
                difficulty_distribution=difficulty_distribution,
                seed_urls=seed_urls,
                max_attempts=max_attempts,
                runtime_constraints=runtime_constraints,
                status="researching",
            )
            run = repo.create_run(generation_request_id=request.id, attempt=1, status="queued")
            return request, run

    def claim_next_run(
        self,
        agent_id: str,
        lease_seconds: int,
        *,
        expired_recovery_limit: int = 16,
    ) -> dto.ResearchRun | None:
        if lease_seconds <= 0:
            raise ValueError(f"lease_seconds must be positive, got {lease_seconds}")
        if expired_recovery_limit <= 0:
            raise ValueError(f"expired_recovery_limit must be positive, got {expired_recovery_limit}")

        with transaction(factory=self.repository_factory) as session:
            now = _utcnow()
            expired_rows = session.scalars(
                sa.select(model.ResearchRun)
                .where(
                    model.ResearchRun.status == "running",
                    model.ResearchRun.lease_expires_at < now,
                )
                .order_by(model.ResearchRun.lease_expires_at, model.ResearchRun.created_at)
                .limit(expired_recovery_limit)
                .with_for_update(skip_locked=True)
            ).all()
            for run in expired_rows:
                self._apply_run_failed(session, run, "lease expired", log_path=None)

            queued = session.scalars(
                sa.select(model.ResearchRun)
                .where(model.ResearchRun.status == "queued")
                .order_by(model.ResearchRun.created_at)
                .limit(1)
                .with_for_update(skip_locked=True)
            ).first()
            if queued is None:
                return None

            queued.status = "running"
            queued.claimed_by = agent_id
            queued.claim_token = uuid4()
            queued.claimed_at = now
            queued.heartbeat_at = now
            queued.lease_expires_at = now + timedelta(seconds=lease_seconds)
            session.flush()
            return _run_dto(queued)

    def heartbeat(
        self,
        run_id: UUID,
        agent_id: str,
        claim_token: UUID,
        lease_seconds: int,
    ) -> bool:
        if lease_seconds <= 0:
            raise ValueError(f"lease_seconds must be positive, got {lease_seconds}")
        now = _utcnow()
        with transaction(factory=self.repository_factory) as session:
            result = session.execute(
                sa.update(model.ResearchRun)
                .where(
                    model.ResearchRun.id == run_id,
                    model.ResearchRun.status == "running",
                    model.ResearchRun.claimed_by == agent_id,
                    model.ResearchRun.claim_token == claim_token,
                )
                .values(
                    heartbeat_at=now,
                    lease_expires_at=now + timedelta(seconds=lease_seconds),
                )
            )
            return result.rowcount == 1

    def mark_run_completed(
        self,
        run_id: UUID,
        agent_id: str,
        claim_token: UUID,
        *,
        log_path: str | Path,
    ) -> dto.ResearchRun:
        with transaction(factory=self.repository_factory) as session:
            run = self._get_owned_running_run(session, run_id, agent_id, claim_token)
            self._apply_run_completed(session, run, log_path)
            session.flush()
            return _run_dto(run)

    def mark_run_failed(
        self,
        run_id: UUID,
        agent_id: str,
        claim_token: UUID,
        last_error: str,
        *,
        log_path: str | Path | None = None,
    ) -> dto.ResearchRun:
        with transaction(factory=self.repository_factory) as session:
            run = self._get_owned_running_run(session, run_id, agent_id, claim_token)
            was_retried = self._apply_run_failed(session, run, last_error, log_path=log_path)
            session.flush()
            return _run_dto(run, was_retried=was_retried)

    def complete_run_with_results(
        self,
        run_id: UUID,
        agent_id: str,
        claim_token: UUID,
        *,
        sources: Sequence[Mapping[str, Any]],
        findings: Sequence[Mapping[str, Any]],
        binding_role: str,
        log_path: str | Path,
    ) -> dto.ResearchRun:
        with transaction(factory=self.repository_factory) as session:
            run = self._get_owned_running_run(session, run_id, agent_id, claim_token)
            repo = ResearchRepository(session)
            source_ids: list[UUID] = []
            for source in sources:
                saved = repo.add_source(
                    run_id,
                    url=str(source["url"]),
                    title=str(source["title"]),
                    summary=str(source["summary"]),
                    content_hash=str(source["content_hash"]),
                    fetched_at=_coerce_datetime(source.get("fetched_at")),
                    raw_text_path=_optional_str(source.get("raw_text_path")),
                )
                source_ids.append(saved.id)

            for finding in findings:
                finding_source_ids = _finding_source_ids(finding, source_ids)
                repo.create_finding(
                    run_id,
                    kind=str(finding["kind"]),
                    label=str(finding["label"]),
                    summary=str(finding["summary"]),
                    source_ids=finding_source_ids,
                )

            repo.touch_binding(binding_role, last_used_at=_utcnow(), last_used_run_id=run_id)
            self._apply_run_completed(session, run, log_path)
            session.flush()
            return _run_dto(run)

    def get_binding(self, role: str) -> dto.HermesProfileBinding | None:
        with transaction(factory=self.repository_factory) as session:
            return ResearchRepository(session).get_binding(role)

    def _get_owned_running_run(
        self,
        session,
        run_id: UUID,
        agent_id: str,
        claim_token: UUID,
    ) -> model.ResearchRun:
        run = session.scalars(
            sa.select(model.ResearchRun)
            .where(
                model.ResearchRun.id == run_id,
                model.ResearchRun.status == "running",
                model.ResearchRun.claimed_by == agent_id,
                model.ResearchRun.claim_token == claim_token,
            )
            .with_for_update()
        ).first()
        if run is None:
            raise StaleClaimError(f"run {run_id} is no longer running under this claim")
        return run

    def _apply_run_completed(
        self,
        session,
        run: model.ResearchRun,
        log_path: str | Path,
    ) -> None:
        now = _utcnow()
        run.status = "completed"
        run.finished_at = now
        run.last_error = None
        run.hermes_log_path = str(log_path)
        request = _get_request(session, run.generation_request_id)
        request.status = "researched"
        request.updated_at = now

    def _apply_run_failed(
        self,
        session,
        run: model.ResearchRun,
        last_error: str,
        *,
        log_path: str | Path | None,
    ) -> bool:
        if not last_error:
            raise ResearchValidationError("last_error is required when marking a run failed")
        request = _get_request(session, run.generation_request_id)
        if run.attempt > request.max_attempts:
            raise ResearchAttemptError(
                f"run attempt {run.attempt} exceeds max_attempts {request.max_attempts}"
            )

        now = _utcnow()
        run.status = "failed"
        run.finished_at = now
        run.last_error = last_error
        run.hermes_log_path = str(log_path) if log_path is not None else run.hermes_log_path

        if run.attempt < request.max_attempts:
            retry = model.ResearchRun(
                id=uuid4(),
                generation_request_id=run.generation_request_id,
                parent_run_id=run.id,
                attempt=run.attempt + 1,
                status="queued",
            )
            session.add(retry)
            request.status = "researching"
            was_retried = True
        else:
            request.status = "failed"
            was_retried = False
        request.updated_at = now
        return was_retried


def _get_request(session, request_id: UUID) -> model.GenerationRequest:
    request = session.get(model.GenerationRequest, request_id)
    if request is None:
        raise ResearchValidationError(f"generation_request {request_id} does not exist")
    return request


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _coerce_datetime(value: Any) -> datetime:
    if value is None:
        return _utcnow()
    if isinstance(value, datetime):
        return value
    raise ResearchValidationError(f"expected datetime for fetched_at, got {type(value).__name__}")


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _finding_source_ids(finding: Mapping[str, Any], source_ids: Sequence[UUID]) -> list[UUID]:
    if "source_ids" in finding:
        return list(finding["source_ids"])
    indices = finding.get("source_indices")
    if not isinstance(indices, Sequence):
        raise ResearchValidationError("finding must include source_indices or source_ids")
    resolved: list[UUID] = []
    for index in indices:
        if not isinstance(index, int):
            raise ResearchValidationError(f"source_indices must contain integers, got {index!r}")
        try:
            resolved.append(source_ids[index])
        except IndexError as exc:
            raise ResearchValidationError(f"source index {index} is out of range") from exc
    return resolved


def _run_dto(row: model.ResearchRun, *, was_retried: bool | None = None) -> dto.ResearchRun:
    return dto.ResearchRun(
        id=row.id,
        generation_request_id=row.generation_request_id,
        parent_run_id=row.parent_run_id,
        attempt=row.attempt,
        status=row.status,
        claimed_by=row.claimed_by,
        claim_token=row.claim_token,
        claimed_at=row.claimed_at,
        heartbeat_at=row.heartbeat_at,
        lease_expires_at=row.lease_expires_at,
        started_at=row.started_at,
        finished_at=row.finished_at,
        last_error=row.last_error,
        hermes_log_path=row.hermes_log_path,
        profile_name_used=row.profile_name_used,
        created_at=row.created_at,
        was_retried=was_retried,
    )
