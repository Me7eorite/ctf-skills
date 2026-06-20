"""HTTP endpoints for the research-planning data.

Section 10 of add-research-planning-core provides the seven read
endpoints; ``POST /api/research/requests`` is a pragmatic addition so
operators can submit through the dashboard form without leaving the
browser (the CLI path remains available and is unchanged). Each
handler opens its own short persistence transaction.
"""

from __future__ import annotations

from datetime import datetime
from http import HTTPStatus
from pathlib import Path
from typing import Any
from uuid import UUID

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response

from core.paths import ProjectPaths
from domain import challenge_designs as challenge_dto
from domain import design_tasks as design_dto
from domain import research as dto
from domain.design_task_validators import DesignTaskValidationError
from domain.research import GenerationRequestStatus, ResearchRunStatus
from domain.research_failure_taxonomy import classify_last_error
from domain.research_validators import ResearchValidationError, validate_runtime_constraints
from services.research_log_utils import (
    SafeResearchLogError,
    has_ordered_stdout_markers,
    read_safe_research_log,
)


def register_research_endpoints(app: FastAPI, worker_manager=None) -> None:
    """Attach the Section 10 read endpoints + the submit / worker endpoints to `app`.

    MUST be called BEFORE the static catch-all route in `create_app`
    so the `/api/...` paths win over the wildcard. `worker_manager` is
    optional — when omitted, the worker control endpoints respond 503
    so test fixtures that don't need a spawnable subprocess can still
    mount the read-only endpoints in isolation.
    """
    _register_categories(app)
    _register_requests_list(app)
    _register_request_submit(app)
    _register_request_detail(app)
    _register_runs_list(app)
    _register_queue_stats(app)
    _register_bindings_list(app)
    _register_binding_detail(app)
    _register_log_endpoints(app, worker_manager)
    _register_worker_endpoints(app, worker_manager)
    _register_design_task_endpoints(app)


# ---------------------------------------------------------------------------
# POST /api/research/worker/start
# POST /api/research/worker/stop
# GET  /api/research/worker/status
# ---------------------------------------------------------------------------


def _register_worker_endpoints(app: FastAPI, manager) -> None:
    @app.get("/api/research/worker/status")
    def worker_status() -> JSONResponse:
        if manager is None:
            return JSONResponse({"running": False, "available": False})
        snapshot = manager.state()
        snapshot["available"] = True
        return JSONResponse(snapshot)

    @app.post("/api/research/worker/start")
    async def worker_start(request: Request) -> JSONResponse:
        return await _start_worker_from_request(request, manager)

    @app.post("/api/research/requests/{request_id}/worker/start")
    async def request_worker_start(request_id: str, request: Request) -> JSONResponse:
        try:
            UUID(request_id)
        except ValueError as exc:
            raise HTTPException(
                status_code=HTTPStatus.NOT_FOUND,
                detail="request not found",
            ) from exc
        return await _start_worker_from_request(
            request,
            manager,
            generation_request_id=request_id,
        )

    async def _start_worker_from_request(
        request: Request,
        manager,
        *,
        generation_request_id: str | None = None,
    ) -> JSONResponse:
        if manager is None:
            raise HTTPException(
                status_code=HTTPStatus.SERVICE_UNAVAILABLE,
                detail="worker manager is not configured",
            )
        try:
            payload = await request.json()
        except ValueError:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        kind = payload.get("kind", "once")
        try:
            max_jobs = int(payload.get("max_jobs", 1))
            lease_seconds = int(payload.get("lease_seconds", 900))
            hermes_timeout_seconds = int(payload.get("hermes_timeout_seconds", 810))
        except (TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=HTTPStatus.BAD_REQUEST,
                detail=f"worker parameters must be integers: {exc}",
            ) from exc

        if generation_request_id is not None:
            ok, status_code, body = _preflight_scoped_research_worker(generation_request_id)
            if not ok:
                return JSONResponse(body, status_code=status_code)

        ok, message = manager.start(
            kind=kind,
            agent_id=payload.get("agent_id"),
            max_jobs=max_jobs,
            lease_seconds=lease_seconds,
            hermes_timeout_seconds=hermes_timeout_seconds,
            generation_request_id=generation_request_id,
        )
        if not ok:
            if str(message).startswith("worker_startup_failed:"):
                return JSONResponse(
                    {
                        "ok": False,
                        "code": "worker_startup_failed",
                        "stderr_tail": str(message).split(":", 1)[1].strip(),
                    },
                    status_code=HTTPStatus.CONFLICT,
                )
            raise HTTPException(
                status_code=HTTPStatus.CONFLICT, detail=message
            )
        return JSONResponse(
            {"ok": True, "message": message, "state": manager.state()},
            status_code=HTTPStatus.ACCEPTED,
        )

    @app.post("/api/research/worker/stop")
    def worker_stop() -> JSONResponse:
        if manager is None:
            raise HTTPException(
                status_code=HTTPStatus.SERVICE_UNAVAILABLE,
                detail="worker manager is not configured",
            )
        ok, message = manager.stop()
        if not ok:
            raise HTTPException(
                status_code=HTTPStatus.CONFLICT, detail=message
            )
        return JSONResponse({"ok": True, "message": message, "state": manager.state()})


