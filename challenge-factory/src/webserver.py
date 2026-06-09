"""FastAPI adapter for the dashboard."""

from __future__ import annotations

import mimetypes
from http import HTTPStatus
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from dashboard import DashboardService
from paths import ProjectPaths


def create_app(service: DashboardService) -> FastAPI:
    app = FastAPI(
        title="Challenge Factory Dashboard",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @app.get("/api/state")
    def get_state() -> JSONResponse:
        return JSONResponse(service.state())

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

    @app.post("/api/shards/{state}/{name:path}/requeue")
    def post_requeue_shard(state: str, name: str) -> JSONResponse:
        if state not in {"failed", "running"}:
            raise HTTPException(
                status_code=HTTPStatus.NOT_FOUND, detail="not found"
            )
        try:
            destination = service.requeue_shard(name, state)
        except (FileNotFoundError, RuntimeError) as exc:
            return JSONResponse(
                {"ok": False, "message": "当前无法重新入队该分片"},
                status_code=HTTPStatus.CONFLICT,
            )
        return JSONResponse(
            {"ok": True, "message": f"{destination.name} 已重新入队"}
        )

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


def serve(paths: ProjectPaths, host: str, port: int) -> None:
    import uvicorn

    paths.initialize()
    service = DashboardService(paths)
    app = create_app(service)
    print(f"Challenge Factory dashboard: http://{host}:{port}")
    uvicorn.run(app, host=host, port=port, log_level="info", access_log=False)
