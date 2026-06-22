"""Submit validated challenge designs to the file-backed shard queue."""

from __future__ import annotations

import logging
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.orm import Session

from core.execution_config import execution_minting_enabled
from core.jsonio import read_json, write_json
from core.paths import ProjectPaths
from domain import challenge_designs as design_dto
from domain import design_tasks as task_dto
from persistence.models import build_attempts as build_model
from persistence.models import design_tasks as task_model
from persistence.models import executions as exec_model
from persistence.repositories import (
    BuildAttemptsRepository,
    ChallengeDesignRepository,
    DesignTaskRepository,
    ExecutionsRepository,
)
from persistence.session import SessionFactory, transaction

LOG = logging.getLogger(__name__)
STAGING_ORPHAN_GRACE_SECONDS = 60 * 60

MATRIX_FIELDS: dict[str, tuple[str, ...]] = {
    "web": (
        "id",
        "title",
        "category",
        "difficulty",
        "points",
        "template",
        "deployment",
        "runtime",
        "framework",
        "port",
        "primary_technique",
        "learning_objective",
        "distinctness",
    ),
    "pwn": (
        "id",
        "title",
        "category",
        "difficulty",
        "points",
        "template",
        "deployment",
        "language",
        "compiler",
        "target_format",
        "architecture",
        "port",
        "mitigations",
        "primary_technique",
        "learning_objective",
    ),
    "re": (
        "id",
        "title",
        "category",
        "difficulty",
        "points",
        "template",
        "deployment",
        "language",
        "compiler",
        "target_format",
        "target_platform",
        "strip",
        "primary_technique",
        "learning_objective",
    ),
}


class BuildOrchestrationError(ValueError):
    """Raised when a task cannot be submitted under the build contract."""

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class _PreparedSubmission:
    attempt_id: UUID
    task: task_dto.DesignTask
    design: design_dto.ChallengeDesign
    shard_basename: str
    resume_from_shard_basename: str | None
    payload: dict[str, Any]
    execution_mode: str
    idempotency_key: str | None
    # Option A (execution-minting) fields; populated only when the cutover flag
    # is on. ``attempt_id`` is the container id for both fresh and retry paths.
    minting: bool = False
    is_fresh: bool = True
    iteration_no: int = 1
    execution_kind: str = "initial"


