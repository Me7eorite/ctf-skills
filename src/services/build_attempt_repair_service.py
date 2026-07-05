"""Focused AI repair for an existing failed build attempt."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import sqlalchemy as sa

from core.build_timeout import validation_repair_timeout_cap
from core.clock import beijing_now_isoformat
from core.jsonio import read_json
from core.paths import ProjectPaths
from core.queue import ShardQueue
from domain.output_consistency import validate_workspace_success_state
from domain.pwn_artifact_evidence import (
    PwnArtifactEvidenceError,
    ensure_pwn_solver_evidence,
    final_pwn_artifact_prompt_block,
)
from domain.validation import pwn_source_protocol_token
from domain.validation_failure_governance import latest_failed_validation, summarize_validation_entry
from domain.validation_repair_policy import policy_for_validation_failure
from hermes import process as hermes_process
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
_EXECUTION_ID_RE = re.compile(
    r"/(?:workspace/executions|root/ctf-skills/work/executions)/"
    r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})\b"
)


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
        with _category_repair_lock(self.paths, str(context.get("category") or "unknown")):
            return self._repair_prepared(attempt_id, context)

    def _repair_prepared(
        self,
        attempt_id: UUID,
        context: dict[str, Any],
    ) -> BuildAttemptRepairResult:
        repair_id = f"repair-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:8]}"
        repair_dir = self.paths.executions / str(attempt_id) / "repairs" / repair_id
        repair_dir.mkdir(parents=True, exist_ok=False)
        events_path = repair_dir / "repair-events.jsonl"
        log_path = repair_dir / "hermes.log"
        prompt_path = repair_dir / "prompt.md"

        self._record_event(events_path, "analysis", "started", "analysis started")
        policy = context["repair_policy"]
        auto_result = (
            auto_repair_challenge(
                Path(context["challenge_dir"]),
                challenge_id=context["challenge_id"],
                allowed_mechanics=policy.deterministic_mechanics,
            )
            if policy.deterministic_mechanics
            else None
        )
        if auto_result is not None and auto_result.changed:
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
        pre_repair_fingerprint = _challenge_file_fingerprint(Path(context["challenge_dir"]))
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

        post_repair_fingerprint = _challenge_file_fingerprint(Path(context["challenge_dir"]))
        if pre_repair_fingerprint and pre_repair_fingerprint == post_repair_fingerprint:
            message = "Hermes repair made no changes; solver exploit logic still needs repair"
            self._record_event(
                events_path,
                "solve",
                "failed",
                message,
                repair_result="no_change",
                blocked_reason="solver_repair_noop",
                expected_next_action="fix solver exploit logic",
            )
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
            consistency_failure = _attempt_success_consistency_failure(self.paths, row)
            if consistency_failure is not None:
                _downgrade_inconsistent_success_to_failed(
                    self.paths,
                    row,
                    task_model.DesignTask,
                    session,
                    reason=str(consistency_failure.get("validation_error") or "success state is inconsistent"),
                )
            if row.status != "failed" and consistency_failure is None:
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
            latest_failure = (
                consistency_failure
                if consistency_failure is not None
                else latest_failed_validation(self.paths, row.id)
            )
            first_failure = _first_validation_failure(self.paths, row.id)
            if consistency_failure is not None:
                failure_summary = consistency_failure.get("validation_error") or failure_summary
            attempt = {
                "id": row.id,
                "shard_basename": row.shard_basename,
                "resulting_challenge_dir": row.resulting_challenge_dir,
                "design_task_id": row.design_task_id,
                "category": task.category,
                "failure_summary": failure_summary,
                "latest_failure": latest_failure,
                "first_failure": first_failure,
            }
            failure_details = _failure_details(latest_failure)

        payload = _attempt_payload(self.paths, attempt["shard_basename"])
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
        try:
            ensure_pwn_solver_evidence(challenge_dir)
        except PwnArtifactEvidenceError:
            pass
        file_context = _file_context(challenge_dir)
        context = {
            **attempt,
            "challenge_id": challenge_id,
            "challenge_dir": str(challenge_dir),
            "failure_details": failure_details,
            "repair_policy": policy_for_validation_failure(
                latest_failure or {},
                operator_triggered=True,
            ),
            "file_context": file_context,
        }
        _assert_no_context_leak(attempt["id"], context)
        return context

    @staticmethod
    def _record_event(
        path: Path,
        phase: str,
        status: str,
        message: str,
        **extra: Any,
    ) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "created_at": beijing_now_isoformat(),
            "phase": phase,
            "status": status,
            "message": message,
            **extra,
        }
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _attempt_payload(paths: ProjectPaths, shard_basename: str) -> dict[str, Any]:
    if Path(shard_basename).name != shard_basename:
        raise BuildAttemptRepairError("build attempt shard basename is invalid")
    candidates = (
        paths.shards / "failed" / shard_basename,
        paths.shards / "done" / shard_basename,
    )
    shard = next((candidate for candidate in candidates if candidate.is_file()), None)
    if shard is None or shard.is_symlink():
        raise BuildAttemptRepairError("attempt shard is missing")
    payload = read_json(shard, None)
    if not isinstance(payload, dict):
        raise BuildAttemptRepairError("attempt shard payload is invalid")
    return payload


def _attempt_success_consistency_failure(paths: ProjectPaths, row) -> dict[str, Any] | None:
    if row.status != "succeeded":
        return None
    result = validate_workspace_success_state(
        paths.executions / str(row.id) / "current"
    )
    if result.get("ok"):
        return None
    status = str(result.get("status") or "validation_inconclusive")
    reason = str(result.get("reason") or "success state is inconsistent")
    details = result.get("failure_details")
    return {
        "source": "publish-consistency",
        "challenge_id": None,
        "validation_status": status,
        "validation_error": reason,
        "validation_failure_class": status,
        "validation_failure_details": details if isinstance(details, list) else [],
    }


def _downgrade_inconsistent_success_to_failed(
    paths: ProjectPaths,
    row,
    task_cls,
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
    row.error = f"{reason}"
    row.finished_at = now
    task = session.get(task_cls, row.design_task_id)
    if task is not None:
        task.status = "build_failed"
        task.updated_at = now


@contextmanager
def _category_repair_lock(paths: ProjectPaths, category: str):
    locks_dir = paths.executions / "locks"
    locks_dir.mkdir(parents=True, exist_ok=True)
    safe_category = re.sub(r"[^A-Za-z0-9_.-]+", "_", category or "unknown")
    lock_path = locks_dir / f"repair-{safe_category}.lock"
    with lock_path.open("w", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


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
    policy = context.get("repair_policy")
    policy_summary = getattr(policy, "summary", None) or "(none)"
    latest_failure = context.get("latest_failure") or {}
    first_failure = context.get("first_failure") or {}
    validation_class = (
        latest_failure.get("validation_failure_class")
        if isinstance(latest_failure, dict)
        else None
    )
    validation_signature = (
        latest_failure.get("validation_failure_signature")
        if isinstance(latest_failure, dict)
        else None
    )
    semantic_plan = _semantic_repair_plan(context.get("failure_details") or [])
    prompt = f"""You are repairing an existing CTF challenge artifact.