def _preflight_scoped_research_worker(request_id: str) -> tuple[bool, int, dict[str, Any]]:
    import sqlalchemy as sa

    from persistence.models import research as model
    from persistence.session import transaction

    request_uuid = UUID(request_id)
    with transaction() as session:
        if not hasattr(session, "get"):
            return True, HTTPStatus.ACCEPTED, {}
        row = session.get(model.GenerationRequest, request_uuid)
        if row is None:
            return False, HTTPStatus.NOT_FOUND, {"detail": "request not found"}
        if row.status == "researched":
            return False, HTTPStatus.CONFLICT, {"code": "already_researched"}
        if row.status == "failed":
            return False, HTTPStatus.CONFLICT, {"code": "final_failure_no_retry_left"}
        if row.status not in {"draft", "researching"}:
            return False, HTTPStatus.CONFLICT, {"code": "request_not_runnable"}
        runnable = session.scalar(
            sa.select(sa.func.count())
            .select_from(model.ResearchRun)
            .where(
                model.ResearchRun.generation_request_id == request_uuid,
                sa.or_(
                    model.ResearchRun.status == "queued",
                    sa.and_(
                        model.ResearchRun.status == "running",
                        model.ResearchRun.lease_expires_at <= sa.func.now(),
                    ),
                ),
            )
        )
        if not runnable:
            return False, HTTPStatus.CONFLICT, {"code": "no_runnable_run"}
    return True, HTTPStatus.ACCEPTED, {}

# ---------------------------------------------------------------------------
# GET /api/research/logs
# GET /api/research/logs/{name}
# ---------------------------------------------------------------------------


def _register_log_endpoints(app: FastAPI, manager) -> None:
    @app.get("/api/research/logs")
    def list_research_logs() -> JSONResponse:
        if manager is None:
            raise HTTPException(
                status_code=HTTPStatus.SERVICE_UNAVAILABLE,
                detail="worker manager is not configured",
            )
        log_dir = manager.paths.research_logs
        if not log_dir.exists():
            return JSONResponse([])
        rows = []
        for path in sorted(log_dir.glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True):
            stat = path.stat()
            rows.append(
                {
                    "name": path.name,
                    "size": stat.st_size,
                    "updated_at": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                }
            )
        return JSONResponse(rows)

    @app.get("/api/research/logs/{name:path}")
    def get_research_log(name: str) -> JSONResponse:
        if manager is None:
            raise HTTPException(
                status_code=HTTPStatus.SERVICE_UNAVAILABLE,
                detail="worker manager is not configured",
            )
        safe_name = name.replace("\\", "/").rsplit("/", 1)[-1]
        path = manager.paths.research_logs / safe_name
        try:
            path.resolve().relative_to(manager.paths.research_logs.resolve())
            content = path.read_text(encoding="utf-8", errors="replace")[-30000:]
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.FORBIDDEN, detail="forbidden") from exc
        except OSError as exc:
            raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail="log not found") from exc
        return JSONResponse({"name": path.name, "content": content})


