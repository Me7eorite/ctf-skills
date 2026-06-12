"""New Run presets persisted to ``work/presets.json``."""

from __future__ import annotations

import time
from http import HTTPStatus

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from core.jsonio import read_json, write_json
from core.paths import ProjectPaths


def _presets_path(paths: ProjectPaths):
    return paths.work / "presets.json"


def _load(paths: ProjectPaths) -> list[dict]:
    raw = read_json(_presets_path(paths), {"presets": []})
    if not isinstance(raw, dict):
        return []
    presets = raw.get("presets")
    if not isinstance(presets, list):
        return []
    cleaned: list[dict] = []
    for item in presets:
        if isinstance(item, dict) and isinstance(item.get("name"), str):
            cleaned.append(item)
    return cleaned


def _save(paths: ProjectPaths, presets: list[dict]) -> None:
    write_json(_presets_path(paths), {"presets": presets})


def create_presets_router(paths: ProjectPaths) -> APIRouter:
    router = APIRouter()

    @router.get("/api/presets")
    def list_presets() -> JSONResponse:
        return JSONResponse({"presets": _load(paths)})

    @router.post("/api/presets")
    async def create_preset(request: Request) -> JSONResponse:
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
        name = str(payload.get("name", "")).strip()
        if not name:
            return JSONResponse(
                {"ok": False, "message": "name is required"},
                status_code=HTTPStatus.BAD_REQUEST,
            )
        record = {
            "name": name,
            "payload": payload.get("payload"),
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        presets = [item for item in _load(paths) if item.get("name") != name]
        presets.append(record)
        _save(paths, presets)
        return JSONResponse(
            {"ok": True, "preset": record},
            status_code=HTTPStatus.CREATED,
        )

    @router.delete("/api/presets/{name}")
    def delete_preset(name: str) -> JSONResponse:
        presets = _load(paths)
        remaining = [item for item in presets if item.get("name") != name]
        if len(remaining) == len(presets):
            return JSONResponse(
                {"ok": False, "message": "preset not found"},
                status_code=HTTPStatus.NOT_FOUND,
            )
        _save(paths, remaining)
        return JSONResponse({"ok": True, "name": name})

    return router
