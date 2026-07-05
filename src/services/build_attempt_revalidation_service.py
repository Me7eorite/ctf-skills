"""Re-run host validation for an existing failed build attempt."""

from __future__ import annotations

import json
import shutil
import uuid
from collections.abc import Callable, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator
from uuid import UUID

import sqlalchemy as sa

from core.docker import image_exists as default_image_exists
from core.jsonio import read_json
from core.paths import ProjectPaths
from core.queue import ShardQueue
from core.state import ProgressStore
from domain.output_consistency import validate_workspace_success_state
from domain.pwn_artifact_evidence import PwnArtifactEvidenceError, ensure_pwn_solver_evidence
from domain.resume import ChallengeResumePlan, find_challenge_directory
from domain.validation import ChallengeValidator
from hermes.validation import record_per_challenge_complete, run_validation
from persistence.models import build_attempts as build_model
from persistence.models import design_tasks as task_model
from persistence.models import executions as exec_model
from persistence.repositories import BuildAttemptsRepository
from persistence.session import SessionFactory, transaction

REVALIDATION_WORKER = "dashboard-revalidate"


class BuildAttemptRevalidationError(ValueError):
    """Raised when a build attempt cannot be revalidated or remains invalid."""


class BuildAttemptRevalidationNotFoundError(BuildAttemptRevalidationError):
    """Raised when the requested build attempt does not exist."""


@dataclass(frozen=True)
class BuildAttemptRevalidationResult:
    """Result of a successful same-attempt revalidation."""

    attempt_id: UUID


