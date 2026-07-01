"""Dedicated read endpoints for design task resources."""

from __future__ import annotations

from http import HTTPStatus
from typing import Any
from uuid import UUID

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse

from core.clock import beijing_isoformat
from domain import challenge_designs as challenge_dto
from domain import design_tasks as design_dto
from domain.design.difficulty_review import DesignDifficultyReview


def register_design_task_read_endpoints(app: FastAPI) -> None:
    @app.get("/api/design-tasks")
    def list_design_tasks(
        generation_request_id: str | None = Query(default=None),
        status: str | None = Query(default=None),
        category: str | None = Query(default=None),
        limit: int = Query(default=100, ge=1, le=500),
    ) -> JSONResponse:
        from persistence.repositories import (
            DesignDifficultyReviewRepository,
            DesignTaskRepository,
        )
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
            review_repo = DesignDifficultyReviewRepository(session)
            summaries = {
                task.id: review_repo.summarize_for_design_task(task.id)
                for task in tasks
            }
        return JSONResponse(
            [
                design_task_dict(
                    task,
                    difficulty_review_summary=summaries.get(task.id),
                )
                for task in tasks
            ]
        )

    @app.get("/api/design-tasks/collapse")
    def design_batch_collapse(
        generation_request_id: str = Query(...),
    ) -> JSONResponse:
        """Report design-collapse risk across one request's designed tasks."""
        from domain.design.collapse import compute_batch_collapse
        from persistence.repositories import (
            ChallengeDesignRepository,
            DesignTaskRepository,
        )
        from persistence.session import transaction

        try:
            request_uuid = UUID(generation_request_id)
        except ValueError as exc:
            raise HTTPException(
                status_code=HTTPStatus.BAD_REQUEST,
                detail="generation_request_id must be a uuid",
            ) from exc

        designed_statuses = {"designed", "building", "built"}
        challenges: list[dict[str, Any]] = []
        with transaction() as session:
            task_repo = DesignTaskRepository(session)
            design_repo = ChallengeDesignRepository(session)
            for task in task_repo.list_design_tasks(request_uuid):
                if task.status not in designed_statuses:
                    continue
                design = design_repo.latest_design(task.id)
                if design is None:
                    continue
                payload_challenges = (design.payload or {}).get("challenges") or []
                challenge = dict(payload_challenges[0]) if payload_challenges else {}
                # core_mechanism lives on the design task, not the payload —
                # merge it so the fingerprint/collapse axes can see it.
                challenge.setdefault("id", task.challenge_id)
                challenge.setdefault("category", task.category)
                challenge.setdefault("difficulty", task.difficulty)
                challenge["diversity_flags"] = task.diversity_flags or {}
                challenges.append(challenge)

        report = compute_batch_collapse(challenges)
        report["generation_request_id"] = str(request_uuid)
        return JSONResponse(report)

    @app.get("/api/design-tasks/{task_id}")
    def get_design_task(task_id: str) -> JSONResponse:
        from persistence.repositories import (
            DesignDifficultyReviewRepository,
            DesignTaskRepository,
        )
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
            review_summary = (
                DesignDifficultyReviewRepository(session).summarize_for_design_task(task_uuid)
                if result is not None
                else None
            )
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
                difficulty_review_summary=review_summary,
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
    return beijing_isoformat(value)


def design_task_dict(
    task: design_dto.DesignTask,
    *,
    attempts: list[challenge_dto.DesignAttempt] | None = None,
    latest_design: challenge_dto.ChallengeDesign | None = None,
    difficulty_review_summary: dict[str, object] | None = None,
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
        "diversity_flags": dict(task.diversity_flags) if task.diversity_flags else None,
        "plan_reviewed_at": isofmt(task.plan_reviewed_at),
        "status": task.status,
        "created_at": isofmt(task.created_at),
        "updated_at": isofmt(task.updated_at),
    }
    if attempts is not None:
        row["attempts"] = [attempt_summary_dict(attempt) for attempt in attempts]
    if latest_design is not None or attempts is not None:
        row["latest_design"] = challenge_design_dict(latest_design)
    if difficulty_review_summary is not None:
        row["difficulty_review_summary"] = difficulty_review_summary_dict(
            difficulty_review_summary
        )
    return row


def difficulty_review_summary_dict(summary: dict[str, object]) -> dict[str, Any]:
    latest = summary.get("latest")
    return {
        "total": int(summary.get("total") or 0),
        "failed": int(summary.get("failed") or 0),
        "latest": difficulty_review_dict(latest)
        if isinstance(latest, DesignDifficultyReview)
        else None,
    }


def difficulty_review_dict(review: DesignDifficultyReview) -> dict[str, Any]:
    return {
        "id": str(review.id),
        "design_task_id": str(review.design_task_id),
        "challenge_design_id": str(review.challenge_design_id),
        "passed": review.passed,
        "claimed_difficulty": review.claimed_difficulty,
        "actual_difficulty": review.actual_difficulty,
        "confidence": review.confidence,
        "reasons": list(review.reasons),
        "detected_risks": list(review.detected_risks),
        "required_revision": list(review.required_revision),
        "reviewer": review.reviewer,
        "created_at": isofmt(review.created_at),
    }


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
