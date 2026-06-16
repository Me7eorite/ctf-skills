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
from typing import Any
from uuid import UUID

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from domain import research as dto
from domain.research import GenerationRequestStatus, ResearchRunStatus
from domain.research_validators import ResearchValidationError


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
    _register_worker_endpoints(app, worker_manager)


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

        ok, message = manager.start(
            kind=kind,
            agent_id=payload.get("agent_id"),
            max_jobs=max_jobs,
            lease_seconds=lease_seconds,
            hermes_timeout_seconds=hermes_timeout_seconds,
        )
        if not ok:
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
            requests = repo.list_generation_requests(
                category=category, status=status
            )
        return JSONResponse([_request_dict(r) for r in requests])


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

        from services import ResearchJobService

        try:
            generation_request, run = ResearchJobService().submit_request(
                category=category,
                topic=topic,
                target_count=target_count,
                difficulty_distribution=distribution,
                seed_urls=seed_urls,
                max_attempts=max_attempts,
            )
        except ResearchValidationError as exc:
            raise HTTPException(
                status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)
            ) from exc

        return JSONResponse(
            {
                "request_id": str(generation_request.id),
                "run_id": str(run.id),
                "category": generation_request.category,
                "status": "queued",
            },
            status_code=HTTPStatus.CREATED,
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
        from persistence.repositories import ResearchRepository
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
            sources = repo.list_sources(latest.id) if latest else []
            findings = repo.list_findings(latest.id) if latest else []

        # Spec 10.2: finding list "grouped by kind".
        findings_by_kind: dict[str, list[dict[str, Any]]] = {}
        for finding in findings:
            findings_by_kind.setdefault(finding.kind, []).append(
                _finding_dict(finding)
            )

        return JSONResponse(
            {
                "request": _request_dict(request),
                "latest_run": _run_dict(latest, category=request.category)
                if latest is not None
                else None,
                "runs": [_run_dict(r, category=request.category) for r in runs],
                "sources": [_source_dict(s) for s in sources],
                "findings_by_kind": findings_by_kind,
            }
        )


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
            [_run_dict(run, category=category) for run, category in rows]
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


def _request_dict(request: dto.GenerationRequest) -> dict[str, Any]:
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
        "created_at": _isofmt(request.created_at),
        "updated_at": _isofmt(request.updated_at),
    }


def _run_dict(run: dto.ResearchRun, *, category: str | None = None) -> dict[str, Any]:
    return {
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