class _DirectoryBoundValidator:
    """Prevent challenge-id lookup from selecting a different artifact directory."""

    def __init__(
        self,
        validator: ChallengeValidator,
        plans: Mapping[str, ChallengeResumePlan],
    ) -> None:
        self.validator = validator
        self.plans = plans

    def validate_challenge(self, challenge_id: str) -> dict:
        plan = self.plans.get(challenge_id)
        if plan is None or plan.directory is None:
            return {"challenge_id": challenge_id, "status": "missing_challenge"}
        result = self.validator.validate_one(plan.directory)
        result.setdefault("challenge_id", challenge_id)
        return result


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
        with self._advisory_lock(attempt_id):
            attempt, challenge_ids = self._prepare(attempt_id)
            plans = self._current_plans(attempt, challenge_ids)
            self._ensure_pwn_solver_evidence(plans)
            validator = _DirectoryBoundValidator(self.validator, plans)
            try:
                results = run_validation(
                    state=self.progress,
                    validator=validator,  # type: ignore[arg-type]
                    paths=self.paths,
                    image_exists=self.image_exists,
                    original_shard_name=attempt.shard_basename,
                    worker=self.worker,
                    challenge_ids=challenge_ids,
                    plan_by_id=plans,
                )
            except Exception as exc:
                reason = f"validator_error: {type(exc).__name__}: {exc}"
                self._mark_failed(attempt.id, reason)
                self._record_unexpected_failure(
                    attempt.shard_basename,
                    challenge_ids,
                    reason,
                )
                raise BuildAttemptRevalidationError(reason) from exc

            failures = [
                result
                for result in results
                if result.get("solve_status") != "passed"
            ]
            if failures:
                reason = _failure_reason(failures[0])
                self._mark_failed(attempt.id, reason)
                record_per_challenge_complete(
                    self.progress,
                    attempt.shard_basename,
                    self.worker,
                    results,
                )
                raise BuildAttemptRevalidationError(reason)

            challenge_dir = _relative_challenge_dir(
                self.paths,
                _canonicalize_challenge_directory(
                    self.paths,
                    challenge_ids[0],
                    plans[challenge_ids[0]],
                ),
            )
            try:
                self._mark_succeeded(
                    attempt.id,
                    shard_basename=attempt.shard_basename,
                    challenge_dir=challenge_dir,
                )
            except Exception as exc:
                reason = f"finalization_error: {type(exc).__name__}: {exc}"
                self._record_complete_failure(
                    attempt.shard_basename,
                    challenge_ids,
                    reason,
                )
                if isinstance(exc, BuildAttemptRevalidationError):
                    raise
                raise BuildAttemptRevalidationError(reason) from exc
            record_per_challenge_complete(
                self.progress,
                attempt.shard_basename,
                self.worker,
                results,
            )
            return BuildAttemptRevalidationResult(attempt_id=attempt.id)

    @contextmanager
    def _advisory_lock(self, attempt_id: UUID) -> Iterator[None]:
        key = attempt_id.int & ((1 << 63) - 1)
        with self.session_factory.engine.connect() as connection:
            acquired = connection.scalar(
                sa.select(sa.func.pg_try_advisory_lock(key))
            )
            connection.commit()
            if not acquired:
                raise BuildAttemptRevalidationError(
                    "build attempt is already being revalidated"
                )
            try:
                yield
            finally:
                connection.execute(sa.select(sa.func.pg_advisory_unlock(key)))
                connection.commit()

    def _prepare(self, attempt_id: UUID):
        with transaction(factory=self.session_factory) as session:
            row = session.scalars(
                sa.select(build_model.BuildAttempt)
                .where(build_model.BuildAttempt.id == attempt_id)
                .with_for_update()
            ).one_or_none()
            if row is None:
                raise BuildAttemptRevalidationNotFoundError(
                    f"build attempt {attempt_id} does not exist"
                )
            consistency_failure = _attempt_success_consistency_failure(self.paths, row)
            if consistency_failure is not None:
                _downgrade_inconsistent_success_to_failed(
                    self.paths,
                    row,
                    session,
                    reason=consistency_failure,
                )
            if row.status != "failed":
                raise BuildAttemptRevalidationError(
                    f"build attempt is {row.status}, expected failed"
                )
            if row.latest_execution_id is not None:
                if row.current_execution_id is not None:
                    raise BuildAttemptRevalidationError(
                        "cannot revalidate while an execution is current"
                    )
                latest_execution = session.get(
                    exec_model.Execution, row.latest_execution_id
                )
                if latest_execution is None or latest_execution.status not in {
                    "succeeded",
                    "failed",
                    "lost",
                }:
                    raise BuildAttemptRevalidationError(
                        "latest execution must be terminal before revalidate"
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
            self._assert_no_organizer_file_leaks(payload)
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

    @staticmethod
    def _assert_no_organizer_file_leaks(payload: Mapping[str, Any]) -> None:
        if payload.get("repair_requested") is not True:
            return
        context = payload.get("repair_context", {})
        text = json.dumps(context, ensure_ascii=False)
        for needle in ("/root/ctf-skills/work/executions/", "/workspace/executions/"):
            if needle in text:
                raise BuildAttemptRevalidationError(
                    f"failed shard repair context leaks execution path reference: {needle}"
                )

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
        attempt,
        challenge_ids: list[str],
    ) -> dict[str, ChallengeResumePlan]:
        plans: dict[str, ChallengeResumePlan] = {}
        for challenge_id in challenge_ids:
            directory, lookup_status = self._resolve_challenge_directory(
                attempt,
                challenge_id,
            )
            plans[challenge_id] = ChallengeResumePlan(
                challenge_id=challenge_id,
                directory=directory,
                lookup_status=lookup_status,
                skipped_stages=(),
                first_pending_stage="validate",
                stage_sources={},
            )
        return plans

    @staticmethod
    def _ensure_pwn_solver_evidence(plans: Mapping[str, ChallengeResumePlan]) -> None:
        for plan in plans.values():
            if plan.directory is None:
                continue
            try:
                ensure_pwn_solver_evidence(plan.directory)
            except PwnArtifactEvidenceError:
                continue

    def _resolve_challenge_directory(
        self,
        attempt,
        challenge_id: str,
    ) -> tuple[Path | None, str]:
        if attempt.resulting_challenge_dir:
            directory = (self.paths.root / attempt.resulting_challenge_dir).resolve()
            try:
                directory.relative_to(self.paths.challenges.resolve())
            except ValueError as exc:
                raise BuildAttemptRevalidationError(
                    "resulting challenge directory is outside work/challenges"
                ) from exc
            if not directory.is_dir():
                raise BuildAttemptRevalidationError(
                    "resulting challenge directory is missing"
                )
            metadata = read_json(directory / "metadata.json", None)
            if not isinstance(metadata, Mapping) or metadata.get("id") != challenge_id:
                raise BuildAttemptRevalidationError(
                    "resulting challenge metadata id does not match"
                )
            return directory, "matched"

        lookup = find_challenge_directory(self.paths, challenge_id)
        if lookup.directory is not None:
            metadata = read_json(lookup.directory / "metadata.json", None)
            if not isinstance(metadata, Mapping) or metadata.get("id") != challenge_id:
                raise BuildAttemptRevalidationError(
                    "challenge metadata id does not match"
                )
            return lookup.directory, lookup.status

        workspace_directory = _attempt_execution_workspace(
            self.paths,
            attempt.id,
            challenge_id,
        )
        if workspace_directory is None:
            return None, lookup.status
        metadata = read_json(workspace_directory / "metadata.json", None)
        if not isinstance(metadata, Mapping) or metadata.get("id") != challenge_id:
            raise BuildAttemptRevalidationError(
                "challenge metadata id does not match"
            )
        return workspace_directory, "workspace"

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
        try:
            if claim_source.exists():
                claim_source.replace(ShardQueue._claim_path(destination))
        except Exception:
            destination.replace(source)
            raise

    def _restore_done_shard_to_failed(self, shard_basename: str) -> None:
        source = self.paths.shards / "failed" / shard_basename
        destination = self.paths.shards / "done" / shard_basename
        if source.exists() or not destination.is_file():
            raise BuildAttemptRevalidationError(
                "cannot restore done shard to failed"
            )
        claim_destination = ShardQueue._claim_path(destination)
        claim_source = ShardQueue._claim_path(source)
        claim_moved = False
        if claim_destination.exists():
            claim_destination.replace(claim_source)
            claim_moved = True
        try:
            destination.replace(source)
        except Exception:
            if claim_moved:
                claim_source.replace(claim_destination)
            raise

    def _record_unexpected_failure(
        self,
        shard_basename: str,
        challenge_ids: list[str],
        reason: str,
    ) -> None:
        for challenge_id in challenge_ids:
            self.progress.record(
                shard=shard_basename,
                challenge_id=challenge_id,
                worker=self.worker,
                stage="validate",
                status="failed",
                message=reason,
            )
        self._record_complete_failure(shard_basename, challenge_ids, reason)

    def _record_complete_failure(
        self,
        shard_basename: str,
        challenge_ids: list[str],
        reason: str,
    ) -> None:
        record_per_challenge_complete(
            self.progress,
            shard_basename,
            self.worker,
            [
                {
                    "challenge_id": challenge_id,
                    "solve_status": "failed",
                    "validation_status": reason,
                }
                for challenge_id in challenge_ids
            ],
        )

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
        moved = False
        try:
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
                moved = True
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
        except Exception as exc:
            if moved:
                try:
                    self._restore_done_shard_to_failed(shard_basename)
                except Exception as restore_exc:
                    raise BuildAttemptRevalidationError(
                        f"database update failed and shard restore failed: {restore_exc}"
                    ) from exc
            raise


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
    details = result.get("validation_failure_details") or result.get("failure_details")
    if isinstance(details, list):
        for detail in details:
            if isinstance(detail, Mapping) and detail.get("message"):
                return f"{status}: {detail['message']}"
    for key in ("stderr_tail", "stdout_tail", "validation_stderr_tail", "validation_stdout_tail"):
        text = result.get(key)
        if isinstance(text, str) and text.strip():
            return f"{status}: {text.strip().splitlines()[-1]}"
    return status


