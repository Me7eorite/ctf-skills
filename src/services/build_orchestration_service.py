"""Submit validated challenge designs to the file-backed shard queue."""

from __future__ import annotations

import logging
import os
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
from core.state import EXECUTION_STAGES
from domain import challenge_designs as design_dto
from domain import design_tasks as task_dto
from domain.design.difficulty_review import DifficultyReviewResult
from domain.generation_profile import generation_profile
from domain.validation_failure_governance import latest_failed_validation, summarize_validation_entry
from persistence.models import build_attempts as build_model
from persistence.models import design_tasks as task_model
from persistence.models import executions as exec_model
from persistence.models.progress import ProgressEvent, ProgressSnapshot
from persistence.repositories import (
    BuildAttemptsRepository,
    ChallengeDesignRepository,
    DesignDifficultyReviewRepository,
    DesignTaskRepository,
    ExecutionsRepository,
)
from persistence.session import SessionFactory, transaction
from services.design_difficulty_validator import DesignDifficultyValidator

LOG = logging.getLogger(__name__)
BUILD_GOVERNANCE_MODE_ENV = "BUILD_GOVERNANCE_MODE"
BUILD_GOVERNANCE_MODES: tuple[str, ...] = (
    "legacy",
    "legacy_trial",
    "shadow",
    "trial",
    "production",
)
GOVERNED_BUILD_ADMISSION_MODES = frozenset({"trial", "production"})
STAGING_ORPHAN_GRACE_SECONDS = 60 * 60
RE_LANGUAGE_DEFAULTS: dict[str, dict[str, str]] = {
    "c": {"compiler": "gcc", "target_format": "elf"},
    "cpp": {"compiler": "g++", "target_format": "elf"},
    "c++": {"compiler": "g++", "target_format": "elf"},
    "rust": {"compiler": "rustc", "target_format": "elf"},
    "go": {"compiler": "go build", "target_format": "elf"},
    "golang": {"compiler": "go build", "target_format": "elf"},
    "java": {"compiler": "javac", "target_format": "jar"},
    "kotlin": {"compiler": "kotlinc", "target_format": "jar"},
}
PWN_LANGUAGE_DEFAULTS: dict[str, dict[str, str]] = {
    "c": {"compiler": "gcc", "target_format": "elf"},
    "cpp": {"compiler": "g++", "target_format": "elf"},
    "c++": {"compiler": "g++", "target_format": "elf"},
    "rust": {"compiler": "rustc", "target_format": "elf"},
    "go": {"compiler": "go build", "target_format": "elf"},
    "golang": {"compiler": "go build", "target_format": "elf"},
    "asm": {"compiler": "nasm + ld", "target_format": "elf"},
    "assembly": {"compiler": "nasm + ld", "target_format": "elf"},
}

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
GENERIC_MATRIX_FIELDS: tuple[str, ...] = (
    "id",
    "title",
    "category",
    "difficulty",
    "points",
    "template",
    "deployment",
    "port",
    "primary_technique",
    "learning_objective",
    "capabilities",
)


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
    repair_requested: bool = False
    repair_context: dict[str, Any] | None = None
    retry_context: dict[str, Any] | None = None
    repair_mode: bool = False
    difficulty_review: DifficultyReviewResult | None = None


