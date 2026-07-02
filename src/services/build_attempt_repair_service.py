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

from core.clock import beijing_now_isoformat
from core.jsonio import read_json
from core.paths import ProjectPaths
from hermes import process as hermes_process
from hermes.runner import validation_repair_timeout_cap
from persistence.models import build_attempts as build_model
from persistence.models import design_tasks as task_model
from persistence.models.progress import ProgressEvent
from persistence.session import SessionFactory, transaction
from services.build_attempt_auto_repair_service import auto_repair_challenge
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
        timeout_seconds: int | None = None,
    ) -> None:
        self.paths = paths or ProjectPaths.discover()
        self.progress = progress
        self.session_factory = session_factory or SessionFactory()
        self.timeout_seconds = (
            timeout_seconds if timeout_seconds is not None else _default_timeout_seconds()
        )

    def repair(self, attempt_id: UUID) -> BuildAttemptRepairResult:
        context = self._prepare(attempt_id)
        repair_id = f"repair-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:8]}"
        repair_dir = self.paths.executions / str(attempt_id) / "repairs" / repair_id
        repair_dir.mkdir(parents=True, exist_ok=False)
        events_path = repair_dir / "repair-events.jsonl"
        log_path = repair_dir / "hermes.log"
        prompt_path = repair_dir / "prompt.md"

        self._record_event(events_path, "analysis", "started", "analysis started")
        auto_result = auto_repair_challenge(
            Path(context["challenge_dir"]),
            challenge_id=context["challenge_id"],
        )
        if auto_result.changed:
            self._record_event(
                events_path,
                "solve",
                "passed",
                "deterministic repair applied: " + "; ".join(auto_result.actions),
            )
            self._record_event(events_path, "verify", "running", "verification running")
            try:
                self._revalidate(attempt_id)
            except BuildAttemptRevalidationError as exc:
                self._record_event(
                    events_path,
                    "verify",
                    "failed",
                    f"deterministic repair did not pass: {exc}",
                )
            else:
                self._record_event(events_path, "verify", "passed", "verification passed")
                return BuildAttemptRepairResult(
                    attempt_id=attempt_id,
                    repair_id=repair_id,
                    status="succeeded",
                    verification_status="passed",
                    log_path=str(log_path),
                    events_path=str(events_path),
                    failure_summary="deterministic repair applied",
                )

        prompt = _repair_prompt(context)
        prompt_path.write_text(prompt, encoding="utf-8")
        self._record_event(events_path, "solve", "running", "AI repair running")
        arguments = _hermes_arguments(context["category"])
        cwd = self.paths.executions / str(attempt_id) / "current"
        environment = _hermes_environment(
            self.paths,
            arguments,
            cwd=cwd,
            profile_name=f"cf-{context['category']}",
        )
        returncode = hermes_process.invoke(
            prompt,
            arguments=arguments,
            log_path=log_path,
            cwd=cwd,
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
            self._revalidate(attempt_id)
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

    def _revalidate(self, attempt_id: UUID) -> None:
        BuildAttemptRevalidationService(
            paths=self.paths,
            progress=self.progress,
            session_factory=self.session_factory,
            worker=REPAIR_WORKER,
        ).revalidate(attempt_id)

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
            failure_details = _failure_details(session, row)

        payload = _failed_payload(self.paths, attempt["shard_basename"])
        challenge_ids = _challenge_ids(payload)
        if len(challenge_ids) != 1:
            raise BuildAttemptRepairError("repair requires a failed shard with exactly one challenge")
        challenge_id = challenge_ids[0]
        challenge_dir = _challenge_directory(
            self.paths,
            attempt["id"],
            challenge_id,
            attempt["resulting_challenge_dir"],
            category=attempt["category"],
        )
        return {
            **attempt,
            "challenge_id": challenge_id,
            "challenge_dir": str(challenge_dir),
            "failure_details": failure_details,
            "file_context": _file_context(challenge_dir),
        }

    @staticmethod
    def _record_event(path: Path, phase: str, status: str, message: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "created_at": beijing_now_isoformat(),
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
    attempt_id: UUID,
    challenge_id: str,
    resulting_challenge_dir: str | None,
    *,
    category: str | None = None,
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

    normalized = _normalize_unclaimed_workspace_output(
        paths,
        attempt_id,
        challenge_id,
        category,
    )
    if normalized is not None:
        return normalized

    execution_root = _attempt_execution_workspace(paths, attempt_id, challenge_id)
    if execution_root is not None:
        return execution_root

    raise BuildAttemptRepairError("missing_challenge")


_CHALLENGE_ROOT_ENTRIES = {
    "metadata.json",
    "challenge.yml",
    "validate.sh",
    "README.md",
    "writenup",
    "src",
    "attachments",
    "deploy",
    "dist",
}


def _normalize_unclaimed_workspace_output(
    paths: ProjectPaths,
    attempt_id: UUID,
    challenge_id: str,
    category: str | None,
) -> Path | None:
    if not category:
        return None
    output_root = paths.executions / str(attempt_id) / "current" / "output"
    if not output_root.is_dir():
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
    metadata = read_json(output_root / "metadata.json", None)
    if isinstance(metadata, dict) and metadata.get("id") not in {None, challenge_id}:
        return None
    canonical.mkdir(parents=True, exist_ok=True)
    moved = False
    for source in direct_entries:
        destination = canonical / source.name
        if destination.exists():
            continue
        source.replace(destination)
        moved = True
    if moved or any((canonical / name).exists() for name in _CHALLENGE_ROOT_ENTRIES):
        return canonical
    return None


def _attempt_execution_workspace(
    paths: ProjectPaths,
    attempt_id: UUID,
    challenge_id: str,
) -> Path | None:
    attempt_root = paths.executions / str(attempt_id) / "current" / "output"
    if not attempt_root.is_dir():
        return None

    candidate_dirs: list[Path] = []
    metadata = read_json(attempt_root / "metadata.json", None)
    if isinstance(metadata, dict) and metadata.get("id") == challenge_id:
        candidate_dirs.append(attempt_root)

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


def _repair_prompt(context: dict[str, Any]) -> str:
    return f"""You are repairing an existing CTF challenge artifact.

This is not a new build, not a resume, and not a clean rebuild.

Workflow:
1. analysis: inspect the failure and current challenge files.
2. solve: make the smallest file changes needed to fix the failure.
3. verify: do not claim success yourself; host-side validation will run after you exit.

Failure summary:
{context.get("failure_summary") or "(none)"}

Structured failure details:
{json.dumps(context.get("failure_details") or [], ensure_ascii=False, indent=2)}

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
- For Web/Pwn repairs, `deploy/docker-compose.yml` is organizer deployment
  configuration and may contain the required literal `FLAG=<metadata.flag>`
  list entry under `environment:` (singular). Do not replace it with
  `${{FLAG}}` and do not move that value into `metadata.json`/`challenge.yml`
  injection logic. Plaintext flag material is forbidden in player-facing
  `attachments/`, solver hardcoding, and published artifacts, not in the
  required Compose injection entry.
- Do not call `./bin/progress` or `$WORKSPACE_ROOT/bin/progress`; this repair
  service records repair progress outside Hermes.

Terminal tool usage:
- The terminal may start in `/`, the workspace root, or a prior challenge root.
  Do not trust `pwd` and do not use relative guesses such as
  `output/challenges/...` or `cd ./output/challenges/...`.
- Start every terminal command that touches challenge files by assigning the
  allowed repair root literally, then `cd` to it:
  ```bash
  CHAL_ROOT={json.dumps(str(context["challenge_dir"]))}
  test -d "$CHAL_ROOT" || exit 1
  cd "$CHAL_ROOT" || exit 1
  ```
- After that, use direct child paths such as `validate.sh`, `writenup/exp.py`,
  `metadata.json`, `deploy/docker-compose.yml`, `attachments/`, `src/`, and
  `writenup/wp.md`. Never prepend `output/challenges/...` once inside
  `$CHAL_ROOT`.
- Before reading optional files, list the containing directory first, for
  example `ls -la deploy src attachments writenup 2>/dev/null || true`.
- Do not use `eval`, ad-hoc quoted command strings, or long shell one-liners
  with nested quotes. If a change needs complex JSON, sed replacement, or long
  text, use the file write/patch tool instead of terminal.
- If `pwd` prints `/`, immediately `cd "$CHAL_ROOT"` using the bootstrap above;
  do not run `ls output/...` from `/`.

Relevant file context:
{context.get("file_context") or "(none)"}
"""


def _hermes_arguments(category: str) -> list[str]:
    profile_name = f"cf-{category}"
    return hermes_process.inject_profile_argument(profile_name)


def _hermes_environment(
    paths: ProjectPaths,
    arguments: list[str],
    *,
    cwd: Path,
    profile_name: str,
) -> dict[str, str]:
    environment = os.environ.copy()
    if (
        hermes_process.project_hermes_home_is_configured(paths.hermes_home)
        and not environment.get("HERMES_HOME")
    ):
        environment["HERMES_HOME"] = str(paths.hermes_home)
    if hermes_process.apply_legacy_custom_provider(paths.hermes_home, environment):
        hermes_process.remove_conflicting_custom_pool(paths.hermes_home)
        query_index = arguments.index("-q") if "-q" in arguments else len(arguments)
        arguments[query_index:query_index] = ["--provider", "custom"]
    terminal_backend = hermes_process.effective_terminal_backend(
        paths.hermes_home,
        environment,
        profile_name=profile_name,
    )
    hermes_process.configure_terminal_workspace(
        environment,
        cwd=cwd,
        terminal_backend=terminal_backend,
    )
    return environment


def _failure_details(session, source) -> list[dict[str, Any]]:
    del session, source
    return []


def _file_context(challenge_dir: Path) -> str:
    snippets: list[str] = []
    for relative in (
        "metadata.json",
        "validate.sh",
        "writenup/exp.py",
        "README.md",
        "writenup/wp.md",
    ):
        path = challenge_dir / relative
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        snippets.append(f"--- {relative} ---\n{_middle_truncate(text, 6000)}")
    return "\n\n".join(snippets)


def _default_timeout_seconds() -> int:
    raw = os.environ.get("BUILD_ATTEMPT_REPAIR_TIMEOUT_SECONDS")
    if raw is not None and raw.strip():
        try:
            configured = int(raw)
        except ValueError:
            configured = 0
        if configured > 0:
            return configured
    return max(60, validation_repair_timeout_cap() * 2 + 120)


def _middle_truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    head = limit // 2
    tail = limit - head
    return f"{text[:head]}\n... <truncated> ...\n{text[-tail:]}"