class BuildOrchestrationService:
    """Bridge PostgreSQL-owned build intent into the file shard queue."""

    def __init__(
        self,
        *,
        paths: ProjectPaths | None = None,
        session_factory: SessionFactory | None = None,
    ) -> None:
        self.paths = paths or ProjectPaths.discover()
        self.session_factory = session_factory or SessionFactory()

    def submit_batch(self, design_task_ids: Sequence[UUID]) -> list[UUID]:
        """Validate, stage, commit, then best-effort publish a task batch."""
        ids = list(design_task_ids)
        if not ids:
            raise BuildOrchestrationError("at least one design task is required")
        if len(set(ids)) != len(ids):
            raise BuildOrchestrationError("duplicate design task ids are not allowed")
        return self._submit(ids, retry_sources={}, execution_mode="resume")

    def submit_single(self, design_task_id: UUID) -> UUID:
        """Submit one task through the exact batch path."""
        return self.submit_batch([design_task_id])[0]

    def retry(self, build_attempt_id: UUID) -> UUID:
        """Retry only the latest failed/lost attempt of a build-failed task."""
        with self._session() as session:
            build_repo = BuildAttemptsRepository(session)
            source = build_repo.get(build_attempt_id)
            if source is None:
                raise BuildOrchestrationError(f"build attempt {build_attempt_id} does not exist")
            latest = build_repo.latest_for_design_task(source.design_task_id)
            task = DesignTaskRepository(session).get_design_task(source.design_task_id)
            if latest is None or latest.id != source.id:
                raise BuildOrchestrationError("only the latest build attempt can be retried")
            if source.status not in {"failed", "lost"}:
                raise BuildOrchestrationError("only failed or lost attempts can be retried")
            if task is None or task.status != "build_failed":
                raise BuildOrchestrationError("retry requires a parent task in build_failed status")
        return self._submit(
            [source.design_task_id],
            retry_sources={source.design_task_id: source.id},
            execution_mode="resume",
        )[0]

    def clean_rebuild(
        self,
        build_attempt_id: UUID,
        *,
        idempotency_key: str,
        confirmed: bool,
    ) -> UUID:
        """Clean rebuild — same eligibility as retry, separate execution mode.

        Same-key replays resolve to the existing row (UNIQUE constraint on
        ``build_attempts.idempotency_key`` enforces single-row collapse).
        Different-key submissions are NOT promised to collapse in this
        proposal; that protection waits for proposal #3's lease/fencing.
        """
        if not idempotency_key:
            raise BuildOrchestrationError("idempotency_key is required", code="idempotency_key_required")
        if not confirmed:
            raise BuildOrchestrationError("confirmation_required", code="confirmation_required")
        with self._session() as session:
            build_repo = BuildAttemptsRepository(session)
            replay = build_repo.find_by_idempotency_key(idempotency_key)
            if replay is not None:
                return replay.id
            source = build_repo.get(build_attempt_id)
            if source is None:
                raise BuildOrchestrationError(f"build attempt {build_attempt_id} does not exist")
            latest = build_repo.latest_for_design_task(source.design_task_id)
            task = DesignTaskRepository(session).get_design_task(source.design_task_id)
            if latest is None or latest.id != source.id:
                raise BuildOrchestrationError(
                    "only the latest build attempt can be clean rebuilt",
                    code="stale_source_attempt",
                )
            if source.status not in {"failed", "lost"}:
                raise BuildOrchestrationError("only failed or lost attempts can be clean rebuilt")
            if task is None or task.status != "build_failed":
                raise BuildOrchestrationError("clean rebuild requires a parent task in build_failed status")
        try:
            return self._submit(
                [source.design_task_id],
                retry_sources={source.design_task_id: source.id},
                execution_mode="clean",
                idempotency_key=idempotency_key,
            )[0]
        except (sa.exc.IntegrityError, BuildOrchestrationError):
            # The loser can observe either the UNIQUE violation or the task
            # status transition during commit-time eligibility re-check.
            with self._session() as session:
                build_repo = BuildAttemptsRepository(session)
                existing = build_repo.find_by_idempotency_key(idempotency_key)
            if existing is None:
                raise
            return existing.id

    def render_shard_payload(
        self,
        design_task: task_dto.DesignTask,
        latest_design: design_dto.ChallengeDesign,
        *,
        build_attempt_id: UUID,
        resume_from_shard_basename: str | None = None,
        execution_mode: str = "resume",
    ) -> dict[str, Any]:
        """Render one attributed shard without filesystem or database effects."""
        challenge = _design_challenge(latest_design.payload)
        matrix_values = _matrix_values(design_task, challenge)
        fields = MATRIX_FIELDS.get(design_task.category)
        if fields is None:
            raise BuildOrchestrationError(f"unsupported challenge category {design_task.category!r}")
        rendered_challenge = {field: matrix_values[field] for field in fields}
        rendered_challenge["design"] = dict(latest_design.payload)
        payload: dict[str, Any] = {
            "build_attempt_id": str(build_attempt_id),
            "design_task_id": str(design_task.id),
            "challenges": [rendered_challenge],
        }
        if execution_mode == "clean":
            payload["execution_mode"] = "clean"
            if resume_from_shard_basename is not None:
                raise BuildOrchestrationError("explicit clean rebuild forbids resume_from_shard_basename")
        else:
            if resume_from_shard_basename is not None:
                payload["execution_mode"] = "resume"
                payload["resume_from_shard_basename"] = resume_from_shard_basename
        return payload

    def recover_staging(self, *, now: float | None = None) -> set[UUID]:
        """Publish committed staging files and remove only old uncommitted orphans.

        The returned ids had a committed queued row and a matching staging
        payload when scanned. A caller may count them as present for its tick
        even if publication failed again.
        """
        self.paths.build_attempt_staging.mkdir(parents=True, exist_ok=True)
        current_time = time.time() if now is None else now
        present: set[UUID] = set()
        with self._session() as session:
            repo = BuildAttemptsRepository(session)
            for staged in sorted(self.paths.build_attempt_staging.glob("*.json")):
                try:
                    attempt_id = UUID(staged.stem)
                except ValueError:
                    self._remove_old_orphan(staged, current_time)
                    continue
                attempt = repo.get(attempt_id)
                if attempt is None:
                    self._remove_old_orphan(staged, current_time)
                    continue
                if attempt.status != "queued":
                    continue
                payload = read_json(staged, None)
                if not _payload_matches_attempt(
                    payload,
                    attempt_id=attempt.id,
                    design_task_id=attempt.design_task_id,
                ):
                    LOG.warning("ignoring mismatched staged build payload %s", staged)
                    continue
                present.add(attempt.id)
                try:
                    self._publish(staged, attempt.shard_basename)
                except Exception as exc:
                    LOG.warning(
                        "failed to recover staged build attempt %s: %s",
                        attempt.id,
                        exc,
                    )
        return present

    def _submit(
        self,
        design_task_ids: list[UUID],
        *,
        retry_sources: Mapping[UUID, UUID],
        execution_mode: str = "resume",
        idempotency_key: str | None = None,
    ) -> list[UUID]:
        prepared = self._prepare(
            design_task_ids,
            retry_sources=retry_sources,
            execution_mode=execution_mode,
            idempotency_key=idempotency_key,
        )
        self.paths.initialize()
        staged_paths: list[Path] = []
        try:
            for submission in prepared:
                staged_paths.append(self._write_staged_payload(submission))
            self._commit(prepared, retry_sources=retry_sources)
        except BaseException:
            for path in staged_paths:
                path.unlink(missing_ok=True)
                path.with_suffix(path.suffix + ".tmp").unlink(missing_ok=True)
            raise

        for submission, staged in zip(prepared, staged_paths, strict=True):
            try:
                self._publish(staged, submission.shard_basename)
            except Exception as exc:
                LOG.warning(
                    "build attempt %s committed but publication failed: %s",
                    submission.attempt_id,
                    exc,
                )
        return [item.attempt_id for item in prepared]

    def _prepare(
        self,
        design_task_ids: list[UUID],
        *,
        retry_sources: Mapping[UUID, UUID],
        execution_mode: str = "resume",
        idempotency_key: str | None = None,
    ) -> list[_PreparedSubmission]:
        if execution_mode not in {"resume", "clean"}:
            raise BuildOrchestrationError(f"unsupported execution_mode {execution_mode!r}")
        prepared: list[_PreparedSubmission] = []
        with self._session() as session:
            task_repo = DesignTaskRepository(session)
            design_repo = ChallengeDesignRepository(session)
            build_repo = BuildAttemptsRepository(session)
            for task_id in design_task_ids:
                task = task_repo.get_design_task(task_id)
                if task is None:
                    raise BuildOrchestrationError(f"design task {task_id} does not exist")
                resume_from = self._validate_task_for_submit(
                    task,
                    build_repo,
                    expected_source_id=retry_sources.get(task_id),
                    execution_mode=execution_mode,
                )
                # Clean mode anchors eligibility on the source attempt id but
                # SHALL NOT emit resume_from_shard_basename in the shard payload.
                payload_resume_from = None if execution_mode == "clean" else resume_from
                design = design_repo.latest_design(task_id)
                if design is None:
                    raise BuildOrchestrationError(f"design task {task_id} has no validated draft design")

                minting = execution_minting_enabled()
                source_attempt_id = retry_sources.get(task_id)
                # Option A: a retry reuses the existing build-attempt container and
                # appends an execution; legacy (or fresh) mints a new container.
                is_retry = minting and source_attempt_id is not None
                if is_retry:
                    container_id = source_attempt_id
                    iteration_no = self._next_iteration_no(session, container_id)
                    # resume/clean retries are kind=retry; revision is a separate
                    # feedback-triggered entry point added later.
                    execution_kind = "retry"
                else:
                    container_id = uuid4()
                    iteration_no = 1
                    execution_kind = "initial"
                if minting:
                    basename = f"{container_id}.iter-{iteration_no:03d}.json"
                else:
                    basename = f"{container_id}.json"
                payload = self.render_shard_payload(
                    task,
                    design,
                    build_attempt_id=container_id,
                    resume_from_shard_basename=payload_resume_from,
                    execution_mode=execution_mode,
                )
                prepared.append(
                    _PreparedSubmission(
                        attempt_id=container_id,
                        task=task,
                        design=design,
                        shard_basename=basename,
                        resume_from_shard_basename=payload_resume_from,
                        payload=payload,
                        execution_mode=execution_mode,
                        idempotency_key=idempotency_key,
                        minting=minting,
                        is_fresh=not is_retry,
                        iteration_no=iteration_no,
                        execution_kind=execution_kind,
                    )
                )
        return prepared

    @staticmethod
    def _next_iteration_no(session: Session, container_id: UUID) -> int:
        current_max = session.scalar(
            sa.select(sa.func.max(exec_model.Execution.iteration_no)).where(
                exec_model.Execution.build_attempt_id == container_id
            )
        )
        return int(current_max or 0) + 1

    def _commit(
        self,
        prepared: Sequence[_PreparedSubmission],
        *,
        retry_sources: Mapping[UUID, UUID],
    ) -> None:
        with transaction(factory=self.session_factory) as session:
            build_repo = BuildAttemptsRepository(session)
            locked_tasks: dict[UUID, task_model.DesignTask] = {}
            # Stable lock order prevents two overlapping batches deadlocking.
            for task_id in sorted((item.task.id for item in prepared), key=str):
                row = session.scalars(
                    sa.select(task_model.DesignTask).where(task_model.DesignTask.id == task_id).with_for_update()
                ).one_or_none()
                if row is None:
                    raise BuildOrchestrationError(f"design task {task_id} disappeared before commit")
                locked_tasks[task_id] = row

            for submission in prepared:
                row = locked_tasks[submission.task.id]
                current = DesignTaskRepository(session).get_design_task(row.id)
                if current is None:
                    raise BuildOrchestrationError(f"design task {row.id} disappeared before commit")
                self._validate_task_for_submit(
                    current,
                    build_repo,
                    expected_source_id=retry_sources.get(row.id),
                    execution_mode=submission.execution_mode,
                )
                if submission.is_fresh:
                    build_repo.create_attempt(
                        row.id,
                        submission.shard_basename,
                        attempt_id=submission.attempt_id,
                        idempotency_key=submission.idempotency_key,
                    )
                if submission.minting:
                    exec_repo = ExecutionsRepository(session)
                    parent_execution_id = None
                    mode = "clean" if submission.execution_mode == "clean" else "standard"
                    if not submission.is_fresh:
                        container = session.get(
                            build_model.BuildAttempt, submission.attempt_id
                        )
                        parent_execution_id = (
                            container.latest_execution_id if container else None
                        )
                        # Keep the legacy shard_basename in sync so reconciler and
                        # runner attribute the reused container's current iteration.
                        if container is not None:
                            container.shard_basename = submission.shard_basename
                    exec_repo.schedule_execution(
                        submission.attempt_id,
                        execution_kind=submission.execution_kind,
                        parent_execution_id=parent_execution_id,
                        execution_mode=mode,
                    )
                row.status = "building"
                row.updated_at = datetime.now(timezone.utc)

    def _validate_task_for_submit(
        self,
        task: task_dto.DesignTask,
        build_repo: BuildAttemptsRepository,
        *,
        expected_source_id: UUID | None,
        execution_mode: str = "resume",
    ) -> str | None:
        clean = execution_mode == "clean"
        if task.status not in {"designed", "build_failed"}:
            if clean and expected_source_id is not None:
                raise BuildOrchestrationError(
                    "clean rebuild source is no longer eligible",
                    code="stale_source_attempt",
                )
            raise BuildOrchestrationError(f"design task {task.id} is {task.status}; expected designed or build_failed")
        latest = build_repo.latest_for_design_task(task.id)
        if task.status == "designed":
            if expected_source_id is not None:
                raise BuildOrchestrationError("retry requires build_failed parent status")
            return None
        if latest is None or latest.status not in {"failed", "lost"}:
            if clean:
                raise BuildOrchestrationError(
                    "clean rebuild source is no longer eligible",
                    code="stale_source_attempt",
                )
            raise BuildOrchestrationError("build_failed task requires a latest failed or lost attempt")
        if expected_source_id is not None and latest.id != expected_source_id:
            if clean:
                raise BuildOrchestrationError(
                    "only the latest build attempt can be clean rebuilt",
                    code="stale_source_attempt",
                )
            raise BuildOrchestrationError("only the latest build attempt can be retried")
        return latest.shard_basename

    def _write_staged_payload(self, submission: _PreparedSubmission) -> Path:
        destination = self.paths.build_attempt_staging / f"{submission.attempt_id}.json"
        temporary = destination.with_suffix(destination.suffix + ".tmp")
        try:
            write_json(temporary, submission.payload)
            temporary.replace(destination)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
        return destination

    def _publish(self, staged: Path, shard_basename: str) -> None:
        destination = self.paths.shards / "pending" / shard_basename
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            payload = read_json(destination, None)
            if isinstance(payload, Mapping) and str(payload.get("build_attempt_id")) == staged.stem:
                staged.unlink(missing_ok=True)
                return
            raise FileExistsError(f"pending shard {destination.name} already exists for another attempt")
        staged.replace(destination)

    def _remove_old_orphan(self, staged: Path, now: float) -> None:
        try:
            age = now - staged.stat().st_mtime
        except OSError:
            return
        if age > STAGING_ORPHAN_GRACE_SECONDS:
            staged.unlink(missing_ok=True)

    def _session(self) -> Session:
        return self.session_factory()