# ---------------------------------------------------------------------------
# 10.4 GET /api/research/categories
# ---------------------------------------------------------------------------


def _register_categories(app: FastAPI) -> None:
    @app.get("/api/research/categories")
    def get_categories() -> JSONResponse:
        from persistence.repositories import ResearchRepository
        from persistence.session import transaction

        with transaction() as session:
            categories = ResearchRepository(session).list_categories()
        return JSONResponse([_category_dict(c) for c in categories])


# ---------------------------------------------------------------------------
# 10.1 GET /api/research/requests?category=&status=
# ---------------------------------------------------------------------------


def _register_requests_list(app: FastAPI) -> None:
    @app.get("/api/research/requests")
    def list_requests(
        category: str | None = Query(default=None),
        status: str | None = Query(default=None),
        display_status: str | None = Query(default=None),
    ) -> JSONResponse:
        from persistence.repositories import ResearchRepository
        from persistence.session import transaction

        # Spec 10.1: validate `status` against the PG enum's allowed set so an
        # unknown value yields a clean 400 instead of leaking a DataError 500
        # from PostgreSQL when the query hits the enum column.
        if status is not None and status not in GenerationRequestStatus:
            raise HTTPException(
                status_code=HTTPStatus.BAD_REQUEST,
                detail=(
                    f"unknown status {status!r}; "
                    f"allowed: {list(GenerationRequestStatus)}"
                ),
            )
        if display_status is not None and display_status not in {
            "draft",
            "queued",
            "researching",
            "researched",
            "failed",
        }:
            raise HTTPException(
                status_code=HTTPStatus.BAD_REQUEST,
                detail=f"unknown display_status {display_status!r}",
            )

        with transaction() as session:
            repo = ResearchRepository(session)
            if category is not None:
                allowed = [c.code for c in repo.list_categories()]
                if category not in allowed:
                    raise HTTPException(
                        status_code=HTTPStatus.BAD_REQUEST,
                        detail=(
                            f"unknown category {category!r}; "
                            f"allowed: {allowed}"
                        ),
                    )
            requests = repo.list_generation_requests(category=category, status=status)
            latest_for_request = {}
            latest_lookup = getattr(repo, "get_latest_run_for_request", None)
            if latest_lookup is not None:
                latest_for_request = {
                    request.id: latest_lookup(request.id) for request in requests
                }
            rows = [
                _request_dict(request, latest_run=latest_for_request.get(request.id))
                for request in requests
            ]
            if display_status is not None:
                rows = [row for row in rows if row["display_status"] == display_status]
        return JSONResponse(rows)


# ---------------------------------------------------------------------------
# POST /api/research/requests  (web-side parity with `cli research submit`)
# ---------------------------------------------------------------------------


def _register_request_submit(app: FastAPI) -> None:
    @app.post("/api/research/requests", status_code=HTTPStatus.CREATED)
    async def submit_request(request: Request) -> JSONResponse:
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

        # Pull the required + optional fields out of the body. The service
        # layer raises ResearchValidationError on bad shapes (unknown
        # category, distribution sum mismatch, illegal difficulty label,
        # empty topic, etc.) — those are translated to 400.
        try:
            category = _require_str(payload, "category")
            topic = _require_str(payload, "topic")
            target_count = _require_positive_int(payload, "target_count")
            distribution = _require_distribution(payload)
        except ValueError as exc:
            raise HTTPException(
                status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)
            ) from exc

        seed_urls = payload.get("seed_urls", [])
        if not isinstance(seed_urls, list) or not all(
            isinstance(url, str) for url in seed_urls
        ):
            raise HTTPException(
                status_code=HTTPStatus.BAD_REQUEST,
                detail="seed_urls must be an array of strings",
            )
        max_attempts = payload.get("max_attempts", 3)
        if not isinstance(max_attempts, int) or max_attempts <= 0:
            raise HTTPException(
                status_code=HTTPStatus.BAD_REQUEST,
                detail="max_attempts must be a positive integer",
            )
        try:
            runtime_constraints = validate_runtime_constraints(
                payload.get("runtime_constraints", {})
            )
        except ResearchValidationError as exc:
            raise HTTPException(
                status_code=HTTPStatus.BAD_REQUEST,
                detail=str(exc),
            ) from exc

        from services import ResearchJobService

        service = ResearchJobService()
        try:
            generation_request, run = service.submit_request(
                category=category,
                topic=topic,
                target_count=target_count,
                difficulty_distribution=distribution,
                seed_urls=seed_urls,
                max_attempts=max_attempts,
                runtime_constraints=runtime_constraints,
                idempotency_key=request.headers.get("Idempotency-Key"),
            )
        except ResearchValidationError as exc:
            if str(exc) == "idempotency_key_conflict":
                raise HTTPException(
                    status_code=HTTPStatus.CONFLICT,
                    detail={"code": "idempotency_key_conflict"},
                ) from exc
            raise HTTPException(
                status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)
            ) from exc

        return JSONResponse(
            {
                "request": _request_dict(generation_request, latest_run=run),
                "latest_run": _run_dict(
                    run,
                    category=generation_request.category,
                    paths=_project_paths(app),
                ),
            },
            status_code=HTTPStatus.CREATED
            if getattr(service, "last_submit_created", True)
            else HTTPStatus.OK,
        )