def _canonicalize_challenge_directory(
    paths: ProjectPaths,
    challenge_id: str,
    plan: ChallengeResumePlan,
) -> Path:
    if plan.directory is None:
        raise BuildAttemptRevalidationError(plan.lookup_status)
    directory = plan.directory.resolve()
    try:
        directory.relative_to(paths.challenges.resolve())
        return directory
    except ValueError:
        pass
    try:
        directory.relative_to(paths.executions.resolve())
    except ValueError as exc:
        raise BuildAttemptRevalidationError(
            "workspace challenge directory is outside managed storage"
        ) from exc

    metadata = read_json(directory / "metadata.json", None)
    if not isinstance(metadata, Mapping) or metadata.get("id") != challenge_id:
        raise BuildAttemptRevalidationError("challenge metadata id does not match")
    category = metadata.get("category") or directory.parent.name
    if not isinstance(category, str) or not category:
        raise BuildAttemptRevalidationError("challenge metadata category is missing")
    destination_root = paths.challenges / category
    destination_name = directory.name
    if destination_name == "output":
        destination_name = challenge_id
    destination = destination_root / destination_name
    if destination.exists():
        raise BuildAttemptRevalidationError(
            f"canonical challenge directory already exists: {destination.relative_to(paths.root).as_posix()}"
        )
    destination_root.mkdir(parents=True, exist_ok=True)
    temporary = destination_root / f".revalidate-{uuid.uuid4().hex}"
    shutil.copytree(directory, temporary, symlinks=False)
    try:
        temporary.rename(destination)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return destination