class BuildOrchestrationService:
    """Bridge PostgreSQL-owned build intent into the file shard queue."""

    def __init__(
        self,
        *,
        paths: ProjectPaths | None = None,
        session_factory: SessionFactory | None = None,
        governance_mode: str | None = None,
    ) -> None:
        self.paths = paths or ProjectPaths.discover()
        self.session_factory = session_factory or SessionFactory()
        self.governance_mode = _normalize_governance_mode(
            governance_mode if governance_mode is not None else os.environ.get(BUILD_GOVERNANCE_MODE_ENV)
        )

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
            task = DesignTaskRepository(session).get_design_task(source.design_task_id)
            if task is None or task.status != "build_failed":
                raise BuildOrchestrationError("retry requires a parent task in build_failed status")
            active = build_repo.active_for_design_task(source.design_task_id)
            if active is not None:
                return active.id
            latest = build_repo.latest_for_design_task(source.design_task_id)
            if latest is None or latest.id != source.id:
                raise BuildOrchestrationError("only the latest build attempt can be retried")
            if source.status not in {"failed", "lost"}:
                raise BuildOrchestrationError("only failed or lost attempts can be retried")
            retry_context = self._retry_context(session, source)
        return self._submit(
            [source.design_task_id],
            retry_sources={source.design_task_id: source.id},
            execution_mode="resume",
            retry_contexts={source.design_task_id: retry_context},
        )[0]

    def repair(self, build_attempt_id: UUID) -> UUID:
        """Queue an AI repair pass for the latest failed build attempt.

        Repair is a current-state fix flow. The next worker iteration should
        analyze the live workspace and failure diagnostics, without carrying
        forward historical progress stages.
        """
        with self._session() as session:
            build_repo = BuildAttemptsRepository(session)
            source = build_repo.get(build_attempt_id)
            if source is None:
                raise BuildOrchestrationError(f"build attempt {build_attempt_id} does not exist")
            task = DesignTaskRepository(session).get_design_task(source.design_task_id)
            if task is None or task.status != "build_failed":
                raise BuildOrchestrationError("repair requires a parent task in build_failed status")
            active = build_repo.active_for_design_task(source.design_task_id)
            if active is not None:
                return active.id
            latest = build_repo.latest_for_design_task(source.design_task_id)
            if latest is None or latest.id != source.id:
                raise BuildOrchestrationError("only the latest build attempt can be repaired")
            if source.status not in {"failed", "lost"}:
                raise BuildOrchestrationError("only failed or lost attempts can be repaired")
            failure_summary = self._repair_failure_summary(session, source) or source.error
            repair_context = {
                "source_build_attempt_id": str(source.id),
                "source_shard_basename": source.shard_basename,
                "failure_summary": failure_summary,
            }
            repair_context.update(_read_retry_diagnostics(self.paths, source.id))
        return self._submit(
            [source.design_task_id],
            retry_sources={source.design_task_id: source.id},
            execution_mode="clean",
            repair_sources={source.design_task_id: repair_context},
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
        repair_requested: bool = False,
        repair_context: Mapping[str, Any] | None = None,
        retry_context: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Render one attributed shard without filesystem or database effects."""
        challenge = _design_challenge(latest_design.payload)
        matrix_values = _matrix_values(design_task, challenge)
        fields = MATRIX_FIELDS.get(design_task.category, GENERIC_MATRIX_FIELDS)
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
        if repair_requested:
            payload["repair_requested"] = True
            if repair_context:
                payload["repair_context"] = dict(repair_context)
        if retry_context:
            payload["retry_context"] = dict(retry_context)
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
        repair_sources: Mapping[UUID, Mapping[str, Any]] | None = None,
        retry_contexts: Mapping[UUID, Mapping[str, Any]] | None = None,
    ) -> list[UUID]:
        prepared = self._prepare(
            design_task_ids,
            retry_sources=retry_sources,
            execution_mode=execution_mode,
            idempotency_key=idempotency_key,
            repair_sources=repair_sources or {},
            retry_contexts=retry_contexts or {},
        )
        self.paths.initialize()
        staged_paths: list[Path] = []
        try:
            for submission in prepared:
                staged_paths.append(self._write_staged_payload(submission))
            self._commit(prepared, retry_sources=retry_sources)
            self._carry_forward_retry_progress(prepared, retry_sources=retry_sources)
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

    def _carry_forward_retry_progress(
        self,
        prepared: Sequence[_PreparedSubmission],
        *,
        retry_sources: Mapping[UUID, UUID],
    ) -> None:
        retry_by_task = {
            submission.task.id: submission
            for submission in prepared
            if submission.task.id in retry_sources
            and submission.execution_mode == "resume"
        }
        if not retry_by_task:
            return
        with transaction(factory=self.session_factory) as session:
            for submission in retry_by_task.values():
                source_shard = submission.resume_from_shard_basename
                if source_shard is None:
                    continue
                _copy_progress_snapshots(
                    session,
                    source_shard=source_shard,
                    target_shard=submission.shard_basename,
                )

    def _prepare(
        self,
        design_task_ids: list[UUID],
        *,
        retry_sources: Mapping[UUID, UUID],
        execution_mode: str = "resume",
        idempotency_key: str | None = None,
        repair_sources: Mapping[UUID, Mapping[str, Any]] | None = None,
        retry_contexts: Mapping[UUID, Mapping[str, Any]] | None = None,
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
                self._validate_design_quality_for_submit(design)
                difficulty_review = DesignDifficultyValidator().review(
                    design_task=task,
                    challenge_design=design,
                )
                if not difficulty_review.passed:
                    reason = difficulty_review.reasons[0] if difficulty_review.reasons else "difficulty review failed"
                    self._record_failed_review_and_request_revision(
                        task_id=task.id,
                        challenge_design_id=design.id,
                        result=difficulty_review,
                        review_error=_review_error_message(difficulty_review),
                    )
                    raise BuildOrchestrationError(
                        f"design task {task_id} failed pre-build difficulty review: {reason}",
                        code="difficulty_review_failed",
                    )

                source_attempt_id = retry_sources.get(task_id)
                minting = execution_minting_enabled()
                if source_attempt_id is not None:
                    source_container = session.get(build_model.BuildAttempt, source_attempt_id)
                    minting = bool(
                        minting
                        and source_container is not None
                        and source_container.latest_execution_id is not None
                    )
                # Retry/repair resume existing evidence by appending a new
                # execution to the source container. Clean rebuild intentionally
                # starts a new container even when execution minting is enabled.
                is_repair = task_id in (repair_sources or {})
                is_retry = (
                    minting
                    and source_attempt_id is not None
                    and (execution_mode != "clean" or is_repair)
                )
                if is_retry:
                    if source_attempt_id is None:
                        raise BuildOrchestrationError("retry source attempt is required")
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
                    repair_requested=task_id in (repair_sources or {}),
                    repair_context=(repair_sources or {}).get(task_id),
                    retry_context=(retry_contexts or {}).get(task_id),
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
                        repair_requested=task_id in (repair_sources or {}),
                        repair_context=dict((repair_sources or {}).get(task_id) or {}),
                        retry_context=dict((retry_contexts or {}).get(task_id) or {}),
                        repair_mode=task_id in (repair_sources or {}),
                        difficulty_review=difficulty_review,
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
                if submission.difficulty_review is not None:
                    DesignDifficultyReviewRepository(session).record(
                        design_task_id=row.id,
                        challenge_design_id=submission.design.id,
                        result=submission.difficulty_review,
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
                    mode = (
                        "clean"
                        if submission.execution_mode == "clean" and not submission.is_fresh
                        else "standard"
                    )
                    container = session.get(build_model.BuildAttempt, submission.attempt_id)
                    if container is not None:
                        self._normalize_terminal_execution(session, container)
                        parent_execution_id = container.latest_execution_id
                        # Keep the legacy shard_basename in sync so reconciler and
                        # runner attribute the reused container's current iteration.
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

    def _validate_design_quality_for_submit(self, design: design_dto.ChallengeDesign) -> None:
        if self.governance_mode not in GOVERNED_BUILD_ADMISSION_MODES:
            return
        if design.quality_gate_passed:
            return
        raise BuildOrchestrationError(
            f"design task {design.design_task_id} failed design quality gate",
            code="design_quality_gate_failed",
        )

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

    def _record_failed_review_and_request_revision(
        self,
        *,
        task_id: UUID,
        challenge_design_id: UUID,
        result: DifficultyReviewResult,
        review_error: str,
    ) -> None:
        with transaction(factory=self.session_factory) as session:
            DesignDifficultyReviewRepository(session).record(
                design_task_id=task_id,
                challenge_design_id=challenge_design_id,
                result=result,
            )
            ChallengeDesignRepository(session).request_revision_from_review(
                design_task_id=task_id,
                challenge_design_id=challenge_design_id,
                review_error=review_error,
            )

    @staticmethod
    def _normalize_terminal_execution(
        session: Session,
        container: build_model.BuildAttempt,
    ) -> None:
        if (
            container.status not in {"succeeded", "failed", "lost"}
            or container.latest_execution_id is None
        ):
            return
        latest = session.get(exec_model.Execution, container.latest_execution_id)
        if latest is None or latest.status not in exec_model.NON_TERMINAL_STATUSES:
            return
        moment = container.finished_at or datetime.now(timezone.utc)
        latest.status = container.status
        latest.error = latest.error or container.error
        latest.finished_at = moment
        if container.current_execution_id == latest.id:
            container.current_execution_id = None

    @staticmethod
    def _repair_failure_summary(session: Session, source) -> str | None:
        events = session.scalars(
            sa.select(ProgressEvent)
            .where(
                ProgressEvent.shard == source.shard_basename,
                ProgressEvent.status == "failed",
            )
            .order_by(ProgressEvent.id.desc())
        ).all()
        for event in events:
            message = (event.message or "").strip()
            if not message or "lease expired" in message.lower():
                continue
            prefix = f"{event.stage}"
            if event.challenge_id:
                prefix = f"{event.challenge_id}:{prefix}"
            return f"{prefix}: {message}"[:2000]
        if source.error:
            return source.error[:2000]
        return None

    def _retry_context(self, session: Session, source) -> dict[str, Any]:
        context: dict[str, Any] = {
            "source_build_attempt_id": str(source.id),
            "source_shard_basename": source.shard_basename,
        }
        failure_summary = self._repair_failure_summary(session, source) or source.error
        if failure_summary:
            context["failure_summary"] = failure_summary
        context.update(_read_retry_diagnostics(self.paths, source.id))
        return context


def _design_challenge(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    challenges = payload.get("challenges")
    if not isinstance(challenges, list) or len(challenges) != 1:
        raise BuildOrchestrationError("validated design must contain one challenge")
    challenge = challenges[0]
    if not isinstance(challenge, Mapping):
        raise BuildOrchestrationError("validated design challenge must be an object")
    return challenge


def _read_retry_diagnostics(paths: ProjectPaths, attempt_id: UUID) -> dict[str, Any]:
    workspace_state = paths.executions / str(attempt_id) / "current" / "state"
    diagnostics: dict[str, Any] = {}
    first_failure = summarize_validation_entry(
        read_json(workspace_state / "first-validation-failure.json", None)
    )
    if first_failure:
        diagnostics["first_failure"] = first_failure
    latest_failure = latest_failed_validation(paths, attempt_id)
    if latest_failure:
        diagnostics["latest_failure"] = latest_failure
    return diagnostics


def _normalize_governance_mode(raw: str | None) -> str:
    value = (raw or "shadow").strip().lower()
    if value not in BUILD_GOVERNANCE_MODES:
        allowed = ", ".join(BUILD_GOVERNANCE_MODES)
        raise BuildOrchestrationError(
            f"unsupported build governance mode {raw!r}; expected one of: {allowed}",
            code="unsupported_governance_mode",
        )
    return value


def _matrix_values(
    task: task_dto.DesignTask,
    challenge: Mapping[str, Any],
) -> dict[str, Any]:
    plan = challenge.get("implementation_plan")
    plan = plan if isinstance(plan, Mapping) else {}
    constraints = task.constraints
    profile = generation_profile(task.category)

    def value(name: str, default: Any) -> Any:
        return challenge.get(name, plan.get(name, constraints.get(name, default)))

    language_default = "c"
    compiler_default = "gcc"
    target_format_default = "elf"
    if task.category == "re":
        language_default = value("language", "c")
        language_key = str(language_default).strip().lower()
        defaults = RE_LANGUAGE_DEFAULTS.get(language_key, RE_LANGUAGE_DEFAULTS["c"])
        compiler_default = defaults["compiler"]
        target_format_default = defaults["target_format"]
    elif task.category == "pwn":
        language_default = value("language", "c")
        language_key = str(language_default).strip().lower()
        defaults = PWN_LANGUAGE_DEFAULTS.get(language_key, PWN_LANGUAGE_DEFAULTS["c"])
        compiler_default = defaults["compiler"]
        target_format_default = defaults["target_format"]

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
        "language": value("language", language_default),
        "compiler": value("compiler", compiler_default),
        "target_format": value("target_format", target_format_default),
        "architecture": value("architecture", "x86_64"),
        "mitigations": value("mitigations", {}),
        "target_platform": value("target_platform", "linux/amd64"),
        "strip": value("strip", True),
        "primary_technique": task.primary_technique,
        "learning_objective": task.learning_objective,
        "distinctness": value("distinctness", task.scenario or task.primary_technique),
        "capabilities": {
            "requires_container": profile.capabilities.requires_container,
            "requires_network_service": profile.capabilities.requires_network_service,
            "requires_solver": profile.capabilities.requires_solver,
            "requires_player_artifact": profile.capabilities.requires_player_artifact,
            "launcher": profile.capabilities.launcher,
        },
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


def _copy_progress_snapshots(
    session: Session,
    *,
    source_shard: str,
    target_shard: str,
) -> None:
    if source_shard == target_shard:
        return
    session.execute(
        sa.delete(ProgressSnapshot).where(ProgressSnapshot.shard == target_shard)
    )
    best_rows: dict[str, Any] = {}
    event_rows = session.scalars(
        sa.select(ProgressEvent)
        .where(ProgressEvent.shard == source_shard)
        .order_by(ProgressEvent.id.asc())
    ).all()
    for row in event_rows:
        if not _copyable_resume_progress(row.stage, row.status):
            continue
        current = best_rows.get(row.challenge_id)
        if current is None or int(row.percent or 0) >= int(current.percent or 0):
            best_rows[row.challenge_id] = row
    now = datetime.now(timezone.utc)
    snapshot_rows = session.scalars(
        sa.select(ProgressSnapshot).where(ProgressSnapshot.shard == source_shard)
    ).all()
    for row in snapshot_rows:
        if not _copyable_resume_progress(row.stage, row.status):
            continue
        current = best_rows.get(row.challenge_id)
        if current is None or int(row.percent or 0) > int(current.percent or 0):
            best_rows[row.challenge_id] = row
    for row in best_rows.values():
        session.add(
            ProgressSnapshot(
                shard=target_shard,
                challenge_id=row.challenge_id,
                worker=row.worker,
                stage=row.stage,
                status=row.status,
                percent=row.percent,
                message=row.message,
                updated_at=now,
            )
        )


def _copyable_resume_progress(stage: str, status: str) -> bool:
    return stage in EXECUTION_STAGES and status == "passed"


def _review_error_message(result: DifficultyReviewResult) -> str:
    lines = ["Pre-build difficulty review failed."]
    if result.reasons:
        lines.append("Reasons:")
        lines.extend(f"- {item}" for item in result.reasons)
    if result.required_revision:
        lines.append("Required revisions:")
        lines.extend(f"- {item}" for item in result.required_revision)
    return "\n".join(lines)