def _require_str(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key!r} must be a non-empty string")
    return value


def _require_positive_int(payload: dict[str, Any], key: str) -> int:
    value = payload.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"{key!r} must be a positive integer")
    return value


def _require_distribution(payload: dict[str, Any]) -> dict[str, int]:
    raw = payload.get("difficulty_distribution")
    if not isinstance(raw, dict) or not raw:
        raise ValueError(
            "'difficulty_distribution' must be a non-empty object mapping "
            "label to positive integer count"
        )
    result: dict[str, int] = {}
    for label, count in raw.items():
        if not isinstance(label, str) or not label:
            raise ValueError("difficulty_distribution labels must be strings")
        if not isinstance(count, int) or isinstance(count, bool) or count <= 0:
            raise ValueError(
                f"difficulty_distribution[{label!r}] must be a positive integer"
            )
        result[label] = count
    return result


# ---------------------------------------------------------------------------
# 10.2 GET /api/research/requests/{id}
# ---------------------------------------------------------------------------


def _register_request_detail(app: FastAPI) -> None:
    @app.get("/api/research/requests/{request_id}")
    def get_request_detail(request_id: str) -> JSONResponse:
        from persistence.repositories import DesignTaskRepository, ResearchRepository
        from persistence.session import transaction

        try:
            request_uuid = UUID(request_id)
        except ValueError as exc:
            raise HTTPException(
                status_code=HTTPStatus.NOT_FOUND,
                detail="request not found",
            ) from exc

        with transaction() as session:
            repo = ResearchRepository(session)
            request = repo.get_generation_request(request_uuid)
            if request is None:
                raise HTTPException(
                    status_code=HTTPStatus.NOT_FOUND,
                    detail="request not found",
                )
            runs = repo.list_runs(generation_request_id=request_uuid)
            latest = repo.get_latest_run_for_request(request_uuid)
            latest_completed_lookup = getattr(
                repo,
                "get_latest_completed_run_for_request",
                None,
            )
            latest_completed = (
                latest_completed_lookup(request_uuid)
                if latest_completed_lookup is not None
                else (latest if latest and latest.status == "completed" else None)
            )
            result_run = latest_completed or (
                latest if latest and latest.status == "completed" else None
            )
            sources = repo.list_sources(result_run.id) if result_run else []
            findings = repo.list_findings(result_run.id) if result_run else []
            design_tasks_summary = DesignTaskRepository(
                session
            ).summarize_for_request(request_uuid)

        # Spec 10.2: finding list "grouped by kind".
        findings_by_kind: dict[str, list[dict[str, Any]]] = {}
        for finding in findings:
            findings_by_kind.setdefault(finding.kind, []).append(
                _finding_dict(finding)
            )

        return JSONResponse(
            {
                "request": _request_dict(request, latest_run=latest),
                "latest_run": _run_dict(
                    latest,
                    category=request.category,
                    paths=_project_paths(app),
                )
                if latest is not None
                else None,
                "latest_completed_run": _run_dict(
                    latest_completed,
                    category=request.category,
                    paths=_project_paths(app),
                )
                if latest_completed is not None
                else None,
                "runs": [
                    _run_dict(r, category=request.category, paths=_project_paths(app))
                    for r in runs
                ],
                "sources": [_source_dict(s) for s in sources],
                "findings_by_kind": findings_by_kind,
                "design_tasks_summary": design_tasks_summary,
            }
        )

    @app.delete("/api/research/requests/{request_id}")
    def delete_request(
        request_id: str,
        delete_artifacts: bool = Query(default=False),
    ) -> JSONResponse:
        from services import (
            ResourceDeletionConflictError,
            ResourceDeletionNotFoundError,
        )
        from web.resource_deletion import deletion_service

        try:
            request_uuid = UUID(request_id)
        except ValueError as exc:
            raise HTTPException(
                status_code=HTTPStatus.NOT_FOUND,
                detail="request not found",
            ) from exc
        try:
            result = deletion_service(app).delete_generation_request(
                request_uuid,
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


# ---------------------------------------------------------------------------
# 10.7 GET /api/research/runs?status=&claimed_by=&generation_request_id=&limit=
# ---------------------------------------------------------------------------


def _register_runs_list(app: FastAPI) -> None:
    @app.get("/api/research/runs")
    def list_runs(
        status: str | None = Query(default=None),
        claimed_by: str | None = Query(default=None),
        generation_request_id: str | None = Query(default=None),
        limit: int = Query(default=100, ge=1, le=500),
    ) -> JSONResponse:
        from persistence.repositories import ResearchRepository
        from persistence.session import transaction

        # Spec 10.7: validate `status` against ResearchRunStatus so an unknown
        # value yields a clean 400 instead of a PG DataError 500.
        if status is not None and status not in ResearchRunStatus:
            raise HTTPException(
                status_code=HTTPStatus.BAD_REQUEST,
                detail=(
                    f"unknown status {status!r}; "
                    f"allowed: {list(ResearchRunStatus)}"
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
            # Spec 10.7: "joined with category for queue inspection" — one SQL
            # JOIN, no N+1.
            rows = ResearchRepository(session).list_runs_with_category(
                status=status,
                claimed_by=claimed_by,
                generation_request_id=request_uuid,
                limit=limit,
            )

        return JSONResponse(
            [
                _run_dict(run, category=category, paths=_project_paths(app))
                for run, category in rows
            ]
        )


# ---------------------------------------------------------------------------
# 10.8 GET /api/research/queue/stats
# ---------------------------------------------------------------------------


def _register_queue_stats(app: FastAPI) -> None:
    @app.get("/api/research/queue/stats")
    def get_queue_stats() -> JSONResponse:
        from persistence.repositories import ResearchRepository
        from persistence.session import transaction

        with transaction() as session:
            stats = ResearchRepository(session).queue_stats()
        # `runs_near_lease_expiry` is already filtered to status='running'
        # AND lease_expires_at <= now()+60s by the repository; just stringify
        # the UUIDs for JSON transport.
        return JSONResponse(
            {
                "queued": stats["queued"],
                "running": stats["running"],
                "completed": stats["completed"],
                "failed": stats["failed"],
                "oldest_queued_age_seconds": stats["oldest_queued_age_seconds"],
                "runs_near_lease_expiry": [
                    str(run_id) for run_id in stats["runs_near_lease_expiry"]
                ],
            }
        )


# ---------------------------------------------------------------------------
# 10.5 GET /api/profile/bindings
# ---------------------------------------------------------------------------


def _register_bindings_list(app: FastAPI) -> None:
    @app.get("/api/profile/bindings")
    def list_bindings() -> JSONResponse:
        import sqlalchemy as sa

        from persistence.models import research as model
        from persistence.repositories import ResearchRepository
        from persistence.session import transaction

        with transaction() as session:
            bindings = ResearchRepository(session).list_bindings()
            roles = session.scalars(sa.select(model.AgentRole)).all()
            role_display = {row.code: row.display_name for row in roles}
        return JSONResponse(
            [_binding_dict(b, role_display.get(b.role)) for b in bindings]
        )


# ---------------------------------------------------------------------------
# 10.6 GET /api/profile/bindings/{role}
# ---------------------------------------------------------------------------


def _register_binding_detail(app: FastAPI) -> None:
    @app.get("/api/profile/bindings/{role}")
    def get_binding_detail(role: str) -> JSONResponse:
        import sqlalchemy as sa

        from persistence.models import research as model
        from persistence.repositories import ResearchRepository
        from persistence.session import transaction

        with transaction() as session:
            binding = ResearchRepository(session).get_binding(role)
            if binding is None:
                raise HTTPException(
                    status_code=HTTPStatus.NOT_FOUND,
                    detail=f"no binding for role {role!r}",
                )
            display = session.scalar(
                sa.select(model.AgentRole.display_name).where(
                    model.AgentRole.code == role
                )
            )
        return JSONResponse(_binding_dict(binding, display))


# ---------------------------------------------------------------------------
# DTO → dict serializers
# ---------------------------------------------------------------------------


def _isofmt(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _category_dict(category: dto.ChallengeCategory) -> dict[str, Any]:
    return {
        "code": category.code,
        "display_name": category.display_name,
        "description": category.description,
    }


def _derive_display_status(
    request: dto.GenerationRequest,
    latest_run: dto.ResearchRun | None = None,
) -> str:
    if request.status == "researching" and latest_run is not None:
        if latest_run.status == "queued":
            return "queued"
        if latest_run.status == "running":
            return "researching"
    return request.status


def _request_dict(
    request: dto.GenerationRequest,
    *,
    latest_run: dto.ResearchRun | None = None,
) -> dict[str, Any]:
    return {
        "id": str(request.id),
        "category": request.category,
        "topic": request.topic,
        "target_count": request.target_count,
        "difficulty_distribution": dict(request.difficulty_distribution),
        "runtime_constraints": dict(request.runtime_constraints),
        "seed_urls": list(request.seed_urls),
        "max_attempts": request.max_attempts,
        "status": request.status,
        "display_status": _derive_display_status(request, latest_run),
        "created_at": _isofmt(request.created_at),
        "updated_at": _isofmt(request.updated_at),
    }


def _run_dict(
    run: dto.ResearchRun,
    *,
    category: str | None = None,
    paths: ProjectPaths,
) -> dict[str, Any]:
    failure = _failure_fields(run, paths)
    row = {
        "id": str(run.id),
        "generation_request_id": str(run.generation_request_id),
        "parent_run_id": str(run.parent_run_id) if run.parent_run_id else None,
        "attempt": run.attempt,
        "status": run.status,
        "claimed_by": run.claimed_by,
        "claim_token": str(run.claim_token) if run.claim_token else None,
        "claimed_at": _isofmt(run.claimed_at),
        "heartbeat_at": _isofmt(run.heartbeat_at),
        "lease_expires_at": _isofmt(run.lease_expires_at),
        "started_at": _isofmt(run.started_at),
        "finished_at": _isofmt(run.finished_at),
        "last_error": run.last_error,
        "hermes_log_path": run.hermes_log_path,
        "profile_name_used": run.profile_name_used,
        "created_at": _isofmt(run.created_at),
        "category": category,
    }
    row.update(failure)
    return row


def _failure_fields(run: dto.ResearchRun, paths: ProjectPaths) -> dict[str, Any]:
    if run.status != "failed":
        return {
            "last_error_category": None,
            "last_error_title": None,
            "last_error_description": None,
            "last_error_actions": [],
            "recoverable": False,
        }
    classification = classify_last_error(run.last_error)
    return {
        "last_error_category": classification.category,
        "last_error_title": classification.title,
        "last_error_description": classification.description,
        "last_error_actions": list(classification.actions),
        "recoverable": _is_run_recoverable(run, paths),
    }


def _is_run_recoverable(run: dto.ResearchRun, paths: ProjectPaths) -> bool:
    if run.status != "failed":
        return False
    try:
        safe_log = read_safe_research_log(paths, run.hermes_log_path)
    except SafeResearchLogError:
        return False
    return has_ordered_stdout_markers(safe_log.text)


def _source_dict(source: dto.ResearchSource) -> dict[str, Any]:
    return {
        "id": str(source.id),
        "url": source.url,
        "title": source.title,
        "summary": source.summary,
        "content_hash": source.content_hash,
        "fetched_at": _isofmt(source.fetched_at),
        "raw_text_path": source.raw_text_path,
    }


def _finding_dict(finding: dto.ResearchFinding) -> dict[str, Any]:
    return {
        "id": str(finding.id),
        "kind": finding.kind,
        "label": finding.label,
        "summary": finding.summary,
    }


def _binding_dict(
    binding: dto.HermesProfileBinding,
    display_name: str | None,
) -> dict[str, Any]:
    return {
        "role": binding.role,
        "display_name": display_name,
        "profile_name": binding.profile_name,
        "description": binding.description,
        "status": binding.status,
        "last_used_at": _isofmt(binding.last_used_at),
        "last_used_run_id": str(binding.last_used_run_id)
        if binding.last_used_run_id
        else None,
        "created_at": _isofmt(binding.created_at),
        "updated_at": _isofmt(binding.updated_at),
    }


# ---------------------------------------------------------------------------
# Design task planning endpoints (add-design-task-planning §5)
# ---------------------------------------------------------------------------


def _register_design_task_endpoints(app: FastAPI) -> None:
    @app.post("/api/research/requests/{request_id}/design-tasks/generate")
    def generate_design_tasks(request_id: str) -> JSONResponse:
        from services import DesignTaskPlanningService

        try:
            request_uuid = UUID(request_id)
        except ValueError as exc:
            raise HTTPException(
                status_code=HTTPStatus.NOT_FOUND,
                detail="request not found",
            ) from exc

        try:
            tasks = DesignTaskPlanningService().generate_for_request(request_uuid)
        except DesignTaskValidationError as exc:
            status = (
                HTTPStatus.NOT_FOUND
                if "does not exist" in str(exc)
                else HTTPStatus.CONFLICT
            )
            raise HTTPException(status_code=status, detail=str(exc)) from exc

        return JSONResponse(
            {
                "request_id": str(request_uuid),
                "design_task_ids": [str(t.id) for t in tasks],
                "total": len(tasks),
            },
            status_code=HTTPStatus.CREATED,
        )

    @app.post("/api/design-tasks/{task_id}/queue")
    def queue_design_task(task_id: str) -> JSONResponse:
        return _transition_design_task(task_id, "queued")

    @app.post("/api/design-tasks/{task_id}/archive")
    def archive_design_task(task_id: str) -> JSONResponse:
        return _transition_design_task(task_id, "archived")

    @app.post("/api/design-tasks/{task_id}/design")
    def design_challenge(task_id: str) -> JSONResponse:
        from services import (
            ChallengeDesignConflictError,
            ChallengeDesignNotFoundError,
            ChallengeDesignService,
        )

        try:
            task_uuid = UUID(task_id)
        except ValueError as exc:
            raise HTTPException(
                status_code=HTTPStatus.NOT_FOUND,
                detail="design task not found",
            ) from exc

        try:
            result = ChallengeDesignService(
                paths=_project_paths(app),
            ).design_for_task(task_uuid, caller="dashboard")
        except ChallengeDesignNotFoundError as exc:
            raise HTTPException(
                status_code=HTTPStatus.NOT_FOUND,
                detail=str(exc),
            ) from exc
        except ChallengeDesignConflictError as exc:
            raise HTTPException(
                status_code=HTTPStatus.CONFLICT,
                detail=str(exc),
            ) from exc

        return JSONResponse(_design_result_dict(result))

    @app.get("/api/design-attempts/{attempt_id}/artifact")
    def get_design_attempt_artifact(
        attempt_id: str,
        kind: str = Query(...),
    ) -> Response:
        from persistence.repositories import ChallengeDesignRepository
        from persistence.session import transaction

        if kind not in {"prompt", "log"}:
            raise HTTPException(
                status_code=HTTPStatus.BAD_REQUEST,
                detail="kind must be 'prompt' or 'log'",
            )
        try:
            attempt_uuid = UUID(attempt_id)
        except ValueError as exc:
            raise HTTPException(
                status_code=HTTPStatus.NOT_FOUND,
                detail="design attempt not found",
            ) from exc

        with transaction() as session:
            attempt = ChallengeDesignRepository(session).get_attempt(attempt_uuid)
        if attempt is None:
            raise HTTPException(
                status_code=HTTPStatus.NOT_FOUND,
                detail="design attempt not found",
            )

        stored_path = attempt.prompt_path if kind == "prompt" else attempt.hermes_log_path
        if not stored_path:
            raise HTTPException(
                status_code=HTTPStatus.NOT_FOUND,
                detail=f"{kind} artifact not found",
            )

        paths = _project_paths(app)
        allowed_root = paths.design_prompts if kind == "prompt" else paths.design_logs
        artifact = _resolve_design_artifact(paths.root, allowed_root, stored_path)
        try:
            content = artifact.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise HTTPException(
                status_code=HTTPStatus.NOT_FOUND,
                detail=f"{kind} artifact not found",
            ) from exc
        return Response(content=content, media_type="text/plain; charset=utf-8")


def _transition_design_task(task_id: str, target_status: str) -> JSONResponse:
    from persistence.repositories import DesignTaskRepository
    from persistence.session import transaction

    try:
        task_uuid = UUID(task_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=HTTPStatus.NOT_FOUND,
            detail="design task not found",
        ) from exc

    try:
        with transaction() as session:
            updated = DesignTaskRepository(session).set_design_task_status(
                task_uuid, target_status
            )
    except DesignTaskValidationError as exc:
        status = (
            HTTPStatus.NOT_FOUND
            if "does not exist" in str(exc)
            else HTTPStatus.CONFLICT
        )
        raise HTTPException(status_code=status, detail=str(exc)) from exc

    return JSONResponse(_design_task_dict(updated))


def _project_paths(app: FastAPI):
    from core.paths import ProjectPaths

    return getattr(app.state, "project_paths", None) or ProjectPaths.discover()


def _design_task_dict(
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
        "created_at": _isofmt(task.created_at),
        "updated_at": _isofmt(task.updated_at),
    }
    if attempts is not None:
        row["attempts"] = [_attempt_summary_dict(attempt) for attempt in attempts]
    if latest_design is not None or attempts is not None:
        row["latest_design"] = _challenge_design_dict(latest_design)
    return row


def _attempt_summary_dict(attempt: challenge_dto.DesignAttempt) -> dict[str, Any]:
    return {
        "id": str(attempt.id),
        "attempt": attempt.attempt,
        "status": attempt.status,
        "started_at": _isofmt(attempt.started_at),
        "finished_at": _isofmt(attempt.finished_at),
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


def _challenge_design_dict(
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
        "created_at": _isofmt(design.created_at),
        "updated_at": _isofmt(design.updated_at),
    }


def _design_result_dict(result) -> dict[str, Any]:
    return {
        "design_task_id": str(result.design_task_id),
        "attempt_id": str(result.attempt_id),
        "design_task_status": result.design_task_status,
        "attempt_status": result.attempt_status,
        "challenge_design": _challenge_design_dict(result.challenge_design),
        "error": result.error,
    }


def _resolve_design_artifact(root: Path, allowed_root: Path, stored_path: str) -> Path:
    raw = Path(stored_path)
    if raw.is_absolute():
        raise HTTPException(
            status_code=HTTPStatus.FORBIDDEN,
            detail="absolute artifact paths are forbidden",
        )
    candidate = (root / raw).resolve()
    allowed = allowed_root.resolve()
    try:
        candidate.relative_to(allowed)
    except ValueError as exc:
        raise HTTPException(
            status_code=HTTPStatus.FORBIDDEN,
            detail="artifact path is outside the allowed design artifact root",
        ) from exc
    return candidate