def _design_challenge(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    challenges = payload.get("challenges")
    if not isinstance(challenges, list) or len(challenges) != 1:
        raise BuildOrchestrationError("validated design must contain one challenge")
    challenge = challenges[0]
    if not isinstance(challenge, Mapping):
        raise BuildOrchestrationError("validated design challenge must be an object")
    return challenge


def _matrix_values(
    task: task_dto.DesignTask,
    challenge: Mapping[str, Any],
) -> dict[str, Any]:
    plan = challenge.get("implementation_plan")
    plan = plan if isinstance(plan, Mapping) else {}
    constraints = task.constraints

    def value(name: str, default: Any) -> Any:
        return challenge.get(name, plan.get(name, constraints.get(name, default)))

    return {
        "id": task.challenge_id,
        "title": task.title,
        "category": task.category,
        "difficulty": task.difficulty,
        "points": task.points,
        "template": value("template", f"{task.category}-designed"),
        "deployment": value("deployment", "download"),
        "runtime": value("runtime", "unspecified"),
        "framework": value("framework", "unspecified"),
        "port": task.port,
        "language": value("language", "c"),
        "compiler": value("compiler", "gcc"),
        "target_format": value("target_format", "elf"),
        "architecture": value("architecture", "x86_64"),
        "mitigations": value("mitigations", {}),
        "target_platform": value("target_platform", "linux/amd64"),
        "strip": value("strip", True),
        "primary_technique": task.primary_technique,
        "learning_objective": task.learning_objective,
        "distinctness": value("distinctness", task.scenario or task.primary_technique),
    }


def _payload_matches_attempt(
    payload: Any,
    *,
    attempt_id: UUID,
    design_task_id: UUID,
) -> bool:
    return bool(
        isinstance(payload, Mapping)
        and str(payload.get("build_attempt_id")) == str(attempt_id)
        and str(payload.get("design_task_id")) == str(design_task_id)
    )
