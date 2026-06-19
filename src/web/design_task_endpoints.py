"""Dedicated read endpoints for design task resources."""

from __future__ import annotations

from http import HTTPStatus
from typing import Any
from uuid import UUID

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse

from domain import challenge_designs as challenge_dto
from domain import design_tasks as design_dto


def register_design_task_read_endpoints(app: FastAPI) -> None:
    @app.get("/api/design-tasks")
    def list_design_tasks(
        generation_request_id: str | None = Query(default=None),
        status: str | None = Query(default=None),
        category: str | None = Query(default=None),
        limit: int = Query(default=100, ge=1, le=500),
    ) -> JSONResponse:
        from persistence.repositories import DesignTaskRepository
        from persistence.session import transaction

        if status is not None and status not in design_dto.DesignTaskStatus:
            raise HTTPException(
                status_code=HTTPStatus.BAD_REQUEST,
                detail=(
                    f"unknown status {status!r}; "
                    f"allowed: {list(design_dto.DesignTaskStatus)}"
                ),
            )

        request_uuid: UUID | None = None
        if generation_request_id is not None:
            try:
                request_uuid = UUID(generation_request_id)
            except ValueError as exc:
                raise HTTPException(
                    status_code=HTTPStatus.BAD_REQUEST,
                    detail="generation_request_id must be a uuid",
                ) from exc

        with transaction() as session:
            tasks = DesignTaskRepository(session).list_tasks(
                generation_request_id=request_uuid,
                status=status,
                category=category,
                limit=limit,
            )
        return JSONResponse([design_task_dict(task) for task in tasks])

    @app.get("/api/design-tasks/{task_id}")
    def get_design_task(task_id: str) -> JSONResponse:
        from persistence.repositories import DesignTaskRepository
        from persistence.session import transaction

        try:
            task_uuid = UUID(task_id)
        except ValueError as exc:
            raise HTTPException(
                status_code=HTTPStatus.NOT_FOUND,
                detail="design task not found",
            ) from exc

        with transaction() as session:
            result = DesignTaskRepository(session).get_with_history(task_uuid)
        if result is None:
            raise HTTPException(
                status_code=HTTPStatus.NOT_FOUND,
                detail="design task not found",
            )
        task, attempts, latest_design = result
        return JSONResponse(
            design_task_dict(
                task,
                attempts=attempts,
                latest_design=latest_design,
            )
        )

    @app.delete("/api/design-tasks/{task_id}")
    def delete_design_task(
        task_id: str,
        delete_artifacts: bool = Query(default=False),
    ) -> JSONResponse:
        from services import (
            ResourceDeletionConflictError,
            ResourceDeletionNotFoundError,
        )
        from web.resource_deletion import deletion_service

        try:
            task_uuid = UUID(task_id)
        except ValueError as exc:
            raise HTTPException(
                status_code=HTTPStatus.NOT_FOUND,
                detail="design task not found",
            ) from exc
        try:
            result = deletion_service(app).delete_design_task(
                task_uuid,
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


def isofmt(value) -> str | None:
    return value.isoformat() if value is not None else None


def design_task_dict(
    task: design_dto.DesignTask,
    *,
    attempts: list[challenge_dto.DesignAttempt] | None = None,
    latest_design: challenge_dto.ChallengeDesign | None = None,
) -> dict[str, Any]:
    row = {
        "id": str(task.id),
        "generation_request_id": str(task.generation_request_id),
        "research_run_id": str(task.research_run_id),
        "task_no": task.task_no,
        "challenge_id": task.challenge_id,
        "title": task.title,
        "category": task.category,
        "difficulty": task.difficulty,
        "primary_technique": task.primary_technique,
        "learning_objective": task.learning_objective,
        "points": task.points,
        "port": task.port,
        "scenario": task.scenario,
        "constraints": dict(task.constraints),
        "evidence_summary": task.evidence_summary,
        "finding_ids": [str(fid) for fid in task.finding_ids],
        "status": task.status,
        "created_at": isofmt(task.created_at),
        "updated_at": isofmt(task.updated_at),
    }
    if attempts is not None:
        row["attempts"] = [attempt_summary_dict(attempt) for attempt in attempts]
    if latest_design is not None or attempts is not None:
        row["latest_design"] = challenge_design_dict(latest_design)
    return row


def attempt_summary_dict(attempt: challenge_dto.DesignAttempt) -> dict[str, Any]:
    return {
        "id": str(attempt.id),
        "attempt": attempt.attempt,
        "status": attempt.status,
        "started_at": isofmt(attempt.started_at),
        "finished_at": isofmt(attempt.finished_at),
        "last_error": attempt.last_error,
        "prompt_artifact_url": (
            f"/api/design-attempts/{attempt.id}/artifact?kind=prompt"
            if attempt.prompt_path
            else None
        ),
        "log_artifact_url": (
            f"/api/design-attempts/{attempt.id}/artifact?kind=log"
            if attempt.hermes_log_path
            else None
        ),
    }


def challenge_design_dict(
    design: challenge_dto.ChallengeDesign | None,
) -> dict[str, Any] | None:
    if design is None:
        return None
    return {
        "id": str(design.id),
        "design_task_id": str(design.design_task_id),
        "design_attempt_id": str(design.design_attempt_id),
        "payload": dict(design.payload),
        "summary": design.summary,
        "flag_format": design.flag_format,
        "validation_notes": design.validation_notes,
        "quality_gate_passed": design.quality_gate_passed,
        "status": design.status,
        "created_at": isofmt(design.created_at),
        "updated_at": isofmt(design.updated_at),
    }


def _project_paths(app: FastAPI):
    from core.paths import ProjectPaths

    return getattr(app.state, "project_paths", None) or ProjectPaths.discover()
