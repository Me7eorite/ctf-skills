"""HTTP endpoints for build-attempt orchestration and inspection."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from http import HTTPStatus
from pathlib import Path
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError

from core.build_timeout import shard_timeout_policy
from core.jsonio import read_json
from core.queue import SUPPORTED_CATEGORIES
from domain.build_attempts import BuildAttempt, BuildAttemptListItem, BuildAttemptStatus
from persistence.models import build_attempts as build_model
from persistence.models import design_tasks as task_model
from persistence.models.progress import ProgressEvent
from persistence.repositories import (
    BuildAttemptsRepository,
)
from services import BuildOrchestrationError, BuildOrchestrationService
from services.build_attempt_revalidation_service import (
    BuildAttemptRevalidationError,
    BuildAttemptRevalidationNotFoundError,
    BuildAttemptRevalidationService,
)

LOG = logging.getLogger(__name__)
DEFAULT_LIST_LIMIT = 100
MAX_LIST_LIMIT = 500


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        value = 0
    if value <= 0:
        LOG.warning("invalid %s=%r; using %s", name, raw, default)
        return default
    return value


BUILD_ATTEMPTS_LIST_DEFAULT_LIMIT = _env_int(
    "BUILD_ATTEMPTS_LIST_DEFAULT_LIMIT",
    DEFAULT_LIST_LIMIT,
)
BUILD_ATTEMPTS_LIST_MAX_LIMIT = _env_int(
    "BUILD_ATTEMPTS_LIST_MAX_LIMIT",
    MAX_LIST_LIMIT,
)


def register_build_attempts_endpoints(app: FastAPI) -> None:
    @app.post("/api/design-tasks/build")
    async def submit_design_tasks_for_build(request: Request) -> JSONResponse:
        try:
            payload = await request.json()
        except ValueError as exc:
            raise HTTPException(
                status_code=HTTPStatus.BAD_REQUEST,
                detail=f"request body must be JSON: {exc}",
            ) from exc
        if not isinstance(payload, dict):
            raise HTTPException(
                status_code=HTTPStatus.BAD_REQUEST,
                detail="request body must be a JSON object",
            )
        raw_ids = payload.get("design_task_ids")
        if not isinstance(raw_ids, list):
            raise HTTPException(
                status_code=HTTPStatus.BAD_REQUEST,
                detail="design_task_ids must be an array of UUID strings",
            )
        task_ids = [_parse_uuid(value, "design_task_ids") for value in raw_ids]
        attempt_ids = _submit_batch(app, task_ids)
        return JSONResponse(
            {"build_attempt_ids": [str(item) for item in attempt_ids]},
            status_code=HTTPStatus.CREATED,
        )

    @app.post("/api/design-tasks/{task_id}/build")
    def submit_design_task_for_build(task_id: str) -> JSONResponse:
        task_uuid = _parse_uuid(task_id, "design task id")
        attempt_id = _submit_single(app, task_uuid)
        return JSONResponse(
            {"build_attempt_id": str(attempt_id)},
            status_code=HTTPStatus.CREATED,
        )

    @app.get("/api/build-attempts")
    def list_build_attempts(
        status: str | None = Query(default=None),
        worker: str | None = Query(default=None),
        design_task_id: str | None = Query(default=None),
        generation_request_id: str | None = Query(default=None),
        category: str | None = Query(default=None),
        limit: str | None = Query(default=None),
    ) -> JSONResponse:
        if status is not None and status not in BuildAttemptStatus:
            raise HTTPException(
                status_code=HTTPStatus.BAD_REQUEST,
                detail=(
                    f"unknown status {status!r}; allowed: "
                    f"{list(BuildAttemptStatus)}"
                ),
            )
        if category is not None and category not in SUPPORTED_CATEGORIES:
            raise HTTPException(
                status_code=HTTPStatus.BAD_REQUEST,
                detail=f"unknown category {category!r}; allowed: {sorted(SUPPORTED_CATEGORIES)}",
            )
        task_uuid = _parse_optional_uuid(design_task_id, "design_task_id")
        request_uuid = _parse_optional_uuid(
            generation_request_id,
            "generation_request_id",
        )
        requested_limit = _parse_limit(limit)
        capped = min(requested_limit, BUILD_ATTEMPTS_LIST_MAX_LIMIT)

        from persistence.session import transaction

        with transaction() as session:
            rows = BuildAttemptsRepository(session).list_attempts(
                design_task_id=task_uuid,
                generation_request_id=request_uuid,
                status=status,
                worker=worker,
                category=category,
                limit=capped,
            )
            summaries = _failure_summaries(
                session,
                [row.shard_basename for row in rows],
            )
        headers = {}
        if capped != requested_limit:
            headers["X-Limit-Capped"] = str(capped)
        return JSONResponse(
            [_list_item_dict(row, summaries=summaries) for row in rows],
            headers=headers,
        )

    @app.get("/api/build-attempts/{attempt_id}")
    def get_build_attempt(attempt_id: str) -> JSONResponse:
        attempt_uuid = _parse_uuid(attempt_id, "build attempt id", not_found=True)

        from persistence.session import transaction

        with transaction() as session:
            repo = BuildAttemptsRepository(session)
            attempt = repo.get(attempt_uuid)
            if attempt is None:
                raise HTTPException(
                    status_code=HTTPStatus.NOT_FOUND,
                    detail="build attempt not found",
                )
            siblings = repo.list_for_design_task(attempt.design_task_id)
            events = session.scalars(
                sa.select(ProgressEvent)
                .where(ProgressEvent.shard == attempt.shard_basename)
                .order_by(ProgressEvent.id.asc())
            ).all()

        event_payloads = [_progress_event_dict(row) for row in events]
        body = _attempt_dict(
            attempt,
            failure_summary=_derive_failure_summary(event_payloads, attempt.error),
        )
        timeout_manifest = read_json(
            _project_paths(app).executions / str(attempt.id) / "input" / "manifest.json",
            {},
        )
        if isinstance(timeout_manifest, dict):
            if isinstance(timeout_manifest.get("effective_timeout_seconds"), int):
                body["effective_timeout_seconds"] = timeout_manifest[
                    "effective_timeout_seconds"
                ]
                body["timeout_source"] = timeout_manifest.get("timeout_source")
        body["sibling_attempts"] = [_attempt_dict(row) for row in siblings]
        body["progress_events"] = event_payloads
        return JSONResponse(body)

    @app.post("/api/build-attempts/worker/start")
    async def start_category_build_worker(request: Request) -> JSONResponse:
        try:
            payload = await request.json()
        except ValueError as exc:
            raise HTTPException(
                status_code=HTTPStatus.BAD_REQUEST,
                detail=f"request body must be JSON: {exc}",
            ) from exc
        if not isinstance(payload, dict):
            raise HTTPException(
                status_code=HTTPStatus.BAD_REQUEST,
                detail="request body must be a JSON object",
            )
        category = payload.get("category")
        if category not in SUPPORTED_CATEGORIES:
            raise HTTPException(
                status_code=HTTPStatus.BAD_REQUEST,
                detail=(
                    f"unknown category {category!r}; "
                    f"allowed: {sorted(SUPPORTED_CATEGORIES)}"
                ),
            )

        BuildOrchestrationService(paths=_project_paths(app)).recover_staging()
        selected = _next_eligible_attempt(app, category)
        if selected is None:
            raise HTTPException(
                status_code=HTTPStatus.CONFLICT,
                detail=f"no queued {category} build attempt has a matching pending shard",
            )
        attempt_id, selected_category = selected
        return _start_constrained_worker(app, attempt_id, selected_category)

    @app.post("/api/build-attempts/{attempt_id}/worker/start")
    def start_attempt_build_worker(attempt_id: str) -> JSONResponse:
        attempt_uuid = _parse_uuid(
            attempt_id,
            "build attempt id",
            not_found=True,
        )
        BuildOrchestrationService(paths=_project_paths(app)).recover_staging()
        selected = _exact_eligible_attempt(app, attempt_uuid)
        if selected is None:
            raise HTTPException(
                status_code=HTTPStatus.NOT_FOUND,
                detail="build attempt not found",
            )
        status, category, matches_pending = selected
        if status != "queued":
            raise HTTPException(
                status_code=HTTPStatus.CONFLICT,
                detail=f"build attempt is {status}, expected queued",
            )
        if not matches_pending:
            raise HTTPException(
                status_code=HTTPStatus.CONFLICT,
                detail="build attempt has no matching pending shard",
            )
        return _start_constrained_worker(app, attempt_uuid, category)

    @app.post("/api/build-attempts/worker/start-sequential")
    async def start_sequential_build_worker(request: Request) -> JSONResponse:
        try:
            payload = await request.json()
        except ValueError as exc:
            raise HTTPException(
                status_code=HTTPStatus.BAD_REQUEST,
                detail=f"request body must be JSON: {exc}",
            ) from exc
        if not isinstance(payload, dict):
            raise HTTPException(
                status_code=HTTPStatus.BAD_REQUEST,
                detail="request body must be a JSON object",
            )
        raw_ids = payload.get("build_attempt_ids")
        if not isinstance(raw_ids, list) or not raw_ids:
            raise HTTPException(
                status_code=HTTPStatus.BAD_REQUEST,
                detail="build_attempt_ids must be a non-empty array of UUID strings",
            )
        attempt_ids = [_parse_uuid(value, "build_attempt_ids") for value in raw_ids]
        if len(set(attempt_ids)) != len(attempt_ids):
            raise HTTPException(
                status_code=HTTPStatus.BAD_REQUEST,
                detail="duplicate build attempt ids are not allowed",
            )

        BuildOrchestrationService(paths=_project_paths(app)).recover_staging()
        for attempt_id in attempt_ids:
            selected = _exact_eligible_attempt(app, attempt_id)
            if selected is None:
                raise HTTPException(
                    status_code=HTTPStatus.NOT_FOUND,
                    detail=f"build attempt {attempt_id} not found",
                )
            status, _category, matches_pending = selected
            if status != "queued" or not matches_pending:
                raise HTTPException(
                    status_code=HTTPStatus.CONFLICT,
                    detail=(
                        f"build attempt {attempt_id} is not an eligible queued task"
                    ),
                )

        tasks = app.state.dashboard_tasks
        ok, message = tasks.start_sequential_worker(
            build_attempt_ids=attempt_ids,
        )
        if not ok:
            raise HTTPException(status_code=HTTPStatus.CONFLICT, detail=message)
        return JSONResponse(
            {
                "ok": True,
                "message": message,
                "build_attempt_ids": [str(item) for item in attempt_ids],
                "queue_length": len(attempt_ids),
            },
            status_code=HTTPStatus.ACCEPTED,
        )

    @app.post("/api/build-attempts/{attempt_id}/retry")
    def retry_build_attempt(attempt_id: str) -> JSONResponse:
        attempt_uuid = _parse_uuid(attempt_id, "build attempt id")
        try:
            new_id = BuildOrchestrationService(paths=_project_paths(app)).retry(
                attempt_uuid
            )
        except BuildOrchestrationError as exc:
            raise HTTPException(
                status_code=HTTPStatus.CONFLICT,
                detail=str(exc),
            ) from exc
        except IntegrityError as exc:
            raise HTTPException(
                status_code=HTTPStatus.CONFLICT,
                detail="a build is already active for this design task",
            ) from exc
        return JSONResponse(
            {"build_attempt_id": str(new_id)},
            status_code=HTTPStatus.CREATED,
        )

    @app.post("/api/build-attempts/{attempt_id}/restore")
    def restore_build_attempt(attempt_id: str) -> JSONResponse:
        """Restore a wrongly-marked-lost attempt back to queued.

        Operator escape hatch for the known reconciler race: an attempt can
        be marked `lost` even while its shard file is still in pending/. This
        endpoint:
          - verifies the row is currently `lost`
          - verifies the shard file (or its claim sidecar) is physically
            present somewhere in the queue
          - resets row.status → queued, clears finished_at/error
          - resets the parent design_task back to `building`
        Anything else (succeeded/failed/queued/running) is rejected; this is
        deliberately not a generic state-edit endpoint.
        """
        attempt_uuid = _parse_uuid(attempt_id, "build attempt id", not_found=True)
        paths = _project_paths(app)
        from persistence.session import transaction as _txn
        from persistence.session import SessionFactory as _SF
        with _txn(factory=_SF()) as session:
            row = session.get(build_model.BuildAttempt, attempt_uuid)
            if row is None:
                raise HTTPException(
                    status_code=HTTPStatus.NOT_FOUND,
                    detail=f"build attempt {attempt_uuid} not found",
                )
            if row.status != "lost":
                raise HTTPException(
                    status_code=HTTPStatus.CONFLICT,
                    detail=f"only lost attempts can be restored; current status: {row.status}",
                )
            # 物理存在性校验：避免恢复一个真没了的 shard。
            shard_basename = row.shard_basename
            located: Path | None = None
            for state in ("pending", "done", "failed"):
                candidate = paths.shards / state / shard_basename
                if candidate.is_file():
                    located = candidate
                    break
            if located is None:
                # running/ 下文件名带 worker 后缀
                expected = Path(shard_basename)
                for candidate in (paths.shards / "running").glob(
                    f"{expected.stem}.*{expected.suffix}"
                ):
                    if candidate.name.endswith(".claim.json"):
                        continue
                    located = candidate
                    break
            if located is None:
                raise HTTPException(
                    status_code=HTTPStatus.CONFLICT,
                    detail=(
                        f"cannot restore: shard file {shard_basename} not found in any "
                        "queue directory. Resubmit via retry instead."
                    ),
                )
            row.status = "queued"
            row.finished_at = None
            row.error = None
            row.artifact_status = "unknown"
            task = session.get(task_model.DesignTask, row.design_task_id)
            if task is not None and task.status == "build_failed":
                task.status = "building"
                task.updated_at = datetime.now(timezone.utc)
        return JSONResponse(
            {
                "build_attempt_id": str(attempt_uuid),
                "restored_from": "lost",
                "shard_found_at": str(located),
            },
            status_code=HTTPStatus.OK,
        )

    @app.post("/api/build-attempts/{attempt_id}/revalidate")
    def revalidate_build_attempt(attempt_id: str) -> JSONResponse:
        attempt_uuid = _parse_uuid(attempt_id, "build attempt id", not_found=True)
        progress = getattr(app.state, "progress_store", None)
        if progress is None:
            raise HTTPException(
                status_code=HTTPStatus.CONFLICT,
                detail="progress store is not configured",
            )
        try:
            BuildAttemptRevalidationService(
                paths=_project_paths(app),
                progress=progress,
            ).revalidate(attempt_uuid)
        except BuildAttemptRevalidationNotFoundError as exc:
            raise HTTPException(
                status_code=HTTPStatus.NOT_FOUND,
                detail=str(exc),
            ) from exc
        except BuildAttemptRevalidationError as exc:
            raise HTTPException(
                status_code=HTTPStatus.CONFLICT,
                detail=str(exc),
            ) from exc

        from persistence.session import transaction

        with transaction() as session:
            attempt = BuildAttemptsRepository(session).get(attempt_uuid)
            if attempt is None:
                raise HTTPException(
                    status_code=HTTPStatus.NOT_FOUND,
                    detail="build attempt not found",
                )
            return JSONResponse(_attempt_dict(attempt))

    @app.delete("/api/build-attempts/{attempt_id}")
    def delete_build_attempt(
        attempt_id: str,
        delete_artifacts: bool = Query(default=False),
    ) -> JSONResponse:
        from services import (
            ResourceDeletionConflictError,
            ResourceDeletionNotFoundError,
        )
        from web.resource_deletion import deletion_service

        attempt_uuid = _parse_uuid(attempt_id, "build attempt id", not_found=True)
        try:
            result = deletion_service(app).delete_build_attempt(
                attempt_uuid,
                delete_artifacts=delete_artifacts,
            )
        except ResourceDeletionNotFoundError as exc:
            raise HTTPException(
                status_code=HTTPStatus.NOT_FOUND,
                detail=str(exc),
            ) from exc
        except ResourceDeletionConflictError as exc:
            raise HTTPException(
                status_code=HTTPStatus.CONFLICT,
                detail=str(exc),
            ) from exc
        return JSONResponse(result.to_dict())


def _submit_batch(app: FastAPI, task_ids: list[UUID]) -> list[UUID]:
    try:
        return BuildOrchestrationService(paths=_project_paths(app)).submit_batch(task_ids)
    except BuildOrchestrationError as exc:
        raise HTTPException(
            status_code=HTTPStatus.CONFLICT,
            detail=str(exc),
        ) from exc
    except IntegrityError as exc:
        raise HTTPException(
            status_code=HTTPStatus.CONFLICT,
            detail="a build is already active for this design task",
        ) from exc


def _submit_single(app: FastAPI, task_id: UUID) -> UUID:
    try:
        return BuildOrchestrationService(paths=_project_paths(app)).submit_single(task_id)
    except BuildOrchestrationError as exc:
        raise HTTPException(
            status_code=HTTPStatus.CONFLICT,
            detail=str(exc),
        ) from exc
    except IntegrityError as exc:
        raise HTTPException(
            status_code=HTTPStatus.CONFLICT,
            detail="a build is already active for this design task",
        ) from exc


def _parse_limit(raw: str | None) -> int:
    if raw is None:
        return BUILD_ATTEMPTS_LIST_DEFAULT_LIMIT
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=HTTPStatus.BAD_REQUEST,
            detail="limit must be a positive integer",
        ) from exc
    if value <= 0:
        raise HTTPException(
            status_code=HTTPStatus.BAD_REQUEST,
            detail="limit must be a positive integer",
        )
    return value


def _next_eligible_attempt(app: FastAPI, category: str) -> tuple[UUID, str] | None:
    from persistence.session import transaction

    paths = _project_paths(app)
    with transaction() as session:
        rows = session.execute(
            sa.select(build_model.BuildAttempt, task_model.DesignTask.category)
            .join(
                task_model.DesignTask,
                task_model.DesignTask.id == build_model.BuildAttempt.design_task_id,
            )
            .where(
                build_model.BuildAttempt.status == "queued",
                task_model.DesignTask.category == category,
            )
            .order_by(
                build_model.BuildAttempt.created_at.asc(),
                build_model.BuildAttempt.id.asc(),
            )
        ).all()
        for attempt, row_category in rows:
            if _pending_payload_matches(
                paths,
                attempt_id=attempt.id,
                design_task_id=attempt.design_task_id,
                shard_basename=attempt.shard_basename,
                category=row_category,
            ):
                return attempt.id, row_category
    return None


def _exact_eligible_attempt(
    app: FastAPI,
    attempt_id: UUID,
) -> tuple[str, str, bool] | None:
    from persistence.session import transaction

    paths = _project_paths(app)
    with transaction() as session:
        row = session.execute(
            sa.select(build_model.BuildAttempt, task_model.DesignTask.category)
            .join(
                task_model.DesignTask,
                task_model.DesignTask.id == build_model.BuildAttempt.design_task_id,
            )
            .where(build_model.BuildAttempt.id == attempt_id)
        ).one_or_none()
        if row is None:
            return None
        attempt, category = row
        matches = _pending_payload_matches(
            paths,
            attempt_id=attempt.id,
            design_task_id=attempt.design_task_id,
            shard_basename=attempt.shard_basename,
            category=category,
        )
        return attempt.status, category, matches


def _pending_payload_matches(
    paths,
    *,
    attempt_id: UUID,
    design_task_id: UUID,
    shard_basename: str,
    category: str,
) -> bool:
    if Path(shard_basename).name != shard_basename:
        return False
    if shard_basename != f"{attempt_id}.json":
        return False
    shard = paths.shards / "pending" / shard_basename
    if shard.is_symlink() or not shard.is_file():
        return False
    payload = read_json(shard, None)
    if not isinstance(payload, dict):
        return False
    try:
        payload_attempt_id = UUID(str(payload.get("build_attempt_id")))
        payload_design_task_id = UUID(str(payload.get("design_task_id")))
    except (TypeError, ValueError, AttributeError):
        return False
    challenges = payload.get("challenges")
    return bool(
        payload_attempt_id == attempt_id
        and payload_design_task_id == design_task_id
        and isinstance(challenges, list)
        and challenges
        and all(
            isinstance(challenge, dict)
            and challenge.get("category") == category
            for challenge in challenges
        )
    )


def _start_constrained_worker(
    app: FastAPI,
    attempt_id: UUID,
    category: str,
) -> JSONResponse:
    effective_timeout, timeout_source = _effective_timeout_for_attempt(
        _project_paths(app), attempt_id
    )
    tasks = app.state.dashboard_tasks
    ok, message = tasks.start_worker(
        category=category,
        build_attempt_id=attempt_id,
    )
    if not ok:
        raise HTTPException(status_code=HTTPStatus.CONFLICT, detail=message)
    return JSONResponse(
        {
            "ok": True,
            "message": message,
            "build_attempt_id": str(attempt_id),
            "effective_timeout_seconds": effective_timeout,
            "timeout_source": timeout_source,
        },
        status_code=HTTPStatus.ACCEPTED,
    )


def _effective_timeout_for_attempt(paths, attempt_id: UUID) -> tuple[int, str]:
    env_raw = os.environ.get("HERMES_TIMEOUT")
    if env_raw:
        try:
            value = int(env_raw)
        except ValueError:
            value = 0
        if value > 0:
            return value, "env"
    payload = read_json(paths.shards / "pending" / f"{attempt_id}.json", {})
    return shard_timeout_policy(payload), "shard_policy"


def _parse_uuid(value: Any, label: str, *, not_found: bool = False) -> UUID:
    if not isinstance(value, str):
        raise HTTPException(
            status_code=HTTPStatus.BAD_REQUEST,
            detail=f"{label} must be a uuid",
        )
    try:
        return UUID(value)
    except ValueError as exc:
        raise HTTPException(
            status_code=HTTPStatus.NOT_FOUND if not_found else HTTPStatus.BAD_REQUEST,
            detail=f"{label} must be a uuid",
        ) from exc


def _parse_optional_uuid(value: str | None, label: str) -> UUID | None:
    if value is None:
        return None
    return _parse_uuid(value, label)


def _attempt_dict(
    attempt: BuildAttempt,
    *,
    failure_summary: str | None = None,
) -> dict[str, Any]:
    payload = {
        "id": str(attempt.id),
        "design_task_id": str(attempt.design_task_id),
        "attempt_no": attempt.attempt_no,
        "status": attempt.status,
        "shard_basename": attempt.shard_basename,
        "worker": attempt.worker,
        "resulting_challenge_dir": attempt.resulting_challenge_dir,
        "artifact_status": attempt.artifact_status,
        "error": attempt.error,
        "created_at": _isofmt(attempt.created_at),
        "started_at": _isofmt(attempt.started_at),
        "finished_at": _isofmt(attempt.finished_at),
    }
    if failure_summary:
        payload["failure_summary"] = failure_summary
    return payload


def _list_item_dict(
    item: BuildAttemptListItem,
    *,
    summaries: dict[str, str] | None = None,
) -> dict[str, Any]:
    row = _attempt_dict(
        item,
        failure_summary=(summaries or {}).get(item.shard_basename)
        or _derive_failure_summary([], item.error),
    )
    row.update(
        {
            "generation_request_id": str(item.generation_request_id),
            "challenge_id": item.challenge_id,
            "title": item.title,
            "category": item.category,
            "difficulty": item.difficulty,
            "percent": item.percent,
        }
    )
    return row


def _failure_summaries(
    session,
    shards: list[str],
) -> dict[str, str]:
    if not shards:
        return {}
    events = session.scalars(
        sa.select(ProgressEvent)
        .where(ProgressEvent.shard.in_(set(shards)))
        .order_by(ProgressEvent.shard.asc(), ProgressEvent.id.asc())
    ).all()
    grouped: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        grouped.setdefault(event.shard, []).append(_progress_event_dict(event))
    return {
        shard: summary
        for shard, rows in grouped.items()
        if (summary := _derive_failure_summary(rows, None))
    }


def _derive_failure_summary(
    events: list[dict[str, Any]],
    fallback: str | None,
) -> str | None:
    # The latest validate/complete terminal event is the source of truth — a
    # successful revalidate appends new passed events after the old failed
    # ones, so the summary MUST follow the newest result. Short-circuit on
    # the first terminal event seen newest-first; if it's passed, there is
    # no failure to report.
    for event in reversed(events):
        stage = event.get("stage")
        status = event.get("status")
        if stage not in ("validate", "complete") or status not in (
            "passed",
            "failed",
        ):
            continue
        if status == "passed":
            return None
        reason = _failure_message_reason(event.get("message") or "")
        if stage == "validate":
            return f"校验失败：{reason}" if reason else "校验失败"
        return f"构建执行失败：{reason}" if reason else "构建执行失败"
    if fallback and fallback != "shard execution failed":
        return fallback
    if fallback:
        return "构建执行失败"
    return None


# 中文注释：把 ChallengeValidator / hermes runner 写入 progress message 的
# 英文状态码翻译成面向用户的中文描述。状态码本身保持英文（DB / 测试 / 日志
# 仍按英文匹配），仅在面向 UI 的失败摘要里转换。新加状态码时记得同步这张表。
_FAILURE_REASON_TRANSLATIONS: dict[str, str] = {
    "contract_failed": "合约校验未通过（缺少必需文件、字段或不符约定）",
    "nonzero_exit": "参考解题脚本执行失败（validate.sh 返回非 0）",
    "flag_mismatch": "解题脚本输出的 flag 与 metadata 中声明的不一致",
    "missing_validation": "缺少 validate.sh，无法执行解题校验",
    "invalid_metadata": "metadata.json 不是合法的 JSON 对象",
    "timeout": "参考解题脚本执行超时",
    "no_shell": "校验所需的 shell 不可用（默认 bash）",
    "skipped_resume": "断点恢复跳过本次校验",
    # 基础设施类（来自 hermes/workspace + runner 的早期失败）
    "no compiled ELF artifact found in attachments/ or dist/":
        "未找到编译后的 ELF 产物（请放到 attachments/ 下）",
    "shard execution failed": "Hermes 执行阶段失败",
    "Workspace preflight failed": "执行 workspace 预检失败",
    "Workspace materialization failed": "执行 workspace 物化失败",
    "Workspace shim materialization failed": "进度蜘蛛生成失败",
    "attributed shard disappeared from all queue states":
        "shard 文件从队列中消失（可能是 reconciler 误判，参考 /restore 接口）",
    "artifact directory missing": "构建产物目录缺失（worker 标记 done 但 work/challenges 下找不到）",
}


def _translate_failure_reason(reason: str) -> str:
    """先按完整字符串查表；查不到再做"前缀"匹配（带 error= 详情的情况）。"""
    stripped = reason.strip()
    if stripped in _FAILURE_REASON_TRANSLATIONS:
        return _FAILURE_REASON_TRANSLATIONS[stripped]
    # 如果是 "validator: status=X" 这种带格式的，提取 status 再翻译
    if stripped.startswith("validator: status="):
        # validator: status=nonzero_exit elapsed=4.44s -> nonzero_exit
        rest = stripped[len("validator: status="):]
        status_code = rest.split(" ", 1)[0]
        translated = _FAILURE_REASON_TRANSLATIONS.get(status_code)
        if translated is not None:
            return translated
    return reason


def _failure_message_reason(message: str) -> str:
    marker = "error="
    if marker in message:
        raw = message.split(marker, 1)[1].strip(" ;,")
    else:
        raw = message.strip()
    return _translate_failure_reason(raw)


def _progress_event_dict(event: ProgressEvent) -> dict[str, Any]:
    return {
        "id": event.id,
        "shard": event.shard,
        "challenge_id": event.challenge_id,
        "worker": event.worker,
        "stage": event.stage,
        "status": event.status,
        "percent": event.percent,
        "message": event.message,
        "created_at": _isofmt(event.created_at),
    }


def _isofmt(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def _project_paths(app: FastAPI):
    from core.paths import ProjectPaths

    return getattr(app.state, "project_paths", None) or ProjectPaths.discover()
