"""FastAPI adapter for the dashboard."""

from __future__ import annotations

import mimetypes
from http import HTTPStatus
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from core.paths import ProjectPaths
from web.dashboard import DashboardService
from web.trace import create_trace_router


class DemoReadOnlyError(Exception):
    """Raised when a demo-mode request attempts to mutate state."""


def create_app(service: DashboardService, *, demo: bool = False) -> FastAPI:
    app = FastAPI(
        title="Challenge Factory Dashboard",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.include_router(create_trace_router(service.store))

    def require_writable() -> None:
        if demo:
            raise DemoReadOnlyError

    @app.exception_handler(DemoReadOnlyError)
    def demo_read_only_handler(
        _request: Request, _exc: DemoReadOnlyError
    ) -> JSONResponse:
        return JSONResponse(
            {"ok": False, "message": "Demo mode is read-only"},
            status_code=HTTPStatus.CONFLICT,
        )

    @app.get("/api/state")
    def get_state() -> JSONResponse:
        return JSONResponse(service.state())

    @app.get("/api/mode")
    def get_mode() -> JSONResponse:
        return JSONResponse({"demo": demo})

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

    @app.post("/api/actions/worker", dependencies=[Depends(require_writable)])
    def post_worker_action() -> JSONResponse:
        return _action_response(*service.tasks.start("worker"))

    @app.post("/api/actions/validate", dependencies=[Depends(require_writable)])
    def post_validate_action() -> JSONResponse:
        return _action_response(*service.tasks.start("validate"))

    @app.post("/api/seeds", dependencies=[Depends(require_writable)])
    async def post_seed(request: Request) -> JSONResponse:
        try:
            seed = service.save_seed(await request.json())
        except ValueError as exc:
            return JSONResponse(
                {"ok": False, "message": str(exc)},
                status_code=HTTPStatus.BAD_REQUEST,
            )
        return JSONResponse({"ok": True, "seed": seed})

    @app.delete(
        "/api/seeds/{challenge_id}",
        dependencies=[Depends(require_writable)],
    )
    def delete_seed(challenge_id: str) -> JSONResponse:
        try:
            service.delete_seed(challenge_id)
        except FileNotFoundError:
            raise HTTPException(
                status_code=HTTPStatus.NOT_FOUND, detail="seed not found"
            )
        return JSONResponse({"ok": True, "message": f"{challenge_id} 已删除"})

    @app.post("/api/seeds/enqueue", dependencies=[Depends(require_writable)])
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

    @app.post(
        "/api/shards/{state}/{name:path}/requeue",
        dependencies=[Depends(require_writable)],
    )
    def post_requeue_shard(state: str, name: str) -> JSONResponse:
        if state not in {"failed", "running"}:
            raise HTTPException(
                status_code=HTTPStatus.NOT_FOUND, detail="not found"
            )
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

    # Static catch-all stays last so API routes win.
    @app.get("/{request_path:path}")
    def get_static(request_path: str) -> Response:
        relative = "index.html" if request_path in {"", "/"} else request_path
        static_root = service.paths.static / "dist"
        if not static_root.exists():
            return Response(
                "Frontend build is missing. Run: uv run challenge-factory build-ui\n",
                media_type="text/plain",
                status_code=HTTPStatus.OK,
            )
        path = static_root / relative
        try:
            path.resolve().relative_to(static_root.resolve())
            body = path.read_bytes()
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.FORBIDDEN) from exc
        except OSError as exc:
            if request_path and "." not in Path(request_path).name:
                body = (static_root / "index.html").read_bytes()
                return Response(content=body, media_type="text/html")
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


def serve(paths: ProjectPaths, host: str, port: int, *, demo: bool = False) -> None:
    import uvicorn

    paths.initialize()
    service = DashboardService(paths)
    app = create_app(service, demo=demo)
    print(f"Challenge Factory dashboard: http://{host}:{port}")
    uvicorn.run(app, host=host, port=port, log_level="info", access_log=False)