This is not a new build, not a resume, and not a clean rebuild.

Workflow:
1. analysis: inspect the failure and current challenge files.
2. solve: make the smallest file changes needed to fix the failure.
3. verify: do not claim success yourself; host-side validation will run after you exit.

Failure summary:
{context.get("failure_summary") or "(none)"}

Structured failure details:
{json.dumps(context.get("failure_details") or [], ensure_ascii=False, indent=2)}

Semantic contract repair plan:
{semantic_plan}

Validation class:
- validation_failure_class: {validation_class or "(none)"}
- validation_failure_signature: {validation_signature or "(none)"}
- repair_policy: {policy_summary}

Governed repair context:
{json.dumps(_governed_manual_repair_context(first_failure, latest_failure), ensure_ascii=False, indent=2)}

Validation evidence:
{json.dumps(_validation_evidence(latest_failure), ensure_ascii=False, indent=2)}

Source-derived Pwn protocol token:
{_pwn_source_protocol_prompt(Path(context["challenge_dir"]), latest_failure)}

Structured Pwn stage guard:
{json.dumps(_pwn_stage_guard(latest_failure), ensure_ascii=False, indent=2)}

{final_pwn_artifact_prompt_block(Path(context["challenge_dir"]))}

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
- For Pwn repairs, do not invent or restore generic image names such as
  `pwn-canary:latest`, `pwn-demo:latest`, or `pwn-<slug>:latest`. The host
  runner owns final image identity and rewrites `metadata.docker_image`,
  Compose `image`, and Compose `container_name` to
  `pwn-{{workspace_id[:6]}}-{{challenge_slug}}:latest` before the controlled
  Docker build. If you touch those files, prefer the workspace-scoped pattern
  immediately rather than preserving a generic name.
