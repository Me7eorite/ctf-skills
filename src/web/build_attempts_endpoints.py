"""HTTP endpoints for build-attempt orchestration and inspection."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from http import HTTPStatus
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError

from core.queue import SUPPORTED_CATEGORIES
from domain.build_attempts import BuildAttempt, BuildAttemptListItem, BuildAttemptStatus
from persistence.models.progress import ProgressEvent
from persistence.repositories import (
    BuildAttemptsRepository,
)
from services import BuildOrchestrationError, BuildOrchestrationService

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
        headers = {}
        if capped != requested_limit:
            headers["X-Limit-Capped"] = str(capped)
        return JSONResponse([_list_item_dict(row) for row in rows], headers=headers)

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

        body = _attempt_dict(attempt)
        body["sibling_attempts"] = [_attempt_dict(row) for row in siblings]
        body["progress_events"] = [_progress_event_dict(row) for row in events]
        return JSONResponse(body)

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


def _attempt_dict(attempt: BuildAttempt) -> dict[str, Any]:
    return {
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


def _list_item_dict(item: BuildAttemptListItem) -> dict[str, Any]:
    row = _attempt_dict(item)
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

