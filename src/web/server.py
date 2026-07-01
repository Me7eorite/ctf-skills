"""FastAPI adapter for the dashboard."""

from __future__ import annotations

import logging
import mimetypes
import shutil
import sys
import time
from http import HTTPStatus
from pathlib import Path
from threading import Thread
from uuid import UUID

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from core.jsonio import read_json
from core.paths import ProjectPaths
from persistence import make_postgres_progress_store
from services import ResourceDeletionService
from services.build_profile_readiness import check_build_profile_readiness
from services.build_reconciler import BuildReconciler
from web.build_attempts_endpoints import register_build_attempts_endpoints
from web.dashboard import DashboardService
from web.design_task_endpoints import register_design_task_read_endpoints
from web.research_endpoints import register_research_endpoints
from web.research_worker_manager import ResearchWorkerManager

LOG = logging.getLogger(__name__)


def create_app(
    service: DashboardService,
    build_reconciler: BuildReconciler | None = None,
    build_profile_readiness: dict | None = None,
) -> FastAPI:
    app = FastAPI(
        title="Challenge Factory Dashboard",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.project_paths = service.paths
    app.state.build_reconciler = build_reconciler
    app.state.progress_store = service.store
    app.state.dashboard_tasks = service.tasks
    app.state.build_profile_readiness = build_profile_readiness or {
        "ready": True,
        "categories": {},
        "missing_profiles": [],
    }

    @app.get("/api/state")
    def get_state() -> JSONResponse:
        # 中文注释：曾经在这里同步触发 reconciler tick，但前端高频 polling
        # 会让 tick 频率超过文件系统稳定速度，加剧 lost 误判。后台线程
        # 已经按 poll_interval 节奏在跑，前端拿到的状态是最新一次 tick 的
        # 结果，足够使用；如果需要触发立即 tick，请显式调
        # POST /api/actions/reconcile（如后续提供）。
        payload = service.state()
        payload["build_readiness"] = app.state.build_profile_readiness
        payload["sequential_worker_result"] = _latest_sequential_worker_result(service.paths)
        return JSONResponse(payload)

    @app.get("/api/ui-state")
    def get_ui_state() -> JSONResponse:
        payload = service.ui_state()
        payload["build_readiness"] = app.state.build_profile_readiness
        payload["sequential_worker_result"] = _latest_sequential_worker_result(service.paths)
        return JSONResponse(payload)

    @app.get("/api/logs/{name:path}")
    def get_log(name: str) -> JSONResponse:
        try:
            content = service.read_log(name)
        except FileNotFoundError as exc:
            raise HTTPException(
                status_code=HTTPStatus.NOT_FOUND,
                detail="log not found",
            ) from exc
        return JSONResponse({"name": Path(name).name, "content": content})

    @app.post("/api/actions/worker")
    def post_worker_action() -> JSONResponse:
        return _action_response(*service.tasks.start("worker"))

    @app.post("/api/actions/validate")
    def post_validate_action() -> JSONResponse:
        return _action_response(*service.tasks.start("validate"))

    @app.post("/api/seeds")
    async def post_seed(request: Request) -> JSONResponse:
        try:
            seed = service.save_seed(await request.json())
        except ValueError as exc:
            return JSONResponse(
                {"ok": False, "message": str(exc)},
                status_code=HTTPStatus.BAD_REQUEST,
            )
        return JSONResponse({"ok": True, "seed": seed})

    @app.delete("/api/seeds/{challenge_id}")
    def delete_seed(challenge_id: str) -> JSONResponse:
        try:
            service.delete_seed(challenge_id)
        except FileNotFoundError:
            raise HTTPException(
                status_code=HTTPStatus.NOT_FOUND, detail="seed not found"
            )
        return JSONResponse({"ok": True, "message": f"{challenge_id} 已删除"})

    @app.post("/api/seeds/enqueue")
    async def enqueue_seeds(request: Request) -> JSONResponse:
        try:
            payload = await request.json()
            size = int(payload.get("size", 5))
            created = service.enqueue_seeds(size)
        except (TypeError, ValueError) as exc:
            return JSONResponse(
                {"ok": False, "message": str(exc)},
                status_code=HTTPStatus.BAD_REQUEST,
            )
        except FileExistsError as exc:
            return JSONResponse(
                {"ok": False, "message": str(exc)},
                status_code=HTTPStatus.CONFLICT,
            )
        return JSONResponse(
            {
                "ok": True,
                "message": f"已创建 {len(created)} 个待处理分片",
                "shards": [path.name for path in created],
            },
            status_code=HTTPStatus.CREATED,
        )

    @app.post("/api/shards/{state}/{name:path}/requeue")
    def post_requeue_shard(state: str, name: str) -> JSONResponse:
        if state not in {"failed", "running"}:
            raise HTTPException(
                status_code=HTTPStatus.NOT_FOUND, detail="not found"
            )
        conflict = _attributed_shard_conflict(service, state, name)
        if conflict is not None:
            return conflict
        try:
            destination = service.requeue_shard(name, state)
        except (FileNotFoundError, RuntimeError):
            return JSONResponse(
                {"ok": False, "message": "当前无法重新入队该分片"},
                status_code=HTTPStatus.CONFLICT,
            )
        return JSONResponse(
            {"ok": True, "message": f"{destination.name} 已重新入队"}
        )

    # Section 10 read endpoints + worker control: must be registered BEFORE
    # the static catch-all so `/api/research/...` and `/api/profile/...` win
    # over the wildcard.
    worker_manager = ResearchWorkerManager(service.paths)
    register_research_endpoints(app, worker_manager=worker_manager)
    register_design_task_read_endpoints(app)
    register_build_attempts_endpoints(app)

    # Static catch-all stays last so API routes win.
    @app.get("/{request_path:path}")
    def get_static(request_path: str) -> Response:
        relative = "index.html" if request_path in {"", "/"} else request_path
        path = service.paths.static / relative
        try:
            path.resolve().relative_to(service.paths.static.resolve())
            body = path.read_bytes()
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.FORBIDDEN) from exc
        except OSError as exc:
            raise HTTPException(status_code=HTTPStatus.NOT_FOUND) from exc
        media_type = (
            mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        )
        return Response(content=body, media_type=media_type)

    return app