def _relative_challenge_dir(paths: ProjectPaths, directory: Path) -> str:
    try:
        return directory.resolve().relative_to(paths.root.resolve()).as_posix()
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


def _attempt_success_consistency_failure(
    paths: ProjectPaths,
    row,
) -> str | None:
    if row.status != "succeeded":
        return None
    result = validate_workspace_success_state(
        paths.executions / str(row.id) / "current"
    )
    if result.get("ok"):
        return None
    return str(result.get("reason") or "success state is inconsistent")


def _downgrade_inconsistent_success_to_failed(
    paths: ProjectPaths,
    row,
    session,
    *,
    reason: str,
) -> None:
    failed = paths.shards / "failed" / row.shard_basename
    done = paths.shards / "done" / row.shard_basename
    if not failed.exists() and done.is_file() and not done.is_symlink():
        failed.parent.mkdir(parents=True, exist_ok=True)
        done.replace(failed)
        done_claim = ShardQueue._claim_path(done)
        failed_claim = ShardQueue._claim_path(failed)
        if done_claim.exists() and not done_claim.is_symlink():
            failed_claim.parent.mkdir(parents=True, exist_ok=True)
            done_claim.replace(failed_claim)
    now = datetime.now(timezone.utc)
    row.status = "failed"
    row.error = reason
    row.finished_at = now
    task = session.get(task_model.DesignTask, row.design_task_id)
    if task is not None:
        task.status = "build_failed"
        task.updated_at = now


def _attempt_execution_workspace(
    paths: ProjectPaths,
    attempt_id: UUID,
    challenge_id: str,
) -> Path | None:
    attempt_root = paths.executions / str(attempt_id) / "current" / "output"
    if not attempt_root.is_dir():
        return None

    candidates: list[Path] = []
    metadata = read_json(attempt_root / "metadata.json", None)
    if isinstance(metadata, Mapping) and metadata.get("id") == challenge_id:
        normalized = _normalize_direct_attempt_output(attempt_root, metadata)
        if normalized is not None:
            candidates.append(normalized)

    output_root = attempt_root / "challenges"
    if output_root.is_dir():
        for category_dir in output_root.iterdir():
            if not category_dir.is_dir():
                continue
            for challenge_dir in category_dir.iterdir():
                if not challenge_dir.is_dir():
                    continue
                if (
                    challenge_dir.name == challenge_id
                    or challenge_dir.name.startswith(f"{challenge_id}-")
                ):
                    candidates.append(challenge_dir)

    if not candidates:
        return None

    candidates.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    return candidates[0]


_CHALLENGE_ROOT_ENTRIES = {
    "README.md",
    "metadata.json",
    "challenge.yml",
    "validate.sh",
    "writenup",
    "src",
    "attachments",
    "deploy",
    "dist",
}


def _normalize_direct_attempt_output(
    output_root: Path,
    metadata: Mapping[str, Any],
) -> Path | None:
    challenge_id = metadata.get("id")
    category = metadata.get("category")
    if not isinstance(challenge_id, str) or not isinstance(category, str):
        return None
    canonical = output_root / "challenges" / category / challenge_id
    if canonical.is_dir():
        return canonical
    direct_entries = [
        output_root / name
        for name in _CHALLENGE_ROOT_ENTRIES
        if (output_root / name).exists()
    ]
    if not direct_entries:
        return None
    canonical.mkdir(parents=True, exist_ok=True)
    for source in direct_entries:
        destination = canonical / source.name
        if destination.exists():
            continue
        source.replace(destination)
    return canonical
