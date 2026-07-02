"""HTTP endpoints for build-attempt orchestration and inspection."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import replace
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
from core.clock import beijing_isoformat
from core.jsonio import read_json
from core.queue import SUPPORTED_CATEGORIES
from domain.build_attempts import BuildAttempt, BuildAttemptListItem, BuildAttemptStatus
from hermes.process import hermes_profile_health
from persistence.models import build_attempts as build_model
from persistence.models import design_tasks as task_model
from persistence.models import executions as exec_model
from persistence.models.progress import ProgressEvent
from persistence.repositories import (
    BuildAttemptsRepository,
    ExecutionsRepository,
)
from services import BuildOrchestrationError, BuildOrchestrationService
from services.build_attempt_repair_service import (
    BuildAttemptRepairError,
    BuildAttemptRepairService,
)
from services.build_attempt_revalidation_service import (
    BuildAttemptRevalidationError,
    BuildAttemptRevalidationNotFoundError,
    BuildAttemptRevalidationService,
)
from services.build_profile_readiness import unavailable_build_profiles

LOG = logging.getLogger(__name__)
DEFAULT_LIST_LIMIT = 200
MAX_LIST_LIMIT = 500
DEFAULT_SEQUENTIAL_LANES = 4
DEFAULT_MAX_SEQUENTIAL_LANES = 6


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
BUILD_ATTEMPTS_MAX_SEQUENTIAL_LANES = _env_int(
    "BUILD_ATTEMPTS_MAX_SEQUENTIAL_LANES",
    DEFAULT_MAX_SEQUENTIAL_LANES,
)


def register_build_attempts_endpoints(app: FastAPI) -> None:
    @app.get("/api/build-attempts/logs")
    def list_build_logs() -> JSONResponse:
        paths = _project_paths(app)
        return JSONResponse(_collect_build_logs(paths))

    @app.get("/api/build-attempts/logs/{name:path}")
    def get_build_log(name: str) -> JSONResponse:
        paths = _project_paths(app)
        safe_name = name.replace("\\", "/").rsplit("/", 1)[-1]
        path = _build_log_path(paths, safe_name)
        if path is None:
            raise HTTPException(
                status_code=HTTPStatus.NOT_FOUND, detail="log not found"
            )
        executions_root = paths.executions.resolve()
        try:
            path.resolve().relative_to(executions_root)
            content = path.read_text(encoding="utf-8", errors="replace")[-30000:]
        except ValueError as exc:
            raise HTTPException(
                status_code=HTTPStatus.FORBIDDEN, detail="forbidden"
            ) from exc
        except OSError as exc:
            raise HTTPException(
                status_code=HTTPStatus.NOT_FOUND, detail="log not found"
            ) from exc
        return JSONResponse({"name": safe_name, "content": content})

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
        _require_task_build_profiles(app, task_ids)
        attempt_ids = _submit_batch(app, task_ids)
        return JSONResponse(
            {"build_attempt_ids": [str(item) for item in attempt_ids]},
            status_code=HTTPStatus.CREATED,
        )

    @app.post("/api/design-tasks/{task_id}/build")
    def submit_design_task_for_build(task_id: str) -> JSONResponse:
        task_uuid = _parse_uuid(task_id, "design task id")
        _require_task_build_profiles(app, [task_uuid])
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
        _sync_finished_dashboard_workers(app)
        if status is not None and status not in BuildAttemptStatus:
            raise HTTPException(
                status_code=HTTPStatus.BAD_REQUEST,
                detail=(f"unknown status {status!r}; allowed: {list(BuildAttemptStatus)}"),
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
            rows = [
                _normalize_artifact_status(
                    session,
                    _project_paths(app),
                    row,
                    category=row.category,
                    challenge_id=row.challenge_id,
                )
                for row in rows
            ]
            summaries = _failure_summaries(
                session,
                [row.shard_basename for row in rows],
            )
        headers = {}
        if capped != requested_limit:
            headers["X-Limit-Capped"] = str(capped)
        return JSONResponse(
            [
                _list_item_dict(
                    row,
                    project_root=_project_paths(app).root,
                    summaries=summaries,
                )
                for row in rows
            ],
            headers=headers,
        )

    @app.get("/api/build-attempts/{attempt_id}")
    def get_build_attempt(attempt_id: str) -> JSONResponse:
        _sync_finished_dashboard_workers(app)
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
            category = session.scalar(
                sa.select(task_model.DesignTask.category).where(task_model.DesignTask.id == attempt.design_task_id)
            )
            challenge_id = session.scalar(
                sa.select(task_model.DesignTask.challenge_id).where(task_model.DesignTask.id == attempt.design_task_id)
            )
            if isinstance(category, str) and isinstance(challenge_id, str):
                attempt = _normalize_artifact_status(
                    session,
                    _project_paths(app),
                    attempt,
                    category=category,
                    challenge_id=challenge_id,
                )
            events = session.scalars(
                sa.select(ProgressEvent)
                .where(ProgressEvent.shard == attempt.shard_basename)
                .order_by(ProgressEvent.id.asc())
            ).all()
            executions = ExecutionsRepository(session).list_for_attempt(attempt.id)

        event_payloads = [_progress_event_dict(row) for row in events]
        body = _attempt_dict(
            attempt,
            project_root=_project_paths(app).root,
            failure_summary=_derive_failure_summary(event_payloads, attempt.error),
        )
        body["category"] = category
        timeout_manifest = read_json(
            _project_paths(app).executions / str(attempt.id) / "input" / "manifest.json",
            {},
        )
        if isinstance(timeout_manifest, dict):
            if isinstance(timeout_manifest.get("effective_timeout_seconds"), int):
                body["effective_timeout_seconds"] = timeout_manifest["effective_timeout_seconds"]
                body["timeout_source"] = timeout_manifest.get("timeout_source")
        body["sibling_attempts"] = [
            _attempt_dict(row, project_root=_project_paths(app).root)
            for row in siblings
        ]
        body["progress_events"] = event_payloads
        body["executions"] = [_execution_dict(row) for row in executions]
        body["repair_runs"] = _repair_runs(_project_paths(app), attempt.id)
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
                detail=(f"unknown category {category!r}; allowed: {sorted(SUPPORTED_CATEGORIES)}"),
            )
        _require_build_profiles(app, [category])

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
        _require_build_profiles(app, [category])
        return _start_constrained_worker(app, attempt_uuid, category)

    @app.post("/api/build-attempts/worker/start-sequential")
    async def start_sequential_build_worker(request: Request) -> JSONResponse:
        payload = await _json_object(request)
        attempt_ids = _parse_build_attempt_ids(payload)
        BuildOrchestrationService(paths=_project_paths(app)).recover_staging()
        categories = _selected_attempt_categories(app, attempt_ids)
        preflight = _sequential_profile_preflight_response(categories)
        if preflight is not None:
            return preflight

        tasks = app.state.dashboard_tasks
        ok, message = tasks.start_sequential_worker(
            build_attempt_ids=attempt_ids,
        )
        if not ok:
            raise HTTPException(status_code=HTTPStatus.CONFLICT, detail=message)
        _assign_queued_attempt_worker(attempt_ids, worker="dashboard-sequential-01")
        return JSONResponse(
            {
                "ok": True,
                "message": message,
                "build_attempt_ids": [str(item) for item in attempt_ids],
                "queue_length": len(attempt_ids),
            },
            status_code=HTTPStatus.ACCEPTED,
        )

    @app.post("/api/build-attempts/worker/start-sequential-lanes")
    async def start_sequential_lane_pool(request: Request) -> JSONResponse:
        payload = await _json_object(request)
        attempt_ids = _parse_build_attempt_ids(payload)
        lane_count = _parse_lane_count(payload.get("lanes"))

        BuildOrchestrationService(paths=_project_paths(app)).recover_staging()
        categories = _selected_attempt_categories(app, attempt_ids)
        preflight = _sequential_profile_preflight_response(categories)
        if preflight is not None:
            return preflight

        lane_batches = _round_robin_lanes(attempt_ids, lane_count)
        tasks = app.state.dashboard_tasks
        ok, message, pool = tasks.start_sequential_lanes(lanes=lane_batches)
        if not ok:
            raise HTTPException(status_code=HTTPStatus.CONFLICT, detail=message)
        for lane in pool.get("lanes", []):
            lane_attempt_ids = [
                UUID(value)
                for value in lane.get("build_attempt_ids", [])
            ]
            if lane_attempt_ids:
                _assign_queued_attempt_worker(
                    lane_attempt_ids,
                    worker=lane.get("worker", ""),
                )
        return JSONResponse(
            {
                "ok": True,
                "message": message,
                "build_attempt_ids": [str(item) for item in attempt_ids],
                "queue_length": len(attempt_ids),
                "requested_lanes": lane_count,
                "lane_count": len(pool.get("lanes", [])),
                "pool": pool,
            },
            status_code=HTTPStatus.ACCEPTED,
        )

    @app.get("/api/build-attempts/worker/pools")
    def list_sequential_lane_pools() -> JSONResponse:
        tasks = app.state.dashboard_tasks
        if not hasattr(tasks, "lane_pools_state"):
            return JSONResponse({"pools": []})
        return JSONResponse({"pools": tasks.lane_pools_state()})

    @app.post("/api/build-attempts/worker/stop")
    def stop_build_worker() -> JSONResponse:
        tasks = app.state.dashboard_tasks
        if not hasattr(tasks, "stop"):
            raise HTTPException(
                status_code=HTTPStatus.SERVICE_UNAVAILABLE,
                detail="build worker manager is not configured",
            )
        ok, message = tasks.stop()
        if not ok:
            raise HTTPException(status_code=HTTPStatus.CONFLICT, detail=message)
        _sync_finished_dashboard_workers(app)
        state = tasks.state() if hasattr(tasks, "state") else {}
        return JSONResponse(
            {"ok": True, "message": message, "state": state},
            status_code=HTTPStatus.ACCEPTED,
        )

    @app.post("/api/build-attempts/queue/start")
    async def start_build_attempt_queue(request: Request) -> JSONResponse:
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
        if category is not None and category not in SUPPORTED_CATEGORIES:
            raise HTTPException(
                status_code=HTTPStatus.BAD_REQUEST,
                detail=(f"unknown category {category!r}; allowed: {sorted(SUPPORTED_CATEGORIES)}"),
            )
        request_uuid = _parse_optional_uuid(
            payload.get("generation_request_id"),
            "generation_request_id",
        )
        raw_limit = payload.get("limit")
        limit = _parse_limit(str(raw_limit)) if raw_limit is not None else 100

        BuildOrchestrationService(paths=_project_paths(app)).recover_staging()
        attempts = _eligible_queued_attempts(
            app,
            category=category,
            generation_request_id=request_uuid,
            limit=limit,
        )
        if not attempts:
            scope = f" {category}" if category else ""
            raise HTTPException(
                status_code=HTTPStatus.CONFLICT,
                detail=f"no eligible{scope} queued build attempts have matching pending shards",
            )
        categories = [item[1] for item in attempts]
        preflight = _sequential_profile_preflight_response(categories)
        if preflight is not None:
            return preflight

        attempt_ids = [item[0] for item in attempts]
        tasks = app.state.dashboard_tasks
        ok, message = tasks.start_sequential_worker(build_attempt_ids=attempt_ids)
        if not ok:
            raise HTTPException(status_code=HTTPStatus.CONFLICT, detail=message)
        _assign_queued_attempt_worker(attempt_ids, worker="dashboard-sequential-01")
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
        _require_attempt_build_profile(app, attempt_uuid)
        try:
            new_id = BuildOrchestrationService(paths=_project_paths(app)).retry(attempt_uuid)
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
        return JSONResponse(_retry_response_payload(new_id), status_code=HTTPStatus.CREATED)

    @app.post("/api/build-attempts/{attempt_id}/repair")
    def repair_build_attempt(attempt_id: str) -> JSONResponse:
        attempt_uuid = _parse_uuid(attempt_id, "build attempt id")
        _require_attempt_build_profile(app, attempt_uuid)
        progress = getattr(app.state, "progress_store", None)
        if progress is None:
            raise HTTPException(
                status_code=HTTPStatus.CONFLICT,
                detail="progress store is not configured",
            )
        try:
            result = BuildAttemptRepairService(
                paths=_project_paths(app),
                progress=progress,
                session_factory=getattr(app.state, "session_factory", None),
            ).repair(
                attempt_uuid
            )
        except BuildAttemptRepairError as exc:
            raise HTTPException(
                status_code=HTTPStatus.CONFLICT,
                detail=str(exc),
            ) from exc
        return JSONResponse(
            {
                "build_attempt_id": str(result.attempt_id),
                "repair_id": result.repair_id,
                "status": result.status,
                "verification_status": result.verification_status,
                "log_path": result.log_path,
                "events_path": result.events_path,
                "failure_summary": result.failure_summary,
            },
            status_code=HTTPStatus.OK,
        )

    @app.post("/api/build-attempts/{attempt_id}/clean-rebuild")
    async def clean_rebuild_attempt(attempt_id: str, request: Request) -> JSONResponse:
        attempt_uuid = _parse_uuid(attempt_id, "build attempt id")
        try:
            payload = await request.json()
        except ValueError as exc:
            raise HTTPException(
                status_code=HTTPStatus.BAD_REQUEST,
                detail="request body must be a JSON object",
            ) from exc
        if not isinstance(payload, dict):
            raise HTTPException(
                status_code=HTTPStatus.BAD_REQUEST,
                detail="request body must be a JSON object",
            )
        _require_attempt_build_profile(app, attempt_uuid)
        try:
            new_id = BuildOrchestrationService(paths=_project_paths(app)).clean_rebuild(
                attempt_uuid,
                idempotency_key=payload.get("idempotency_key", ""),
                confirmed=payload.get("confirmed") is True,
            )
        except BuildOrchestrationError as exc:
            detail: Any = str(exc)
            if exc.code is not None:
                detail = {"code": exc.code, "message": str(exc)}
            raise HTTPException(
                status_code=HTTPStatus.CONFLICT,
                detail=detail,
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
        from persistence.session import SessionFactory as _SF
        from persistence.session import transaction as _txn

        session_factory = getattr(app.state, "session_factory", None) or _SF()

        with _txn(factory=session_factory) as session:
            row = session.get(build_model.BuildAttempt, attempt_uuid)
            if row is None:
                raise HTTPException(
                    status_code=HTTPStatus.NOT_FOUND,
                    detail=f"build attempt {attempt_uuid} not found",
                )
            if row.status not in {"lost", "running"}:
                raise HTTPException(
                    status_code=HTTPStatus.CONFLICT,
                    detail=(
                        "only lost or stale running attempts can be restored; "
                        f"current status: {row.status}"
                    ),
                )
            if row.status == "running":
                latest = session.get(exec_model.Execution, row.latest_execution_id)
                current = session.get(exec_model.Execution, row.current_execution_id)
                if latest is None or current is None or latest.id != current.id:
                    raise HTTPException(
                        status_code=HTTPStatus.CONFLICT,
                        detail="running attempt is not eligible for restoration",
                    )
                if latest.status not in {"claimed", "running"}:
                    raise HTTPException(
                        status_code=HTTPStatus.CONFLICT,
                        detail="running attempt no longer has an active execution",
                    )
                if latest.lease_expires_at is not None and latest.lease_expires_at >= datetime.now(timezone.utc):
                    raise HTTPException(
                        status_code=HTTPStatus.CONFLICT,
                        detail="running attempt lease has not expired yet",
                    )
                if latest.worker_id is None:
                    raise HTTPException(
                        status_code=HTTPStatus.CONFLICT,
                        detail="running attempt has no owning worker",
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
                exact_running = paths.shards / "running" / shard_basename
                if exact_running.is_file():
                    located = exact_running
                else:
                    # 兼容 worker 后缀形式：basename.worker.json
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
            row.worker = None
            row.started_at = None
            row.resulting_challenge_dir = None
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
            return JSONResponse(
                _attempt_dict(attempt, project_root=_project_paths(app).root)
            )

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


def _sync_finished_dashboard_workers(app: FastAPI) -> None:
    tasks = getattr(app.state, "dashboard_tasks", None)
    if tasks is None or not hasattr(tasks, "finished_build_workers"):
        return
    records = tasks.finished_build_workers()
    if not records:
        return
    worker_errors: dict[str, str] = {}
    attempt_errors: dict[UUID, str] = {}
    for record in records:
        returncode = record.get("returncode")
        kind = record.get("kind") or "worker"
        message = f"dashboard {kind} exited before finalizing attempt"
        if returncode is not None:
            message = f"{message} (returncode {returncode})"
        for worker_id in record.get("worker_ids") or []:
            if isinstance(worker_id, str) and worker_id:
                worker_errors[worker_id] = message
        for raw_id in record.get("build_attempt_ids") or []:
            try:
                attempt_errors[UUID(str(raw_id))] = message
            except (TypeError, ValueError):
                continue
    if not worker_errors and not attempt_errors:
        return

    from persistence.session import transaction

    with transaction() as session:
        filters = [build_model.BuildAttempt.status == "running"]
        selectors = []
        if worker_errors and not attempt_errors:
            selectors.append(build_model.BuildAttempt.worker.in_(worker_errors))
        if attempt_errors:
            selectors.append(build_model.BuildAttempt.id.in_(attempt_errors))
        if not selectors:
            return
        rows = session.scalars(
            sa.select(build_model.BuildAttempt)
            .where(*filters, sa.or_(*selectors))
            .with_for_update()
        ).all()
        now = datetime.now(timezone.utc)
        for row in rows:
            error = attempt_errors.get(row.id)
            if error is None and row.worker is not None:
                error = worker_errors.get(row.worker)
            error = error or "dashboard worker exited before finalizing attempt"
            _mark_dashboard_attempt_lost(session, row, now=now, error=error)


def _mark_dashboard_attempt_lost(
    session: Any,
    row: build_model.BuildAttempt,
    *,
    now: datetime,
    error: str,
) -> None:
    if row.latest_execution_id is not None:
        latest = session.get(exec_model.Execution, row.latest_execution_id)
        if latest is not None and latest.status in {"claimed", "running"}:
            latest.status = "lost"
            latest.exit_class = latest.exit_class or "dashboard_worker_exited"
            latest.error = latest.error or error
            latest.finished_at = now
        if row.current_execution_id == row.latest_execution_id:
            row.current_execution_id = None
    row.status = "lost"
    row.finished_at = now
    row.error = error
    task = session.get(task_model.DesignTask, row.design_task_id)
    if task is not None and task.status == "building":
        task.status = "build_failed"
        task.updated_at = now


def _normalize_artifact_status(
    session: Any,
    paths: Any,
    attempt: BuildAttempt,
    *,
    category: str,
    challenge_id: str,
) -> BuildAttempt:
    """Backfill legacy successful attempts whose artifact state was never persisted."""
    if attempt.status != "succeeded" or attempt.artifact_status != "unknown":
        return attempt
    directory = _locate_resulting_challenge_dir(
        paths,
        attempt,
        category=category,
        challenge_id=challenge_id,
    )
    status = "present" if directory is not None else "missing"
    relative = directory.relative_to(paths.root).as_posix() if directory is not None else None
    row = session.get(build_model.BuildAttempt, attempt.id)
    if row is not None and row.status == "succeeded" and row.artifact_status == "unknown":
        row.artifact_status = status
        row.resulting_challenge_dir = relative
        session.flush()
    return replace(
        attempt,
        artifact_status=status,
        resulting_challenge_dir=relative,
    )


def _locate_resulting_challenge_dir(
    paths: Any,
    attempt: BuildAttempt,
    *,
    category: str,
    challenge_id: str,
) -> Path | None:
    if attempt.resulting_challenge_dir:
        existing = paths.root / attempt.resulting_challenge_dir
        if existing.is_dir():
            return existing

    for root in (
        paths.challenges / category,
        paths.executions / str(attempt.id) / "current" / "output" / "challenges" / category,
    ):
        candidate = _find_claimed_challenge_dir(root, challenge_id)
        if candidate is not None:
            return candidate
    return None


def _find_claimed_challenge_dir(root: Path, challenge_id: str) -> Path | None:
    if not root.is_dir():
        return None
    exact = root / challenge_id
    if exact.is_dir():
        return exact
    matches = sorted(
        entry
        for entry in root.glob(f"{challenge_id}-*")
        if entry.is_dir() and not entry.is_symlink()
    )
    return matches[0] if len(matches) == 1 else None


async def _json_object(request: Request) -> dict[str, Any]:
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
    return payload


def _parse_build_attempt_ids(payload: dict[str, Any]) -> list[UUID]:
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
    return attempt_ids


def _parse_lane_count(raw: Any) -> int:
    if raw is None:
        return DEFAULT_SEQUENTIAL_LANES
    if isinstance(raw, bool):
        raise HTTPException(
            status_code=HTTPStatus.BAD_REQUEST,
            detail="lanes must be a positive integer",
        )
    try:
        lanes = int(raw)
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=HTTPStatus.BAD_REQUEST,
            detail="lanes must be a positive integer",
        ) from exc
    if lanes <= 0:
        raise HTTPException(
            status_code=HTTPStatus.BAD_REQUEST,
            detail="lanes must be a positive integer",
        )
    if lanes > BUILD_ATTEMPTS_MAX_SEQUENTIAL_LANES:
        raise HTTPException(
            status_code=HTTPStatus.BAD_REQUEST,
            detail=f"lanes must be <= {BUILD_ATTEMPTS_MAX_SEQUENTIAL_LANES}",
        )
    return lanes


def _selected_attempt_categories(app: FastAPI, attempt_ids: list[UUID]) -> list[str]:
    categories = []
    for attempt_id in attempt_ids:
        selected = _exact_eligible_attempt(app, attempt_id)
        if selected is None:
            raise HTTPException(
                status_code=HTTPStatus.NOT_FOUND,
                detail=f"build attempt {attempt_id} not found",
            )
        status, category, matches_pending = selected
        if status != "queued" or not matches_pending:
            raise HTTPException(
                status_code=HTTPStatus.CONFLICT,
                detail=(f"build attempt {attempt_id} is not an eligible queued task"),
            )
        categories.append(category)
    return categories


def _round_robin_lanes(attempt_ids: list[UUID], lane_count: int) -> list[list[UUID]]:
    lanes: list[list[UUID]] = [
        []
        for _index in range(min(lane_count, len(attempt_ids)))
    ]
    for index, attempt_id in enumerate(attempt_ids):
        lanes[index % len(lanes)].append(attempt_id)
    return lanes


def _require_task_build_profiles(app: FastAPI, task_ids: list[UUID]) -> None:
    from persistence.session import transaction

    with transaction() as session:
        categories = session.scalars(
            sa.select(task_model.DesignTask.category).where(task_model.DesignTask.id.in_(task_ids))
        ).all()
    _require_build_profiles(app, categories)


def _require_attempt_build_profile(app: FastAPI, attempt_id: UUID) -> None:
    from persistence.session import transaction

    with transaction() as session:
        category = session.scalar(
            sa.select(task_model.DesignTask.category)
            .join(
                build_model.BuildAttempt,
                build_model.BuildAttempt.design_task_id == task_model.DesignTask.id,
            )
            .where(build_model.BuildAttempt.id == attempt_id)
        )
    if category is not None:
        _require_build_profiles(app, [category])


def _require_build_profiles(app: FastAPI, categories) -> None:
    readiness = getattr(app.state, "build_profile_readiness", {"ready": True})
    if readiness.get("ready"):
        return
    unavailable = unavailable_build_profiles(readiness, categories)
    if not unavailable:
        return
    profiles = ", ".join(item["profile"] for item in unavailable)
    commands = "; ".join(item["create_command"] for item in unavailable)
    messages = "; ".join(item["message"] for item in unavailable if item.get("message"))
    detail = f"构建环境未就绪：Hermes Profile 或安全策略不可用：{profiles}；请先运行：{commands}"
    if messages:
        detail += f"；{messages}"
    raise HTTPException(
        status_code=HTTPStatus.SERVICE_UNAVAILABLE,
        detail=detail,
    )


def _sequential_profile_preflight_response(categories) -> JSONResponse | None:
    errors = []
    for category in sorted({str(item) for item in categories if item}):
        profile = f"cf-{category}"
        ok, error_code, message = hermes_profile_health(profile)
        if not ok:
            errors.append(
                {
                    "profile": profile,
                    "error_code": error_code,
                    "message": message,
                }
            )
    if not errors:
        return None
    return JSONResponse(
        {
            "ok": False,
            "error_code": errors[0]["error_code"],
            "message": "；".join(error["message"] for error in errors),
            "errors": errors,
        },
        status_code=HTTPStatus.CONFLICT,
    )


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
    attempts = _eligible_queued_attempts(app, category=category, limit=1)
    return attempts[0] if attempts else None


def _eligible_queued_attempts(
    app: FastAPI,
    *,
    category: str | None = None,
    generation_request_id: UUID | None = None,
    limit: int,
) -> list[tuple[UUID, str]]:
    from persistence.session import transaction

    paths = _project_paths(app)
    query = (
        sa.select(build_model.BuildAttempt, task_model.DesignTask.category)
        .join(
            task_model.DesignTask,
            task_model.DesignTask.id == build_model.BuildAttempt.design_task_id,
        )
        .where(build_model.BuildAttempt.status == "queued")
        .order_by(
            build_model.BuildAttempt.created_at.asc(),
            build_model.BuildAttempt.id.asc(),
        )
    )
    if category is not None:
        query = query.where(task_model.DesignTask.category == category)
    if generation_request_id is not None:
        query = query.where(task_model.DesignTask.generation_request_id == generation_request_id)

    selected: list[tuple[UUID, str]] = []
    with transaction() as session:
        rows = session.execute(query).all()
        for attempt, row_category in rows:
            if not _pending_payload_matches(
                paths,
                attempt_id=attempt.id,
                design_task_id=attempt.design_task_id,
                shard_basename=attempt.shard_basename,
                category=row_category,
            ):
                continue
            selected.append((attempt.id, row_category))
            if len(selected) >= limit:
                break
    return selected


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
    if not _attributed_shard_basename_matches(shard_basename, attempt_id):
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
        and all(isinstance(challenge, dict) and challenge.get("category") == category for challenge in challenges)
    )


def _start_constrained_worker(
    app: FastAPI,
    attempt_id: UUID,
    category: str,
) -> JSONResponse:
    effective_timeout, timeout_source = _effective_timeout_for_attempt(_project_paths(app), attempt_id)
    tasks = app.state.dashboard_tasks
    from persistence.session import transaction

    with transaction() as session:
        existing = session.scalar(
            sa.select(sa.func.count())
            .select_from(build_model.BuildAttempt)
            .where(
                build_model.BuildAttempt.status == "running",
                build_model.BuildAttempt.worker == "dashboard-01",
                build_model.BuildAttempt.id != attempt_id,
            )
        )
        if existing:
            raise HTTPException(
                status_code=HTTPStatus.CONFLICT,
                detail="dashboard worker dashboard-01 is already running another build attempt",
            )
    ok, message = tasks.start_worker(
        category=category,
        build_attempt_id=attempt_id,
    )
    if not ok:
        raise HTTPException(status_code=HTTPStatus.CONFLICT, detail=message)

    with transaction() as session:
        attempt = session.get(build_model.BuildAttempt, attempt_id)
        if (
            attempt is not None
            and attempt.status == "queued"
            and attempt.latest_execution_id is None
        ):
            BuildAttemptsRepository(session).update_to_running(
                attempt_id,
                worker="dashboard-01",
            )
        elif attempt is not None and attempt.status == "queued":
            attempt.worker = "dashboard-01"
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


def _assign_queued_attempt_worker(attempt_ids: list[UUID], *, worker: str) -> None:
    from persistence.session import transaction

    with transaction() as session:
        rows = session.scalars(
            sa.select(build_model.BuildAttempt)
            .where(build_model.BuildAttempt.id.in_(attempt_ids))
            .with_for_update()
        ).all()
        for row in rows:
            if row.status == "queued":
                row.worker = worker


def _effective_timeout_for_attempt(paths, attempt_id: UUID) -> tuple[int, str]:
    env_raw = os.environ.get("HERMES_TIMEOUT")
    if env_raw:
        try:
            value = int(env_raw)
        except ValueError:
            value = 0
        if value > 0:
            return value, "env"
    payload = {}
    for shard in _candidate_pending_shards(paths, attempt_id):
        payload = read_json(shard, {})
        break
    return shard_timeout_policy(payload), "shard_policy"


def _candidate_pending_shards(paths, attempt_id: UUID) -> list[Path]:
    pending = paths.shards / "pending"
    exact = pending / f"{attempt_id}.json"
    candidates = [*sorted(pending.glob(f"{attempt_id}.iter-*.json"), reverse=True), exact]
    return [path for path in candidates if path.is_file() and not path.is_symlink()]


def _attributed_shard_basename_matches(shard_basename: str, attempt_id: UUID) -> bool:
    if shard_basename == f"{attempt_id}.json":
        return True
    prefix = f"{attempt_id}.iter-"
    suffix = ".json"
    if not (shard_basename.startswith(prefix) and shard_basename.endswith(suffix)):
        return False
    iteration = shard_basename[len(prefix) : -len(suffix)]
    return len(iteration) == 3 and iteration.isdigit() and int(iteration) > 0


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
    project_root: Path,
    failure_summary: str | None = None,
) -> dict[str, Any]:
    artifact_metadata = _attempt_artifact_metadata(attempt, project_root=project_root)
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
    if artifact_metadata:
        payload["solve_status"] = artifact_metadata.get("solve_status")
        payload["validation_status"] = artifact_metadata.get("validation_status")
    if failure_summary:
        payload["failure_summary"] = failure_summary
    return payload


def _attempt_artifact_metadata(
    attempt: BuildAttempt,
    *,
    project_root: Path,
) -> dict[str, Any] | None:
    if not attempt.resulting_challenge_dir:
        return None
    metadata = read_json(
        project_root / attempt.resulting_challenge_dir / "metadata.json",
        None,
    )
    return metadata if isinstance(metadata, dict) else None


def _execution_dict(execution) -> dict[str, Any]:
    return {
        "id": str(execution.id),
        "build_attempt_id": str(execution.build_attempt_id),
        "parent_execution_id": (
            str(execution.parent_execution_id)
            if execution.parent_execution_id is not None
            else None
        ),
        "iteration_no": execution.iteration_no,
        "execution_kind": execution.execution_kind,
        "execution_mode": execution.execution_mode,
        "worker_id": execution.worker_id,
        "status": execution.status,
        "exit_class": execution.exit_class,
        "error": execution.error,
        "created_at": _isofmt(execution.created_at),
        "started_at": _isofmt(execution.started_at),
        "finished_at": _isofmt(execution.finished_at),
        "heartbeat_at": _isofmt(execution.heartbeat_at),
        "lease_expires_at": _isofmt(execution.lease_expires_at),
    }


def _retry_response_payload(attempt_id: UUID) -> dict[str, Any]:
    from persistence.session import transaction

    with transaction() as session:
        attempt = session.get(build_model.BuildAttempt, attempt_id)
        if attempt is None:
            raise HTTPException(
                status_code=HTTPStatus.NOT_FOUND,
                detail="build attempt not found after retry",
            )
        latest = ExecutionsRepository(session).latest_for_attempt(attempt_id)
        payload = {
            "build_attempt_id": str(attempt.id),
            "status": attempt.status,
            "shard_basename": attempt.shard_basename,
        }
        if latest is not None:
            payload["execution_id"] = str(latest.id)
            payload["iteration_no"] = latest.iteration_no
            payload["execution_status"] = latest.status
            payload["execution_kind"] = latest.execution_kind
        return payload


def _list_item_dict(
    item: BuildAttemptListItem,
    *,
    project_root: Path,
    summaries: dict[str, str] | None = None,
) -> dict[str, Any]:
    row = _attempt_dict(
        item,
        project_root=project_root,
        failure_summary=(summaries or {}).get(item.shard_basename) or _derive_failure_summary([], item.error),
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
    return {shard: summary for shard, rows in grouped.items() if (summary := _derive_failure_summary(rows, None))}


def _repair_runs(paths, attempt_id: UUID) -> list[dict[str, Any]]:
    root = paths.executions / str(attempt_id) / "repairs"
    if root.is_symlink() or not root.is_dir():
        return []
    runs: list[dict[str, Any]] = []
    for directory in sorted(root.iterdir(), key=lambda item: item.name, reverse=True):
        if directory.is_symlink() or not directory.is_dir():
            continue
        events_path = directory / "repair-events.jsonl"
        events = _read_repair_events(events_path)
        last = events[-1] if events else {}
        runs.append(
            {
                "repair_id": directory.name,
                "status": last.get("status") or "unknown",
                "phase": last.get("phase") or "unknown",
                "message": last.get("message") or "",
                "created_at": events[0].get("created_at") if events else None,
                "updated_at": last.get("created_at"),
                "log_path": str(directory / "hermes.log"),
                "events_path": str(events_path),
                "events": events,
            }
        )
    return runs


def _read_repair_events(path: Path) -> list[dict[str, Any]]:
    if path.is_symlink() or not path.is_file():
        return []
    events: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    for line in lines:
        try:
            parsed = json.loads(line)
        except ValueError:
            continue
        if isinstance(parsed, dict):
            events.append(parsed)
    return events


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
    "no compiled ELF artifact found in attachments/": "未找到编译后的 ELF 产物（请放到 attachments/ 下）",
    "shard execution failed": "Hermes 执行阶段失败",
    "Workspace preflight failed": "执行 workspace 预检失败",
    "Terminal workspace visibility failed": "Docker 终端 workspace 挂载不可见",
    "Workspace materialization failed": "执行 workspace 物化失败",
    "Workspace shim materialization failed": "进度蜘蛛生成失败",
    "attributed shard disappeared from all queue states": (
        "shard 文件从队列中消失（可能是 reconciler 误判，参考 /restore 接口）"
    ),
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
        rest = stripped[len("validator: status=") :]
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
    return beijing_isoformat(value)


def _project_paths(app: FastAPI):
    from core.paths import ProjectPaths

    return getattr(app.state, "project_paths", None) or ProjectPaths.discover()


# 构建日志分散在 work/executions/<attempt_id>/logs/hermes.log 与
# .../repairs/<repair_id>/hermes.log。这里用合成展示名把它们聚合到日志页：
#   主日志：build-<attempt_id>.log
#   修复日志：build-<attempt_id>--repair-<repair_id>.log
_BUILD_REPAIR_SEP = "--repair-"


def _collect_build_logs(paths) -> list[dict[str, Any]]:
    executions = paths.executions
    if not executions.exists():
        return []
    rows: list[dict[str, Any]] = []
    for attempt_dir in executions.iterdir():
        if attempt_dir.is_symlink() or not attempt_dir.is_dir():
            continue
        attempt_id = attempt_dir.name
        main_log = attempt_dir / "logs" / "hermes.log"
        if main_log.is_file() and not main_log.is_symlink():
            rows.append(_build_log_row(f"build-{attempt_id}.log", main_log))
        repairs = attempt_dir / "repairs"
        if repairs.is_dir() and not repairs.is_symlink():
            for repair_dir in repairs.iterdir():
                if repair_dir.is_symlink() or not repair_dir.is_dir():
                    continue
                repair_log = repair_dir / "hermes.log"
                if repair_log.is_file() and not repair_log.is_symlink():
                    name = (
                        f"build-{attempt_id}{_BUILD_REPAIR_SEP}{repair_dir.name}.log"
                    )
                    rows.append(_build_log_row(name, repair_log))
    rows.sort(key=lambda row: row["updated_at"], reverse=True)
    return rows


def _build_log_row(name: str, path: Path) -> dict[str, Any]:
    stat = path.stat()
    return {
        "name": name,
        "size": stat.st_size,
        "updated_at": beijing_isoformat(
            datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
        ),
    }


def _build_log_path(paths, name: str) -> Path | None:
    """Resolve a synthetic build-log name back to its real path, or None."""
    if not name.startswith("build-") or not name.endswith(".log"):
        return None
    body = name[len("build-") : -len(".log")]
    if _BUILD_REPAIR_SEP in body:
        attempt_id, repair_id = body.split(_BUILD_REPAIR_SEP, 1)
        if not attempt_id or not repair_id:
            return None
        return paths.executions / attempt_id / "repairs" / repair_id / "hermes.log"
    if not body:
        return None
    return paths.executions / body / "logs" / "hermes.log"
