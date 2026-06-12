"""LLM provider settings endpoints.

Contract: the plain-text API key MUST NOT appear in any response body, log
line, or error message. ``GET`` returns only the masked form; ``PUT`` accepts
the mask placeholder to leave the stored key unchanged; ``POST .../test``
returns ``{ok, latency_ms, model, error}`` with no key substring.
"""

from __future__ import annotations

from http import HTTPStatus

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from core.paths import ProjectPaths
from domain.llm_settings import load_settings, save_settings, test_connection


def create_llm_router(paths: ProjectPaths) -> APIRouter:
    router = APIRouter()

    @router.get("/api/settings/llm")
    def get_llm_settings() -> JSONResponse:
        return JSONResponse(load_settings(paths))

    @router.put("/api/settings/llm")
    async def put_llm_settings(request: Request) -> JSONResponse:
        try:
            payload = await request.json()
        except ValueError:
            return JSONResponse(
                {"ok": False, "message": "invalid JSON body"},
                status_code=HTTPStatus.BAD_REQUEST,
            )
        if not isinstance(payload, dict):
            return JSONResponse(
                {"ok": False, "message": "payload must be an object"},
                status_code=HTTPStatus.BAD_REQUEST,
            )
        try:
            saved = save_settings(paths, payload)
        except ValueError as exc:
            return JSONResponse(
                {"ok": False, "message": str(exc)},
                status_code=HTTPStatus.BAD_REQUEST,
            )
        return JSONResponse({"ok": True, "settings": saved})

    @router.post("/api/settings/llm/test")
    def post_test_connection() -> JSONResponse:
        return JSONResponse(test_connection(paths))

    return router
