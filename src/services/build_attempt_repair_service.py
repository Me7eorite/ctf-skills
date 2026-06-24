"""Focused AI repair for an existing failed build attempt."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import sqlalchemy as sa

from core.jsonio import read_json
from core.paths import ProjectPaths
from hermes import process as hermes_process
from persistence.models import build_attempts as build_model
from persistence.models import design_tasks as task_model
from persistence.models.progress import ProgressEvent
from persistence.session import SessionFactory, transaction
from services.build_attempt_revalidation_service import (
    BuildAttemptRevalidationError,
    BuildAttemptRevalidationService,
)

REPAIR_WORKER = "dashboard-repair"


class BuildAttemptRepairError(ValueError):
    """Raised when a build attempt cannot be repaired."""


@dataclass(frozen=True)
class BuildAttemptRepairResult:
    attempt_id: UUID
    repair_id: str
    status: str
    verification_status: str
    log_path: str
    events_path: str
    failure_summary: str | None = None


class BuildAttemptRepairService:
    """Run AI repair in the attempt's execution workspace, then revalidate."""

    def __init__(
        self,
        *,
        paths: ProjectPaths | None = None,
        progress,
        session_factory: SessionFactory | None = None,
        timeout_seconds: int = hermes_process.DEFAULT_HERMES_TIMEOUT,
    ) -> None:
        self.paths = paths or ProjectPaths.discover()
        self.progress = progress
        self.session_factory = session_factory or SessionFactory()
        self.timeout_seconds = timeout_seconds

    def repair(self, attempt_id: UUID) -> BuildAttemptRepairResult:
        context = self._prepare(attempt_id)
        repair_id = f"repair-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:8]}"
        repair_dir = self.paths.executions / str(attempt_id) / "repairs" / repair_id
        repair_dir.mkdir(parents=True, exist_ok=False)
        events_path = repair_dir / "repair-events.jsonl"
        log_path = repair_dir / "hermes.log"
        prompt_path = repair_dir / "prompt.md"

        self._record_event(events_path, "analysis", "started", "analysis started")
        prompt = _repair_prompt(context)
        prompt_path.write_text(prompt, encoding="utf-8")
        self._record_event(events_path, "solve", "running", "AI repair running")
        arguments = _hermes_arguments(context["category"])
        environment = _hermes_environment(self.paths, arguments)
        returncode = hermes_process.invoke(
            prompt,
            arguments=arguments,
            log_path=log_path,
            cwd=self.paths.root,
            environment=environment,
            timeout=self.timeout_seconds,
        )
        if returncode != 0:
            message = f"Hermes repair exited with {returncode}"
            self._record_event(events_path, "solve", "failed", message)
            return BuildAttemptRepairResult(
                attempt_id=attempt_id,
                repair_id=repair_id,
                status="failed",
                verification_status="not_run",
                log_path=str(log_path),
                events_path=str(events_path),
                failure_summary=message,
            )

        self._record_event(events_path, "solve", "passed", "patch applied")
        self._record_event(events_path, "verify", "running", "verification running")
        try:
            BuildAttemptRevalidationService(
                paths=self.paths,
                progress=self.progress,
                session_factory=self.session_factory,
                worker=REPAIR_WORKER,
            ).revalidate(attempt_id)
        except BuildAttemptRevalidationError as exc:
            detail = str(exc)
            self._record_event(events_path, "verify", "failed", detail)
            return BuildAttemptRepairResult(
                attempt_id=attempt_id,
                repair_id=repair_id,
                status="failed",
                verification_status="failed",
                log_path=str(log_path),
                events_path=str(events_path),
                failure_summary=detail,
            )

        self._record_event(events_path, "verify", "passed", "verification passed")
        return BuildAttemptRepairResult(
            attempt_id=attempt_id,
            repair_id=repair_id,
            status="succeeded",
            verification_status="passed",
            log_path=str(log_path),
            events_path=str(events_path),
        )

    def _prepare(self, attempt_id: UUID) -> dict[str, Any]:
        with transaction(factory=self.session_factory) as session:
            row = session.scalars(
                sa.select(build_model.BuildAttempt)
                .where(build_model.BuildAttempt.id == attempt_id)
                .with_for_update()
            ).one_or_none()
            if row is None:
                raise BuildAttemptRepairError(f"build attempt {attempt_id} does not exist")
            if row.status != "failed":
                raise BuildAttemptRepairError(f"build attempt is {row.status}, expected failed")
            latest = session.scalars(
                sa.select(build_model.BuildAttempt)
                .where(build_model.BuildAttempt.design_task_id == row.design_task_id)
                .order_by(build_model.BuildAttempt.attempt_no.desc())
                .limit(1)
            ).one_or_none()
            if latest is None or latest.id != row.id:
                raise BuildAttemptRepairError("only the latest build attempt can be repaired")
            active = session.scalars(
                sa.select(build_model.BuildAttempt)
                .where(
                    build_model.BuildAttempt.design_task_id == row.design_task_id,
                    build_model.BuildAttempt.status.in_(("queued", "running")),
                )
                .limit(1)
            ).one_or_none()
            if active is not None:
                raise BuildAttemptRepairError("cannot repair while another build is active")
            task = session.get(task_model.DesignTask, row.design_task_id)
            if task is None or task.status != "build_failed":
                raise BuildAttemptRepairError("repair requires a parent task in build_failed status")
            failure_summary = _failure_summary(session, row) or row.error
            attempt = {
                "id": row.id,
                "shard_basename": row.shard_basename,
                "resulting_challenge_dir": row.resulting_challenge_dir,
                "design_task_id": row.design_task_id,
                "category": task.category,
                "failure_summary": failure_summary,
            }

        payload = _failed_payload(self.paths, attempt["shard_basename"])
        challenge_ids = _challenge_ids(payload)
        if len(challenge_ids) != 1:
            raise BuildAttemptRepairError("repair requires a failed shard with exactly one challenge")
        challenge_id = challenge_ids[0]
        challenge_dir = _challenge_directory(
            self.paths,
            challenge_id,
            attempt["resulting_challenge_dir"],
        )
        return {
            **attempt,
            "challenge_id": challenge_id,
            "challenge_dir": str(challenge_dir),
        }

    @staticmethod
    def _record_event(path: Path, phase: str, status: str, message: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "phase": phase,
            "status": status,
            "message": message,
        }
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _failed_payload(paths: ProjectPaths, shard_basename: str) -> dict[str, Any]:
    if Path(shard_basename).name != shard_basename:
        raise BuildAttemptRepairError("build attempt shard basename is invalid")
    shard = paths.shards / "failed" / shard_basename
    if shard.is_symlink() or not shard.is_file():
        raise BuildAttemptRepairError("failed shard is missing")
    payload = read_json(shard, None)
    if not isinstance(payload, dict):
        raise BuildAttemptRepairError("failed shard payload is invalid")
    return payload


