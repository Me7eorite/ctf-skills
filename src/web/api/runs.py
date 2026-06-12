"""Run/shard inspection endpoints."""

from __future__ import annotations

import mimetypes
from http import HTTPStatus
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse, Response

from core.jsonio import read_json
from core.paths import ProjectPaths

SHARD_STATES = ("pending", "running", "done", "failed")


def _find_shard(paths: ProjectPaths, name: str) -> tuple[str, Path] | None:
    for state in SHARD_STATES:
        candidate = paths.shards / state / name
        if candidate.is_file():
            return state, candidate
    return None


def _report_for_shard(paths: ProjectPaths, shard_file: Path) -> dict:
    report = paths.reports / f"{shard_file.stem}.report.json"
    raw = read_json(report, {})
    return raw if isinstance(raw, dict) else {}


def _pass_rate(report: dict) -> float | None:
    challenges = report.get("challenges")
    if not isinstance(challenges, list) or not challenges:
        return None
    total = 0
    passed = 0
    for entry in challenges:
        if not isinstance(entry, dict):
            continue
        total += 1
        if entry.get("solve_status") == "passed":
            passed += 1
    if total == 0:
        return None
    return round(passed / total, 4)


def _shard_summary(paths: ProjectPaths, state: str, shard_file: Path) -> dict:
    payload = read_json(shard_file, {})
    if not isinstance(payload, dict):
        payload = {}
    challenges = payload.get("challenges", [])
    challenge_ids = [
        item.get("id")
        for item in challenges
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    ]
    report = _report_for_shard(paths, shard_file)
    return {
        "name": shard_file.name,
        "state": state,
        "started_at": shard_file.stat().st_mtime,
        "challenge_count": len(challenge_ids),
        "challenge_ids": challenge_ids,
        "pass_rate": _pass_rate(report),
        "categories": sorted(
            {
                item.get("category", "unknown")
                for item in challenges
                if isinstance(item, dict)
            }
        ),
    }


def _challenge_directory(paths: ProjectPaths, challenge_id: str) -> Path | None:
    for metadata_path in paths.challenges.glob(f"*/{challenge_id}/metadata.json"):
        return metadata_path.parent
    return None


def _safe_resolve(root: Path, relative: str) -> Path | None:
    if relative.startswith("/") or "\x00" in relative:
        return None
    if any(part == ".." for part in Path(relative).parts):
        return None
    candidate = (root / relative).resolve()
    root_resolved = root.resolve()
    try:
        candidate.relative_to(root_resolved)
    except ValueError:
        return None
    return candidate


def create_runs_router(paths: ProjectPaths) -> APIRouter:
    router = APIRouter()

    @router.get("/api/runs")
    def list_runs(
        limit: int = Query(50, ge=1, le=500),
        offset: int = Query(0, ge=0),
        state: str | None = Query(None),
    ) -> JSONResponse:
        states = (state,) if state in SHARD_STATES else SHARD_STATES
        rows: list[dict] = []
        for st in states:
            directory = paths.shards / st
            if not directory.exists():
                continue
            for shard_path in sorted(
                directory.glob("*.json"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            ):
                if shard_path.name.endswith(".claim.json"):
                    continue
                rows.append(_shard_summary(paths, st, shard_path))
        total = len(rows)
        return JSONResponse(
            {
                "total": total,
                "limit": limit,
                "offset": offset,
                "items": rows[offset : offset + limit],
            }
        )

    @router.get("/api/runs/{shard}")
    def get_run(shard: str) -> JSONResponse:
        found = _find_shard(paths, shard)
        if not found:
            raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail="run not found")
        state, shard_file = found
        summary = _shard_summary(paths, state, shard_file)
        summary["report"] = _report_for_shard(paths, shard_file)
        return JSONResponse(summary)

    @router.get("/api/runs/{shard}/challenges/{challenge_id}")
    def get_challenge(shard: str, challenge_id: str) -> JSONResponse:
        found = _find_shard(paths, shard)
        if not found:
            raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail="run not found")
        _state, shard_file = found
        directory = _challenge_directory(paths, challenge_id)
        if directory is None:
            raise HTTPException(
                status_code=HTTPStatus.NOT_FOUND, detail="challenge not found"
            )
        metadata = read_json(directory / "metadata.json", {})
        if not isinstance(metadata, dict):
            metadata = {}
        files = sorted(
            str(path.relative_to(directory)).replace("\\", "/")
            for path in directory.rglob("*")
            if path.is_file()
        )
        report = _report_for_shard(paths, shard_file)
        per_challenge = {}
        for entry in report.get("challenges", []) or []:
            if isinstance(entry, dict) and entry.get("id") == challenge_id:
                per_challenge = entry
                break
        return JSONResponse(
            {
                "id": challenge_id,
                "metadata": metadata,
                "files": files,
                "validation": per_challenge,
            }
        )

    @router.get("/api/runs/{shard}/artifacts/{artifact_path:path}")
    def get_artifact(shard: str, artifact_path: str) -> Response:
        found = _find_shard(paths, shard)
        if not found:
            raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail="run not found")
        safe = _safe_resolve(paths.challenges, artifact_path)
        if safe is None:
            raise HTTPException(
                status_code=HTTPStatus.BAD_REQUEST,
                detail="invalid artifact path",
            )
        if not safe.is_file():
            raise HTTPException(
                status_code=HTTPStatus.NOT_FOUND,
                detail="artifact not found",
            )
        media_type = mimetypes.guess_type(safe.name)[0] or "application/octet-stream"
        return Response(content=safe.read_bytes(), media_type=media_type)

    return router