- The host runner labels managed images with `ctf-factory.*` metadata and
  prunes workspace-scoped dangling managed images after successful Docker
  builds. Do not run broad `docker image prune` or `docker builder prune`
  commands from this repair.
- For Pwn Dockerfile repairs, preserve the xinetd/chroot scaffold's apt mirror
  fallback loop and mirror order. Do not replace it with one hardcoded mirror or
  remove fallback entries; if package fetch fails, keep the loop and adjust only
  the mirror list deliberately.
- For Pwn Dockerfile repairs, do not add `chroot` to `apt-get install`;
  Ubuntu/Debian provide the `chroot` command via `coreutils`. Remove that
  package name or use `coreutils`.
- For Pwn validation repairs, do not start by guessing payload bytes. First
  identify the failing `validate.sh` stage: `start_compose`, `wait_container`,
  `derive_protocol_token`, `wait_app_ready`, `run_solver`, `check_flag`, or
  `diagnostics`.
- Readiness is not a black-box guessing exercise. Use the source/design token
  shown above when available; `deploy/src/*.c`, `src/*`, README, writeup, and
  design files are valid protocol evidence. Exact full-token matching is not
  mandatory: if source says `Perfect Menu:`, waiting for a stable substring
  such as `Perfect` is enough to prove application response.
- `nc` may be used for connectivity tests, but `nc -z` is only port-open
  evidence. It is not application readiness. Replace `head -c`, `dd count=`,
  and EOF-based reads with a bounded socket or `nc` loop that succeeds as soon
  as the token is read.
- If the service has read the token and `validate.sh` entered `run_solver`,
  later EOF, BrokenPipe, missing flag, failed leak, or payload failure is a
  solver/protocol issue. The service is already ready; do not repair startup.
- If the wrapper probe fails but the container is Up and the port connects, do
  not stop with only a readiness claim. Prefer to run `exp.py` anyway, capture
  solver stdout/stderr/exit code, and then decide whether the evidence points
  to wrapper, service startup, binary path, or solver protocol.
- If solver stdout/stderr/exit code are missing, repair `validate.sh` capture
  first before changing exploit logic.
- If `exp.py` is protocol-desynchronized, focus on `sendline`/`sendafter`,
  newline handling, and exact delimiters. In particular, check for target code
  that reads a fixed payload and then consumes a newline with `getchar`.
- If structured evidence has `service_ready=true` and `exploit_started=true`,
  do not repair service readiness, host/port wiring, container startup, or menu
  probing unless a managed pwn-debug run proves the service is unavailable.
  Treat cleanup/readiness probe tail noise as lower priority than the real
  exploit stage.
- If `classification_conflicts` says validate.sh reported service-readiness
  failure while canonical pwn-debug TCP probe read Choice:/menu/banner, the
  service is reachable. Repair `validate.sh` readiness/capture first; do not
  spend the round on xinetd/chroot/container startup or guessed exploit constants.
- For Pwn solver repairs, Bound every `recvuntil` / `recvline` wait with short
  timeouts, cap brute-force or leak loops, and print bounded diagnostics for
  service ready state, the first banner line, the last recv position, and the
  failing phase before exit.