def _challenge_ids(payload: dict[str, Any]) -> list[str]:
    challenges = payload.get("challenges")
    if not isinstance(challenges, list):
        return []
    return [
        challenge["id"]
        for challenge in challenges
        if isinstance(challenge, dict)
        and isinstance(challenge.get("id"), str)
        and challenge["id"]
    ]


def _challenge_directory(
    paths: ProjectPaths,
    challenge_id: str,
    resulting_challenge_dir: str | None,
) -> Path:
    if resulting_challenge_dir:
        directory = (paths.root / resulting_challenge_dir).resolve()
        try:
            directory.relative_to(paths.challenges.resolve())
        except ValueError as exc:
            raise BuildAttemptRepairError("resulting challenge directory is outside work/challenges") from exc
        if not directory.is_dir():
            raise BuildAttemptRepairError("resulting challenge directory is missing")
        return directory

    execution_root = _latest_execution_workspace(paths, challenge_id)
    if execution_root is not None:
        return execution_root

    raise BuildAttemptRepairError("missing_challenge")


def _latest_execution_workspace(paths: ProjectPaths, challenge_id: str) -> Path | None:
    executions = paths.executions
    if not executions.exists():
        return None

    candidate_dirs: list[Path] = []
    for attempt_dir in executions.iterdir():
        if not attempt_dir.is_dir():
            continue
        output_root = attempt_dir / "current" / "output" / "challenges"
        if not output_root.is_dir():
            continue
        for category_dir in output_root.iterdir():
            if not category_dir.is_dir():
                continue
            for challenge_dir in category_dir.iterdir():
                if not challenge_dir.is_dir():
                    continue
                if challenge_dir.name == challenge_id or challenge_dir.name.startswith(f"{challenge_id}-"):
                    candidate_dirs.append(challenge_dir)

    if not candidate_dirs:
        return None

    candidate_dirs.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    return candidate_dirs[0]


def _failure_summary(session, source) -> str | None:
    events = session.scalars(
        sa.select(ProgressEvent)
        .where(
            ProgressEvent.shard == source.shard_basename,
            ProgressEvent.status == "failed",
            ProgressEvent.stage.in_(("validate", "complete")),
        )
        .order_by(ProgressEvent.id.desc())
    ).all()
    for event in events:
        message = (event.message or "").strip()
        if message and "lease expired" not in message.lower():
            prefix = f"{event.challenge_id}: " if event.challenge_id else ""
            return f"{prefix}{message}"[:2000]
    return None


def _repair_prompt(context: dict[str, Any]) -> str:
    return f"""You are repairing an existing CTF challenge artifact.

This is not a new build, not a resume, and not a clean rebuild.

Workflow:
1. analysis: inspect the failure and current challenge files.
2. solve: make the smallest file changes needed to fix the failure.
3. verify: do not claim success yourself; host-side validation will run after you exit.

Failure summary:
{context.get("failure_summary") or "(none)"}

Attempt:
- build_attempt_id: {context["id"]}
- design_task_id: {context["design_task_id"]}
- challenge_id: {context["challenge_id"]}
- category: {context["category"]}

Allowed repair root:
{context["challenge_dir"]}

Rules:
- Only modify files under the allowed repair root.
- Do not redesign or regenerate the challenge from the original design.
- Do not use carry-forward, skip_stages, next_stage, or resume semantics.
- Prefer targeted edits to solver, validate.sh, metadata, documentation, and current artifact files.
- For reverse-engineering repairs, the solver and validate.sh must derive the flag
  from distributed artifacts, not organizer files such as metadata.json or challenge.yml.
"""


def _hermes_arguments(category: str) -> list[str]:
    profile_name = f"cf-{category}"
    return hermes_process.inject_profile_argument(profile_name)


def _hermes_environment(paths: ProjectPaths, arguments: list[str]) -> dict[str, str]:
    environment = os.environ.copy()
    if paths.hermes_home.exists() and not environment.get("HERMES_HOME"):
        environment["HERMES_HOME"] = str(paths.hermes_home)
    if hermes_process.apply_legacy_custom_provider(paths.hermes_home, environment):
        hermes_process.remove_conflicting_custom_pool(paths.hermes_home)
        query_index = arguments.index("-q") if "-q" in arguments else len(arguments)
        arguments[query_index:query_index] = ["--provider", "custom"]
    return environment