def _action_response(ok: bool, message: str) -> JSONResponse:
    return JSONResponse(
        {"ok": ok, "message": message},
        status_code=HTTPStatus.ACCEPTED if ok else HTTPStatus.CONFLICT,
    )


def _latest_sequential_worker_result(paths: ProjectPaths) -> dict | None:
    result = read_json(paths.logs / "dashboard-sequential-worker-result.json", None)
    return result if isinstance(result, dict) else None


def _attributed_shard_conflict(
    service: DashboardService,
    state: str,
    name: str,
) -> JSONResponse | None:
    source = service.paths.shards / state / Path(name).name
    try:
        source.resolve().relative_to((service.paths.shards / state).resolve())
    except ValueError:
        raise HTTPException(status_code=HTTPStatus.FORBIDDEN, detail="forbidden")
    payload = read_json(source, None)
    if not isinstance(payload, dict):
        return None
    build_attempt_id = payload.get("build_attempt_id")
    if not isinstance(build_attempt_id, str) or not build_attempt_id:
        return None
    retry_url = f"/api/build-attempts/{build_attempt_id}/retry"
    return JSONResponse(
        {
            "ok": False,
            "message": (
                "This shard is linked to a build attempt; use the build-attempt "
                "retry action instead."
            ),
            "build_attempt_id": build_attempt_id,
            "retry_url": retry_url,
        },
        status_code=HTTPStatus.CONFLICT,
    )


def serve(paths: ProjectPaths, host: str, port: int) -> None:
    import uvicorn

    paths.initialize()
    service = DashboardService(paths, progress=make_postgres_progress_store())
    build_readiness = check_build_profile_readiness(paths=paths)
    if not build_readiness["ready"]:
        missing = ", ".join(build_readiness["missing_profiles"])
        commands = "; ".join(
            item["create_command"]
            for item in build_readiness["categories"].values()
            if not item["ready"]
        )
        LOG.warning(
            "build environment is not ready; missing Hermes profiles: %s; run: %s",
            missing,
            commands,
        )
    reconciler = BuildReconciler(paths=paths)
    try:
        reconciler.orchestration.recover_staging()
    except Exception as exc:
        LOG.warning("startup build staging recovery failed: %s", exc)
    try:
        for warning in ResourceDeletionService(paths=paths).recover_quarantine():
            LOG.warning("startup deletion quarantine recovery: %s", warning)
    except Exception as exc:
        LOG.warning("startup deletion quarantine recovery failed: %s", exc)
    try:
        _sweep_stale_research_staging(
            paths,
            max_age_seconds=300,
        )
        _reconcile_orphan_research_sources(paths)
    except Exception as exc:
        LOG.warning("startup research source cleanup failed: %s", exc)
    thread = Thread(target=reconciler.run_forever, daemon=True)
    thread.start()
    app = create_app(
        service,
        build_reconciler=reconciler,
        build_profile_readiness=build_readiness,
    )
    print(f"Challenge Factory dashboard: http://{host}:{port}", file=sys.stderr, flush=True)
    try:
        uvicorn.run(app, host=host, port=port, log_level="info", access_log=False)
    finally:
        reconciler.stop()


def _sweep_stale_research_staging(paths: ProjectPaths, *, max_age_seconds: int = 300) -> None:
    root = paths.research_sources_staging
    if not root.exists():
        return
    cutoff = time.time() - max_age_seconds
    for child in root.iterdir():
        try:
            if child.is_dir() and child.stat().st_mtime < cutoff:
                shutil.rmtree(child)
        except OSError as exc:
            LOG.warning("failed to remove stale research staging %s: %s", child, exc)


def _reconcile_orphan_research_sources(paths: ProjectPaths) -> None:
    import sqlalchemy as sa

    from persistence.models import research as model
    from persistence.session import transaction

    root = paths.research_sources
    if not root.exists():
        return
    with transaction() as session:
        for child in root.iterdir():
            if not child.is_dir():
                continue
            try:
                run_id = UUID(child.name)
            except ValueError:
                continue
            supported = session.scalar(
                sa.select(sa.func.count())
                .select_from(model.ResearchSource)
                .join(model.ResearchRun, model.ResearchSource.research_run_id == model.ResearchRun.id)
                .where(
                    model.ResearchRun.id == run_id,
                    model.ResearchRun.status == "completed",
                    model.ResearchSource.raw_text_path.like(f"%{child.name}%"),
                )
            )
            if not supported:
                shutil.rmtree(child)