- For Pwn solver repairs, derive all offsets, symbols, gadgets, and reports
  from the final player ELF named by `metadata.artifact` under `attachments/`.
  Resolve that artifact from the challenge root in `writenup/exp.py`, e.g.
  `Path(__file__).resolve().parents[1] / metadata.artifact`; do not use
  `./attachments/<binary>` when `validate.sh` may run from `writenup`.
  `writenup/exp.py` must declare `BINARY_SHA256 = metadata.artifact_sha256`;
  aliases such as
  `ARTIFACT_SHA256`, SHAs from `deploy/src/<metadata.artifact basename>`, and old
  `pwn_debug_report.json` constants are stale.
- `deploy/src/` may be read to understand the bug and protocol, but it is not
  evidence for offsets, gadgets, symbols, libc bases, checksec, or report SHAs.
- For canary leaks, scan a broad bounded `%n$p` range, keep candidates stable
  across multiple fresh connections, require low byte `0x00`, and do not filter
  by a `2^48` threshold. For stack leaks, never fallback to guessed
  `0x7fffffffxxxx` addresses without a live leak. For fork canary brute force,
  print byte-level progress and use short per-attempt timeouts.
- Do not call `./bin/progress` or `$WORKSPACE_ROOT/bin/progress`; this repair
  service records repair progress outside Hermes.

Terminal tool usage:
- The terminal may start in `/`, the workspace root, or a prior challenge root.
  Do not trust `pwd` and do not use relative guesses such as
  `output/challenges/...` or `cd ./output/challenges/...`.
- Do not run any terminal command that contains `cd ./output/challenges/...`
  unless it first proves it is at the workspace root with
  `test -f ./input/shard.json`. If `pwd` is already under
  `output/challenges/<category>/<id>-<slug>/deploy/src`, that relative `cd`
  will look for a nested `output/` directory and fail.
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
- The same rule applies to file tools (`read_file`, `write_file`, patch): once
  the allowed repair root is the challenge root, use `deploy/Dockerfile`, not
  `./output/challenges/<category>/<id>-<slug>/deploy/Dockerfile`.
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
    return hermes_process.sanitize_prompt_text(prompt)


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


