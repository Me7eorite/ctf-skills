"""Re-run host validation for an existing failed build attempt."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

import sqlalchemy as sa

from core.docker import image_exists as default_image_exists
from core.jsonio import read_json
from core.paths import ProjectPaths
from core.queue import ShardQueue
from core.state import ProgressStore
from domain.resume import ChallengeResumePlan, find_challenge_directory
from domain.validation import ChallengeValidator
from hermes.validation import record_per_challenge_complete, run_validation
from persistence.models import build_attempts as build_model
from persistence.models import design_tasks as task_model
from persistence.repositories import BuildAttemptsRepository
from persistence.session import SessionFactory, transaction

REVALIDATION_WORKER = "dashboard-revalidate"


class BuildAttemptRevalidationError(ValueError):
    """Raised when a build attempt cannot be revalidated or remains invalid."""


@dataclass(frozen=True)
class BuildAttemptRevalidationResult:
    """Result of a successful same-attempt revalidation."""

    attempt_id: UUID


class BuildAttemptRevalidationService:
    """Repair host-validation failures without invoking Hermes or creating attempts."""

    def __init__(
        self,
        *,
        paths: ProjectPaths | None = None,
        progress: ProgressStore,
        session_factory: SessionFactory | None = None,
        validator: ChallengeValidator | None = None,
        image_exists: Callable[[str], bool] = default_image_exists,
        worker: str = REVALIDATION_WORKER,
    ) -> None:
        self.paths = paths or ProjectPaths.discover()
        self.progress = progress
        self.session_factory = session_factory or SessionFactory()
        self.validator = validator or ChallengeValidator(self.paths)
        self.image_exists = image_exists
        self.worker = worker

    def revalidate(self, attempt_id: UUID) -> BuildAttemptRevalidationResult:
        attempt, challenge_ids = self._prepare(attempt_id)
        plans = self._current_plans(challenge_ids)
        results = run_validation(
            state=self.progress,
            validator=self.validator,
            paths=self.paths,
            image_exists=self.image_exists,
            original_shard_name=attempt.shard_basename,
            worker=self.worker,
            challenge_ids=challenge_ids,
            plan_by_id=plans,
        )
        record_per_challenge_complete(
            self.progress,
            attempt.shard_basename,
            self.worker,
            results,
        )
        failures = [result for result in results if result.get("solve_status") != "passed"]
        if failures:
            reason = _failure_reason(failures[0])
            self._mark_failed(attempt.id, reason)
            raise BuildAttemptRevalidationError(reason)

        challenge_dir = _relative_challenge_dir(self.paths, plans[challenge_ids[0]])
        self._mark_succeeded(
            attempt.id,
            shard_basename=attempt.shard_basename,
            challenge_dir=challenge_dir,
        )
        return BuildAttemptRevalidationResult(attempt_id=attempt.id)

    def _prepare(self, attempt_id: UUID):
        with transaction(factory=self.session_factory) as session:
            row = session.scalars(
                sa.select(build_model.BuildAttempt)
                .where(build_model.BuildAttempt.id == attempt_id)
                .with_for_update()
            ).one_or_none()
            if row is None:
                raise BuildAttemptRevalidationError(
                    f"build attempt {attempt_id} does not exist"
                )
            if row.status != "failed":
                raise BuildAttemptRevalidationError(
                    f"build attempt is {row.status}, expected failed"
                )
            latest = BuildAttemptsRepository(session).latest_for_design_task(
                row.design_task_id
            )
            if latest is None or latest.id != row.id:
                raise BuildAttemptRevalidationError(
                    "only the latest build attempt can be revalidated"
                )
            payload = self._failed_payload(
                row.shard_basename,
                attempt_id=row.id,
                design_task_id=row.design_task_id,
            )
            challenge_ids = _challenge_ids(payload)
            if not challenge_ids:
                raise BuildAttemptRevalidationError(
                    "failed shard has no challenge ids"
                )
            if len(challenge_ids) != 1:
                raise BuildAttemptRevalidationError(
                    "failed shard must contain exactly one challenge id"
                )
            return BuildAttemptsRepository(session).get(row.id), challenge_ids

    def _failed_payload(
        self,
        shard_basename: str,
        *,
        attempt_id: UUID,
        design_task_id: UUID,
    ) -> Mapping[str, Any]:
        if Path(shard_basename).name != shard_basename:
            raise BuildAttemptRevalidationError("build attempt shard basename is invalid")
        shard = self.paths.shards / "failed" / shard_basename
        done = self.paths.shards / "done" / shard_basename
        if done.exists():
            raise BuildAttemptRevalidationError("done shard already exists")
        if shard.is_symlink() or not shard.is_file():
            raise BuildAttemptRevalidationError("failed shard is missing")
        payload = read_json(shard, None)
        if not isinstance(payload, Mapping):
            raise BuildAttemptRevalidationError("failed shard payload is invalid")
        if str(payload.get("build_attempt_id")) != str(attempt_id):
            raise BuildAttemptRevalidationError(
                "failed shard build_attempt_id does not match"
            )
        if str(payload.get("design_task_id")) != str(design_task_id):
            raise BuildAttemptRevalidationError(
                "failed shard design_task_id does not match"
            )
        return payload

    def _current_plans(
        self,
        challenge_ids: list[str],
    ) -> dict[str, ChallengeResumePlan]:
        plans: dict[str, ChallengeResumePlan] = {}
        for challenge_id in challenge_ids:
            lookup = find_challenge_directory(self.paths, challenge_id)
            plans[challenge_id] = ChallengeResumePlan(
                challenge_id=challenge_id,
                directory=lookup.directory,
                lookup_status=lookup.status,
                skipped_stages=(),
                first_pending_stage="validate",
                stage_sources={},
            )
        return plans

    def _move_failed_shard_to_done(self, shard_basename: str) -> None:
        source = self.paths.shards / "failed" / shard_basename
        destination = self.paths.shards / "done" / shard_basename
        if not source.is_file():
            raise BuildAttemptRevalidationError("failed shard is missing")
        if destination.exists():
            raise BuildAttemptRevalidationError("done shard already exists")
        destination.parent.mkdir(parents=True, exist_ok=True)
        source.replace(destination)
        claim_source = ShardQueue._claim_path(source)
        if claim_source.exists():
            claim_source.replace(ShardQueue._claim_path(destination))

    def _mark_failed(self, attempt_id: UUID, reason: str) -> None:
        now = datetime.now(timezone.utc)
        with transaction(factory=self.session_factory) as session:
            current = session.get(build_model.BuildAttempt, attempt_id)
            if current is None:
                raise BuildAttemptRevalidationError(
                    f"build attempt {attempt_id} does not exist"
                )
            task = _lock_task(session, current.design_task_id)
            row = session.scalars(
                sa.select(build_model.BuildAttempt)
                .where(build_model.BuildAttempt.id == attempt_id)
                .with_for_update()
            ).one()
            latest = BuildAttemptsRepository(session).latest_for_design_task(
                row.design_task_id
            )
            if latest is None or latest.id != row.id or row.status != "failed":
                raise BuildAttemptRevalidationError(
                    "build attempt changed during revalidation"
                )
            row.status = "failed"
            row.error = reason
            row.finished_at = now
            if task is not None:
                task.status = "build_failed"
                task.updated_at = now

    def _mark_succeeded(
        self,
        attempt_id: UUID,
        *,
        shard_basename: str,
        challenge_dir: str,
    ) -> None:
        now = datetime.now(timezone.utc)
        with transaction(factory=self.session_factory) as session:
            current = session.get(build_model.BuildAttempt, attempt_id)
            if current is None:
                raise BuildAttemptRevalidationError(
                    f"build attempt {attempt_id} does not exist"
                )
            task = _lock_task(session, current.design_task_id)
            row = session.scalars(
                sa.select(build_model.BuildAttempt)
                .where(build_model.BuildAttempt.id == attempt_id)
                .with_for_update()
            ).one()
            latest = BuildAttemptsRepository(session).latest_for_design_task(
                row.design_task_id
            )
            if latest is None or latest.id != row.id or row.status != "failed":
                raise BuildAttemptRevalidationError(
                    "build attempt changed during revalidation"
                )
            self._failed_payload(
                shard_basename,
                attempt_id=row.id,
                design_task_id=row.design_task_id,
            )
            self._move_failed_shard_to_done(shard_basename)
            row.status = "succeeded"
            row.worker = row.worker or self.worker
            row.started_at = row.started_at or now
            row.finished_at = now
            row.resulting_challenge_dir = challenge_dir
            row.artifact_status = "present"
            row.error = None
            if task is not None:
                task.status = "built"
                task.updated_at = now


def _challenge_ids(payload: Mapping[str, Any]) -> list[str]:
    challenges = payload.get("challenges")
    if not isinstance(challenges, list):
        return []
    return [
        challenge["id"]
        for challenge in challenges
        if isinstance(challenge, Mapping)
        and isinstance(challenge.get("id"), str)
        and challenge["id"]
    ]


def _failure_reason(result: Mapping[str, Any]) -> str:
    status = str(result.get("validation_status") or "failed")
    error = result.get("validation_error")
    if error:
        return f"{status}: {error}"
    return status


def _relative_challenge_dir(paths: ProjectPaths, plan: ChallengeResumePlan) -> str:
    if plan.directory is None:
        raise BuildAttemptRevalidationError(plan.lookup_status)
    try:
        return plan.directory.resolve().relative_to(paths.root.resolve()).as_posix()
    except ValueError as exc:
        raise BuildAttemptRevalidationError(
            "challenge directory is outside project root"
        ) from exc


def _lock_task(session, task_id: UUID) -> task_model.DesignTask | None:
    return session.scalars(
        sa.select(task_model.DesignTask)
        .where(task_model.DesignTask.id == task_id)
        .with_for_update()
    ).one_or_none()