def _failure_details(latest_failure: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not latest_failure:
        return []
    details = latest_failure.get("validation_failure_details")
    if isinstance(details, list):
        return [item for item in details if isinstance(item, dict)]
    legacy = latest_failure.get("validation_contract_errors")
    if isinstance(legacy, list):
        return [
            {
                "phase": "contract",
                "code": "contract_error",
                "message": str(item),
            }
            for item in legacy
            if item
        ]
    return []


def _first_validation_failure(paths: ProjectPaths, attempt_id: UUID) -> dict[str, Any] | None:
    summary = summarize_validation_entry(
        read_json(
            paths.executions / str(attempt_id) / "current" / "state" / "first-validation-failure.json",
            None,
        ),
        source="first-validation-failure",
    )
    return summary if isinstance(summary, dict) else None


def _governed_manual_repair_context(
    first_failure: Any,
    latest_failure: Any,
) -> dict[str, Any]:
    latest = latest_failure if isinstance(latest_failure, dict) else {}
    first = first_failure if isinstance(first_failure, dict) else {}
    return {
        "root_validation_failure": _brief_failure(first or latest),
        "current_blocker": _brief_failure(latest),
        "classification_conflicts": latest.get("classification_conflicts") or [],
        "evidence_bundle": {
            "validate_stdout_tail": _budget_text(
                latest.get("validation_stdout_tail"),
                label="validation_stdout_tail",
            ),
            "validate_stderr_tail": _budget_text(
                latest.get("validation_stderr_tail"),
                label="validation_stderr_tail",
            ),
            "pwn_debug_tcp_probe": {
                "status": latest.get("pwn_debug_tcp_probe_status") or "(unavailable)",
                "matched_token": latest.get("pwn_debug_tcp_probe_matched_token") or "(unavailable)",
                "raw_output_tail": latest.get("pwn_debug_tcp_probe_raw_output_tail") or "(unavailable)",
            },
            "final_flag_candidate": latest.get("validation_final_flag_candidate") or "(unavailable)",
            "missing": latest.get("validation_diagnostic_unavailable") or [],
        },
    }


def _brief_failure(value: dict[str, Any]) -> dict[str, Any]:
    details = value.get("validation_failure_details")
    code = None
    if isinstance(details, list):
        for detail in details:
            if isinstance(detail, dict) and detail.get("code"):
                code = detail["code"]
                break
    return {
        key: item
        for key, item in {
            "challenge_id": value.get("challenge_id"),
            "validation_status": value.get("validation_status"),
            "validation_failure_class": value.get("validation_failure_class"),
            "validation_failure_signature": value.get("validation_failure_signature"),
            "pwn_failure_stage": value.get("pwn_failure_stage"),
            "diagnostic_code": code,
            "validation_error": value.get("validation_error"),
            "repair_result": value.get("repair_result"),
            "blocked_reason": value.get("blocked_reason"),
            "expected_next_action": value.get("expected_next_action"),
        }.items()
        if item not in (None, "", [])
    }


def _semantic_repair_plan(failure_details: Any) -> str:
    if not isinstance(failure_details, list):
        return "- No semantic contract details were supplied."
    details = [
        item
        for item in failure_details
        if isinstance(item, dict)
        and str(item.get("code") or "").startswith("semantic_")
    ]
    if not details:
        return "- No semantic contract details were supplied."
    families = sorted(
        {
            str(item.get("declared_family") or "")
            for item in details
            if item.get("declared_family")
        }
    )
    observed = sorted(
        {
            str(item.get("observed_family") or "")
            for item in details
            if item.get("observed_family")
        }
    )
    sources = list(
        dict.fromkeys(
            str(item.get("source") or item.get("path") or "")
            for item in details
            if item.get("source") or item.get("path")
        )
    )
    tokens = list(
        dict.fromkeys(
            str(item.get("conflict_token") or item.get("conflict_pattern") or "")
            for item in details
            if item.get("conflict_token") or item.get("conflict_pattern")
        )
    )
    actions = list(
        dict.fromkeys(
            str(item.get("repair_action") or item.get("hint") or "")
            for item in details
            if item.get("repair_action") or item.get("hint")
        )
    )
    lines = [
        "- Root cause: final artifact semantics conflict with the declared technique contract.",
        (
            "- Preserve the declared technique family"
            + (f" (`{', '.join(families)}`)" if families else "")
            + " unless the metadata declaration is genuinely wrong."
        ),
    ]
    if observed:
        lines.append(f"- Observed conflicting family: `{', '.join(observed)}`.")
    if sources:
        lines.append(
            "- Inspect and edit the cited final evidence files: "
            + ", ".join(f"`{source}`" for source in sources[:8])
            + "."
        )
    if tokens:
        lines.append(
            "- Remove or rewrite the conflicting semantic markers: "
            + ", ".join(f"`{token}`" for token in tokens[:10])
            + "."
        )
    lines.extend(f"- {action}" for action in actions[:6])
    lines.append(
        "- Do not silence the validator by deleting evidence, changing metadata to a different technique, "
        "or bypassing validation unless the actual challenge design has intentionally changed."
    )
    lines.append(
        "- After editing, rerun host validation; the final flag, writeup, report, source, and exploit "
        "must all describe the same technique family."
    )
    return "\n".join(lines)


def _pwn_stage_guard(latest_failure: Any) -> dict[str, Any]:
    if not isinstance(latest_failure, dict):
        return {
            "service_ready": False,
            "exploit_started": False,
            "exploit_exit_code": None,
            "exploit_stdout_tail": "(unavailable)",
            "exploit_stderr_tail": "(unavailable)",
            "pwn_debug_failure_stage": None,
            "validation_failure_class": None,
            "classification_conflicts": [],
        }
    stdout = str(latest_failure.get("validation_stdout_tail") or "")
    stderr = str(latest_failure.get("validation_stderr_tail") or "")
    text = f"{stdout}\n{stderr}".lower()
    canonical_ready = (
        latest_failure.get("pwn_debug_tcp_probe_status") == "ready"
        and bool(latest_failure.get("pwn_debug_tcp_probe_matched_token"))
    )
    return {
        "service_ready": "service is ready" in text or canonical_ready,
        "exploit_started": bool(
            re.search(r"\b(running exploit|exploit phase|starting exploit|launching exploit)\b", text)
        ),
        "exploit_exit_code": latest_failure.get("validation_returncode"),
        "exploit_stdout_tail": stdout[-1000:] if stdout else "(unavailable)",
        "exploit_stderr_tail": stderr[-1000:] if stderr else "(unavailable)",
        "pwn_debug_failure_stage": latest_failure.get("pwn_debug_failure_stage")
        or latest_failure.get("pwn_failure_stage"),
        "validation_failure_class": latest_failure.get("validation_failure_class"),
        "classification_conflicts": latest_failure.get("classification_conflicts") or [],
    }


def _validation_evidence(latest_failure: Any) -> dict[str, Any]:
    if not isinstance(latest_failure, dict):
        return {
            "validation_stdout_tail": "(unavailable)",
            "validation_stderr_tail": "(unavailable)",
            "validation_returncode": "(unavailable)",
            "validation_command": "(unavailable)",
            "missing": ["latest failed validation result unavailable"],
        }
    missing = latest_failure.get("validation_diagnostic_unavailable")
    if not isinstance(missing, list):
        missing = []
    evidence = {
        "validation_status": latest_failure.get("validation_status") or "(unavailable)",
        "validation_error": latest_failure.get("validation_error") or "(unavailable)",
        "validation_command": latest_failure.get("validation_command") or "(unavailable)",
        "validation_returncode": latest_failure.get("validation_returncode", "(unavailable)"),
        "validation_stdout_tail": _budget_text(
            latest_failure.get("validation_stdout_tail"),
            label="validation_stdout_tail",
        ),
        "validation_stderr_tail": _budget_text(
            latest_failure.get("validation_stderr_tail"),
            label="validation_stderr_tail",
        ),
        "validation_final_flag_candidate": latest_failure.get("validation_final_flag_candidate")
        or "(unavailable)",
        "validation_contract_errors": latest_failure.get("validation_contract_errors")
        or [],
        "missing": missing,
        "missing_solver_output": latest_failure.get("missing_solver_output", False),
        "pwn_debug_failure_stage": latest_failure.get("pwn_debug_failure_stage")
        or latest_failure.get("pwn_failure_stage"),
        "pwn_debug_tcp_probe": {
            "status": latest_failure.get("pwn_debug_tcp_probe_status") or "(unavailable)",
            "matched_token": latest_failure.get("pwn_debug_tcp_probe_matched_token") or "(unavailable)",
            "raw_output_tail": _budget_text(
                latest_failure.get("pwn_debug_tcp_probe_raw_output_tail"),
                label="pwn_debug_tcp_probe_raw_output_tail",
            ),
        },
        "pwn_source_protocol_token": latest_failure.get("pwn_source_protocol_token")
        or "(unavailable)",
        "pwn_source_protocol_token_source": latest_failure.get(
            "pwn_source_protocol_token_source"
        )
        or "(unavailable)",
        "classification_conflicts": latest_failure.get("classification_conflicts") or [],
    }
    return evidence


def _pwn_source_protocol_prompt(challenge_dir: Path, latest_failure: Any) -> str:
    token: str | None = None
    source: str | None = None
    if isinstance(latest_failure, dict):
        token_value = latest_failure.get("pwn_source_protocol_token")
        source_value = latest_failure.get("pwn_source_protocol_token_source")
        token = str(token_value) if token_value not in (None, "", []) else None
        source = str(source_value) if source_value not in (None, "", []) else None
    if token is None:
        derived = pwn_source_protocol_token(challenge_dir)
        if derived:
            token, source = derived
    if token:
        return (
            f"- token: {token!r}\n"
            f"- source: {source or '(unknown)'}\n"
            "- service ready means a fresh CHAL_HOST/CHAL_PORT connection read "
            "this token or a stable source-visible substring; after that, do "
            "not repair startup."
        )
    return (
        "- token: (not statically determined)\n"
        "- fallback order: Choice:, menu, Menu, Welcome, >\n"
        "- only use the fallback after checking deploy/src, src, README, writeup, "
        "and design files."
    )


def _file_context(challenge_dir: Path) -> str:
    snippets: list[str] = []
    metadata = read_json(challenge_dir / "metadata.json", {})
    for relative in (
        "metadata.json",
        "validate.sh",
        "writenup/exp.py",
        "writenup/pwn_debug_report.json",
        "README.md",
        "writenup/wp.md",
    ):
        path = challenge_dir / relative
        if not path.is_file():
            continue
        if relative == "writenup/pwn_debug_report.json":
            stale_context = _stale_pwn_debug_report_context(path, metadata)
            if stale_context is not None:
                snippets.append(f"--- {relative} ---\n{stale_context}")
                continue
        text = path.read_text(encoding="utf-8", errors="replace")
        snippets.append(f"--- {relative} ---\n{_budget_text(text, label=relative, limit=6000)}")
    return "\n\n".join(snippets)


def _challenge_file_fingerprint(challenge_dir: Path) -> str:
    digest = hashlib.sha256()
    if not challenge_dir.is_dir():
        return ""
    seen_file = False
    for path in sorted(
        item
        for item in challenge_dir.rglob("*")
        if item.is_file() and not item.is_symlink()
    ):
        try:
            relative = path.relative_to(challenge_dir).as_posix()
        except ValueError:
            continue
        if "/__pycache__/" in f"/{relative}/" or relative.endswith(".pyc"):
            continue
        try:
            stat = path.stat()
            content = path.read_bytes()
        except OSError:
            continue
        seen_file = True
        digest.update(relative.encode("utf-8", errors="replace"))
        digest.update(b"\0")
        digest.update(str(stat.st_mode & 0o777).encode("ascii"))
        digest.update(b"\0")
        digest.update(content)
        digest.update(b"\0")
    return digest.hexdigest() if seen_file else ""


def _stale_pwn_debug_report_context(path: Path, metadata: Any) -> str | None:
    if not isinstance(metadata, dict) or metadata.get("category") != "pwn":
        return None
    expected_sha = metadata.get("artifact_sha256")
    if not isinstance(expected_sha, str) or not expected_sha:
        return None
    report = read_json(path, {})
    report_sha = None
    if isinstance(report, dict):
        binary = report.get("binary")
        if isinstance(binary, dict):
            value = binary.get("sha256")
            if isinstance(value, str) and value:
                report_sha = value
    if report_sha == expected_sha:
        return None
    payload = {
        "stale": True,
        "reason": (
            "pwn_debug_report.json.binary.sha256 does not match "
            "metadata.artifact_sha256; stale offsets/gadgets are omitted from "
            "trusted repair context"
        ),
        "metadata_artifact_sha256": expected_sha,
        "report_binary_sha256": report_sha,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _assert_no_context_leak(current_attempt_id: UUID | str, context: dict[str, Any]) -> None:
    current = str(current_attempt_id)
    text = json.dumps(context, ensure_ascii=False, default=str)
    leaked = sorted({match.group(1) for match in _EXECUTION_ID_RE.finditer(text) if match.group(1) != current})
    if leaked:
        raise BuildAttemptRepairError(
            "orchestration-context-leak: repair context references non-current "
            f"attempt_id(s): {', '.join(leaked)}"
        )


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


def _budget_text(value: Any, *, label: str, limit: int = 2000) -> str:
    if value in (None, ""):
        return "(unavailable)"
    text = str(value)
    text = hermes_process.sanitize_prompt_text(text)
    if len(text) <= limit:
        return text
    return (
        f"{_middle_truncate(text, limit)}\n"
        f"... <{label} truncated from {len(text)} chars to {limit}> ..."
    )
